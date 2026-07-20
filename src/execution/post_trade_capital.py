# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.3 (Post-Trade Capital Lifecycle), §6 (P4 row + co-location decision),
#   §7 (I3 P4->riskguard/P1 commit-before-HTTP no-back-coupling; I4 ingest->P4),
#   §8 Step 2 (split chain-sync READ from exit-SUBMIT), §9 (regression-unconstructable).
"""Zeus P4 post-trade-capital cycle bodies (lifted out of the order daemon).

This module owns the POST_TRADE capital follow-up cycles that were registered in the order
daemon (src.main) and are now hosted by the dedicated P4 process
(com.zeus.post-trade-capital, src/ingest/post_trade_capital_daemon.py):

  - ``chain_sync_read_cycle``        — the chain-truth sync READ phase of the former
                                       ``_chain_sync_and_exit_monitor_cycle`` (src/main.py).
                                       It commits its writes BEFORE returning so it never
                                       holds the trades.db WAL write lock across the
                                       per-position HTTP the order daemon used to run
                                       afterwards (the DATA_DEGRADED-flap root cause, §4.3).
  - ``_harvester_cycle``             — settlement P&L resolver (REDEEM_INTENT_CREATED producer)
  - ``_redeem_reconciler_cycle``     — REDEEM_TX_HASHED -> reconcile_pending_redeems
    (``_redeem_submitter_cycle`` DELETED 2026-07-08, R6-a: dead redeem-submission
    machinery -- Zeus never submits redeem tx, operator law 2026-06-10. Redemption
    accounting stays live via ``request_redeem``/``reconcile_pending_redeems``.)
  - ``_wrap_intent_creator_cycle``   — enqueue WRAP_REQUESTED on balance threshold
  - ``_wrap_submitter_cycle``        — WRAP_REQUESTED/WRAP_APPROVED -> submit APPROVE/WRAP tx
  - ``_wrap_reconciler_cycle``       — WRAP_*_TX_HASHED -> advance on receipt

WHY THIS IS ITS OWN PROCESS (system_decomposition_plan §4.3 / §9):
  - ALWAYS_ON / POST_TRADE (criterion 1): a settled position must be harvested / redeemed /
    wrapped even if trading is paused for weeks. These cycles must keep running when the
    order daemon is idle or dead.
  - FAILURE_DOMAIN (criterion 3): POST_TRADE follow-up must not share the live-decision lane;
    a chain-sync / redeem / wrap fault must not stall the reactor, and a trading bug must not
    blind settlement follow-up.
  - WAL-lock starvation (§4.3, I3): in the order daemon the bundled chain-sync held the
    trades.db write lock across per-position HTTP and starved riskguard.tick() ->
    DATA_DEGRADED flaps that block ALL trades (INV-05). Moving chain-sync to this process
    removes that contention from the trading lane.

THE SPLIT (§8 Step 2): the EXIT-monitoring / exit-SUBMIT phase (``_execute_monitoring_phase``)
of the former bundled function STAYS in the order daemon (src.main) — it posts real sell
orders on RED / force-exit and is order-runtime. Only the chain-sync READ phase moves here.
``chain_sync_read_cycle`` therefore NEVER calls ``_execute_monitoring_phase`` and NEVER posts
a sell order.

INTERFACE I3 (producer P4 -> consumer riskguard/P1, DB-mediated, no in-process back-coupling):
P4 commits chain-sync writes BEFORE any per-position HTTP so it never holds the trades.db WAL
write lock across network calls; P4's pollers are triggered by ``settlement_commands`` /
``wrap_unwrap_commands`` row states, NEVER by the order daemon's trading activity.

INV-37: each cross-DB write below goes through the sanctioned single-DB connection helpers
(``get_trade_connection`` / ``get_world_connection`` / ``get_forecasts_connection``); the
harvester resolver opens a trade conn and a forecasts conn and passes both to the resolver
exactly as it did in the order daemon — the process boundary relocates WHICH process owns the
transaction, it does not relax the ATTACH+SAVEPOINT rule.

These cycle bodies are MOVED VERBATIM from src/main.py (the order daemon registered them with
``@_scheduler_job(...)``; here they are UN-decorated — the P4 daemon applies its own uniform
observability wrapper at registration, mirroring the P2 substrate-observer pattern).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from src.config import get_mode

logger = logging.getLogger("zeus.post_trade_capital")


class CollateralSnapshotDegraded(RuntimeError):
    """The heartbeat completed mechanically but did not obtain authoritative collateral truth."""


class _PusdOnlyCollateralAdapter:
    """Expose only pUSD collateral facts to the sidecar heartbeat.

    The 30s sidecar heartbeat exists to keep entry bankroll proof fresh,
    including pUSD allowance. CTF inventory proof is action-specific sell
    collateral and can require many conditional-token reads, so it must not be
    coupled to the pUSD heartbeat.
    """

    def __init__(self, adapter) -> None:
        self._adapter = adapter

    def get_collateral_payload(self) -> dict:
        pusd_payload = getattr(self._adapter, "get_pusd_collateral_payload", None)
        if callable(pusd_payload):
            # The CLOB ``balance-allowance/update`` endpoint is a cache-refresh
            # hint, not balance authority.  Calling it every 30 seconds can
            # consume the sidecar's whole deadline before the authoritative
            # reads happen.  Read the current CLOB balance directly and prove
            # allowance from chain when CLOB omits/zeros it.
            return dict(
                pusd_payload(
                    refresh_allowance=False,
                    allow_chain_allowance_fallback=True,
                )
                or {}
            )
        return dict(self._adapter.get_collateral_payload() or {})

    @property
    def wallet_address(self) -> str:
        """Funder address identifying this Zeus wallet (wallet_balance_head key).

        LX-T2-a: the head row is keyed by (wallet, asset); the underlying
        v2 adapter already carries funder_address for submission provenance,
        so this is a read-only passthrough, not a new identity source.
        """
        return str(getattr(self._adapter, "funder_address", "") or "")


def _post_trade_collateral_timeout_seconds() -> float:
    raw = os.environ.get("ZEUS_POST_TRADE_COLLATERAL_TIMEOUT_SECONDS")
    if raw in (None, ""):
        # This job creates a cold authenticated CLOB client, so its connect budget must cover
        # DNS/TLS setup. The independent 25s absolute deadline still bounds update+read+fallback.
        return 6.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid ZEUS_POST_TRADE_COLLATERAL_TIMEOUT_SECONDS=%r; using 6.0", raw)
        return 6.0
    if value <= 0:
        logger.warning("Invalid ZEUS_POST_TRADE_COLLATERAL_TIMEOUT_SECONDS=%r; using 6.0", raw)
        return 6.0
    return value


def _post_trade_collateral_deadline_seconds() -> float:
    raw = os.environ.get("ZEUS_POST_TRADE_COLLATERAL_DEADLINE_SECONDS")
    if raw in (None, ""):
        return 25.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid ZEUS_POST_TRADE_COLLATERAL_DEADLINE_SECONDS=%r; using 25.0", raw)
        return 25.0
    if value <= 0:
        logger.warning("Invalid ZEUS_POST_TRADE_COLLATERAL_DEADLINE_SECONDS=%r; using 25.0", raw)
        return 25.0
    return value


def _upsert_pusd_wallet_balance_head(snapshot, wallet_address: str) -> None:
    """Dual-write the sync-owned wallet_balance_head row for pUSD.

    LX-T2-a (docs/rebuild/local_ledger_excision_2026-07-12.md LX-T2 verdict):
    the head row is written from the SAME CollateralSnapshot instance
    ``ledger.refresh()`` just persisted to ``collateral_ledger_snapshots`` —
    this is a second WRITE of already-fetched facts, not a second read.
    Short-lived, separately-committed connection (mirrors CollateralLedger's
    own path-backed connection lifecycle) so this never holds the trade DB
    WAL lock across network I/O — the network read already completed above.
    """
    from src.state.db import get_trade_connection
    from src.state.schema.wallet_balance_head_schema import ensure_table
    from src.state.wallet_balance_head import upsert_wallet_balance_head

    # get_trade_connection is the canonical connection shim (src/state/db.py) —
    # NOT a new raw sqlite3.connect() site (Track A.3 writer-lock antibody).
    conn = get_trade_connection(write_class="bulk")
    try:
        ensure_table(conn)
        upsert_wallet_balance_head(
            conn,
            wallet=wallet_address,
            asset="PUSD",
            balance_micro=snapshot.pusd_balance_micro,
            allowance_micro=snapshot.pusd_allowance_micro,
            # This refresh lane reads pUSD balance/allowance only via the
            # authenticated CLOB balance-allowance endpoint (a chain ERC20
            # read only ever covers the allowance-fallback leg inside that
            # same call, never the balance) -- CHAIN is reserved for a future
            # direct on-chain balance read (e.g. CTF winner balanceOf).
            source="CLOB",
            authority_tier=snapshot.authority_tier,
            block_or_source_ts=snapshot.captured_at.isoformat(),
        )
        conn.commit()
    finally:
        conn.close()


def collateral_snapshot_refresh_cycle() -> None:
    """Refresh pUSD collateral truth for live trading consumers.

    Ownership: post-trade-capital is the wallet/capital sidecar. The live order
    daemon consumes the latest durable collateral_ledger_snapshots row and must
    not perform py-clob-client wallet reads inside the event reactor.

    The periodic heartbeat is deliberately pUSD-only. Full CTF inventory reads
    fan out across every held conditional token and live evidence showed one
    slow token read can keep this scheduler job running past its next cadence,
    aging out bankroll proof and blocking all entries. Sell/exit submission
    still proves the target CTF token on its own submit path.

    LX-T2-a (docs/rebuild/local_ledger_excision_2026-07-12.md): this cycle now
    ALSO upserts the sync-owned ``wallet_balance_head`` row alongside the
    existing ``collateral_ledger_snapshots`` insert (dual-write until LX-3R
    cuts readers over; the snapshot history table is untouched and keeps
    writing exactly as before).
    """

    from src.data.polymarket_client import PolymarketClient
    from src.runtime.timeout_guard import run_with_timeout
    from src.state.collateral_ledger import CollateralLedger
    from src.state.db import _zeus_trade_db_path

    ledger = CollateralLedger(db_path=_zeus_trade_db_path())
    deadline_seconds = _post_trade_collateral_deadline_seconds()

    def _refresh():
        with PolymarketClient(public_http_timeout=_post_trade_collateral_timeout_seconds()) as clob:
            adapter = _PusdOnlyCollateralAdapter(clob._ensure_v2_adapter())
            return ledger.refresh(adapter), adapter.wallet_address

    try:
        snapshot, wallet_address = run_with_timeout(
            _refresh,
            seconds=deadline_seconds,
            label="post_trade_collateral_pusd_refresh",
        )
    except TimeoutError as exc:
        logger.error(
            "collateral_snapshot_refresh: pUSD refresh exceeded %.1fs; preserving sidecar "
            "liveness and leaving the previous collateral snapshot in force/fail-closed: %s",
            deadline_seconds,
            exc,
        )
        raise
    logger.info(
        "collateral_snapshot_refresh: authority=%s captured_at=%s pusd_available_micro=%s ctf_tokens=%d mode=pusd_only",
        snapshot.authority_tier,
        snapshot.captured_at.isoformat(),
        snapshot.available_pusd_micro,
        len(snapshot.ctf_token_balances),
    )
    if wallet_address:
        try:
            _upsert_pusd_wallet_balance_head(snapshot, wallet_address)
        except Exception as exc:  # noqa: BLE001 -- head write must never break the sidecar heartbeat
            logger.error(
                "collateral_snapshot_refresh: wallet_balance_head upsert failed (non-fatal, "
                "collateral_ledger_snapshots history already durable): %s",
                exc,
                exc_info=True,
            )
    else:
        logger.warning(
            "collateral_snapshot_refresh: funder_address unavailable this cycle — skipping "
            "wallet_balance_head upsert (collateral_ledger_snapshots history still written)."
        )
    if snapshot.authority_tier == "DEGRADED":
        # CollateralLedger persists DEGRADED so consumers get typed fail-closed context, but a
        # scheduler cycle that obtained no balance authority is a BUSINESS failure. Raising here
        # makes the daemon wrapper publish FAILED instead of the previous false-green OK status.
        raise CollateralSnapshotDegraded(
            "collateral snapshot refresh returned DEGRADED authority; balance/allowance unknown"
        )


# ---------------------------------------------------------------------------
# Chain-truth sync READ phase (lifted from _chain_sync_and_exit_monitor_cycle).
# §8 Step 2: the chain-sync READ phase moves to P4; the exit-SUBMIT phase STAYS in src.main.
# ---------------------------------------------------------------------------

def chain_sync_read_cycle() -> None:
    """Chain-truth sync READ phase — updates chain_shares / chain_avg_price / chain_state.

    This is the READ half of the former ``_chain_sync_and_exit_monitor_cycle`` (src/main.py).
    It runs ``run_chain_sync`` (one positions-API HTTP call -> DB reconcile writes) and then
    COMMITS the chain-sync writes BEFORE returning, so the trades.db WAL write lock is
    RELEASED and the writes are durable before any further work. There is NO per-position
    monitoring HTTP after it in this process (that lane — ``_execute_monitoring_phase``, which
    posts real sell orders — STAYS in the order daemon, §8 Step 2). So the WAL write lock can
    never be held across a network call here: the lock-across-HTTP starvation that flapped
    riskguard to DATA_DEGRADED (§4.3, I3) is structurally impossible in this process.

    A read or commit failure is logged after cleanup and re-raised so the
    killable child reports FAILED; the parent daemon stays alive for retry.
    The pre-split interim-commit invariant (commit chain-sync writes before Phase-2 HTTP,
    src/main.py:7233 / the riskguard-flaps fix) is preserved here as a same-process invariant:
    commit immediately after the reconcile writes, then return.

    INV-37: ``get_connection`` opens the sanctioned trade+world (+forecasts RO ATTACH)
    connection via ``connect_or_degrade`` — the same path the order daemon used; the
    cross-DB ATTACH is not relaxed.
    """
    # Lazy imports (mirror src/main.py:_chain_sync_and_exit_monitor_cycle). The chain-sync
    # READ helpers live in the order-runtime cycle_runner; we import ONLY the read-phase
    # entry points (run_chain_sync + connection/portfolio helpers) and NEVER the monitoring
    # phase. Lazy so importing this module does not eagerly drag the trading lane into the
    # P4 process at import time (it is pulled only when the chain-sync job actually fires).
    from src.data.polymarket_client import PolymarketClient
    from src.engine.cycle_runner import (
        _run_chain_sync,
        get_connection,
        load_portfolio,
        save_portfolio,
    )

    conn = get_connection()
    if conn is None:
        raise RuntimeError("chain_sync_read: DB write-lock degraded before cycle")

    summary: dict = {}
    failure: Exception | None = None
    try:
        portfolio = load_portfolio()
        with PolymarketClient() as clob:
            # chain-truth sync — updates chain_shares / chain_avg_price / chain_state.
            # Degrades gracefully if Keychain funder_address is absent (REST call fails -> caught).
            try:
                chain_stats, _ = _run_chain_sync(portfolio, clob, conn)
                if chain_stats:
                    summary["chain_sync"] = chain_stats
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "chain_sync_read: chain sync failed: %s", exc, exc_info=True
                )
                summary["chain_sync_error"] = str(exc)
                failure = exc

            # WAL WRITE-LOCK RELEASE (2026-06-08 riskguard-flaps structural fix, now §8 Step 2):
            # the chain-sync reconcile opened an implicit DEFERRED txn on the first DML
            # (chain_shares / chain_state updates) which upgrades to the exclusive WAL write
            # lock on zeus_trades.db. Commit HERE so the WAL write lock is released and the
            # writes are durable before the cycle returns. In the order daemon this commit
            # sat BETWEEN the two phases (before Phase-2 HTTP); in P4 there is no Phase-2, so
            # this commit is the cycle's final write and the lock is released on return.
            try:
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chain_sync_read: chain-sync commit failed: %s", exc
                )
                failure = exc
    except Exception as exc:  # noqa: BLE001
        logger.error("chain_sync_read: unexpected error: %s", exc, exc_info=True)
        failure = exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if failure is not None:
        raise RuntimeError("chain_sync_read cycle failed") from failure

    # status_summary.json is owned by the live trading daemon. This sidecar lacks the
    # process-local heartbeat/risk/collateral singletons required to compute execution
    # capability, so writing a pulse here would overwrite the daemon's true gate state
    # with false UNCONFIGURED blockers. Chain-sync liveness is reported through
    # scheduler_jobs_health.json instead.


# ---------------------------------------------------------------------------
# Harvester resolver (lifted verbatim from src/main.py:_harvester_cycle).
# ---------------------------------------------------------------------------

def _harvester_cycle():
    """Phase 1.5 harvester split: trading-side P&L resolver.

    Reads forecasts.settlements (written by ingest-side harvester_truth_writer)
    and settles positions + writes decision_log. If the resolver is unavailable,
    fail closed; the trading daemon must not fall back to the legacy integrated
    harvester path, which can derive and write settlement truth in the same lane.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.state.db import get_trade_connection, get_forecasts_connection
    with acquire_lock("harvester_pnl") as acquired:
        if not acquired:
            logger.info("harvester_pnl_resolver skipped_lock_held")
            return
        try:
            from src.execution.harvester_pnl_resolver import resolve_pnl_for_settled_markets
            # v4 plan §AX3: harvester PnL resolver = LIVE class.
            # K1 (2026-05-11): settlements -> zeus-forecasts.db; pass forecasts conn.
            trade_conn = get_trade_connection(write_class="live")
            forecasts_conn = get_forecasts_connection(write_class="live")
            try:
                result = resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)
            finally:
                trade_conn.close()
                forecasts_conn.close()
        except ImportError as exc:
            logger.error(
                "harvester_pnl_resolver unavailable; refusing legacy run_harvester fallback: %s",
                exc,
            )
            result = {
                "status": "resolver_unavailable_fail_closed",
                "positions_settled": 0,
                "decision_log_rows_written": 0,
                "errors": 1,
            }
    logger.info("Harvester: %s", result)
    errors = int(result.get("errors") or 0) if isinstance(result, dict) else 1
    status = str(result.get("status") or "") if isinstance(result, dict) else ""
    if errors > 0 or status in {
        "resolver_unavailable_fail_closed",
        "settlement_outcomes_read_error",
    }:
        raise RuntimeError(f"HARVESTER_PNL_RESOLVER_FAILED:{result}")


# ---------------------------------------------------------------------------
# F14 + F16 cascade-liveness pollers (lifted verbatim from src/main.py).
# Per architecture/cascade_liveness_contract.yaml: each state-machine table with
# *_INTENT_CREATED / *_REQUESTED rows MUST have a registered scheduler poller. After the
# P4 lift these pollers are registered in the P4 daemon (not the order daemon) and the
# cascade-liveness boot guard travels with them (post_trade_capital_daemon.py).
# ---------------------------------------------------------------------------

# _redeem_submitter_cycle DELETED 2026-07-08 (R6-a): dead redeem-submission
# scheduler machinery. It already unconditionally calm-skipped every cycle
# (redeem_submission_allowed() is always False per operator law 2026-06-10)
# -- the ~150 lines below the skip-and-return, including the submit_redeem
# call and the autoretry reseat call, were unreachable. See
# src.execution.settlement_commands.assert_redeem_submission_allowed for
# the permanent enforcement point. Scheduler registration removed from
# src/ingest/post_trade_capital_daemon.py in the same commit.


def _redeem_reconciler_cycle() -> None:
    """Poll REDEEM_TX_HASHED rows + reconcile_pending_redeems against web3.

    PR-I.5 completion (2026-05-19): wires Web3 HTTPProvider + calls
    reconcile_pending_redeems so the antibody guard merged in PR #192 is
    reachable in production.  Karachi anchor: tx 0x0c85d94… (negRisk market
    c8c220f5…) sitting in REDEEM_TX_HASHED since 2026-05-19T08:26 UTC.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.execution.settlement_commands import (
        SettlementState,
        list_commands,
        reconcile_pending_redeems,
    )
    from src.state.db import get_trade_connection
    from src.venue.polymarket_v2_adapter import DEFAULT_POLYGON_RPC_URL

    if get_mode() != "live":
        logger.info("redeem_reconciler skipped_non_live mode=%s", get_mode())
        return

    with acquire_lock("redeem_reconciler") as acquired:
        if not acquired:
            logger.info("redeem_reconciler skipped_lock_held")
            return
        conn = get_trade_connection(write_class="live")
        try:
            rows = list_commands(conn, state=SettlementState.REDEEM_TX_HASHED)
            if not rows:
                logger.info("redeem_reconciler: results=0")
                return
            try:
                from web3 import Web3
            except ImportError:
                logger.info(
                    "redeem_reconciler: web3 not installed; rows=%d sitting in "
                    "TX_HASHED (expected pre-PR-I.5)", len(rows),
                )
                return
            polygon_rpc_url = os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
            w3 = Web3(Web3.HTTPProvider(polygon_rpc_url, request_kwargs={"timeout": 15}))
            try:
                results = reconcile_pending_redeems(w3, conn)
                conn.commit()
                logger.info(
                    "redeem_reconciler: reconciled=%d states=%s",
                    len(results), [r.state.value for r in results],
                )
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.error("redeem_reconciler: error=%s", exc)
                raise
        finally:
            conn.close()


def _wrap_intent_creator_cycle() -> None:
    """Enqueue WRAP_REQUESTED if Safe USDC.e balance > threshold and no pending row.

    On-chain balance-driven (not journal-driven). Idempotent: skips if any
    non-terminal WRAP row already exists. Skipped in non-live mode.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.polymarket_client import resolve_polymarket_credentials
    from src.execution.wrap_unwrap_commands import enqueue_wrap_if_balance_above_threshold
    from src.state.db import get_world_connection
    from src.venue.polymarket_v2_adapter import DEFAULT_POLYGON_RPC_URL

    if get_mode() != "live":
        logger.info("wrap_intent_creator skipped_non_live mode=%s", get_mode())
        return

    with acquire_lock("wrap_intent_creator") as acquired:
        if not acquired:
            logger.info("wrap_intent_creator skipped_lock_held")
            return
        try:
            from web3 import Web3
        except ImportError:
            logger.info("wrap_intent_creator: web3 not installed; skipping")
            return
        # Resolve Safe address from the same Keychain-backed credential source
        # used by wrap_submitter and wrap_reconciler so all three cycles agree
        # on which Safe's balance to monitor and which Safe to transact against.
        try:
            creds = resolve_polymarket_credentials()
        except RuntimeError as exc:
            logger.warning("wrap_intent_creator: credentials unavailable, skipping: %s", exc)
            return
        safe_address = creds["funder_address"]
        if not safe_address:
            logger.warning("wrap_intent_creator: funder_address empty in credentials")
            return
        polygon_rpc_url = os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
        w3 = Web3(Web3.HTTPProvider(polygon_rpc_url, request_kwargs={"timeout": 15}))
        conn = get_world_connection()
        try:
            command_id = enqueue_wrap_if_balance_above_threshold(
                safe_address, w3, conn,
            )
            if command_id:
                conn.commit()
                logger.info("wrap_intent_creator: enqueued command_id=%s", command_id)
            else:
                logger.debug("wrap_intent_creator: no wrap needed (threshold or pending)")
        finally:
            conn.close()


def _wrap_submitter_cycle() -> None:
    """Submit APPROVE tx for WRAP_REQUESTED rows; WRAP tx for WRAP_APPROVED rows.

    Each step is a separate Safe execTransaction. Skipped in non-live mode.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.polymarket_client import (
        resolve_polymarket_credentials,
        _resolve_clob_v2_signature_type,
        _resolve_q1_egress_evidence_path,
    )
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        fail_wrap,
        list_pending_wrap_commands,
        mark_wrap_approve_tx_hashed,
        mark_wrap_tx_hashed,
    )
    from src.state.db import get_world_connection
    from src.venue.polymarket_v2_adapter import (
        DEFAULT_Q1_EGRESS_EVIDENCE,
        DEFAULT_POLYGON_RPC_URL,
        DEFAULT_V2_HOST,
        PolymarketV2Adapter,
        Q1_EGRESS_EVIDENCE_ENV,
    )

    if get_mode() != "live":
        logger.info("wrap_submitter skipped_non_live mode=%s", get_mode())
        return

    with acquire_lock("wrap_submitter") as acquired:
        if not acquired:
            logger.info("wrap_submitter skipped_lock_held")
            return
        conn = get_world_connection()
        try:
            rows = list_pending_wrap_commands(conn)
            actionable = [
                r for r in rows
                if r["state"] in (
                    WrapUnwrapState.WRAP_REQUESTED.value,
                    WrapUnwrapState.WRAP_APPROVED.value,
                )
            ]
            if not actionable:
                logger.debug("wrap_submitter: no actionable rows")
                return
            try:
                creds = resolve_polymarket_credentials()
            except RuntimeError as exc:
                raise RuntimeError(
                    f"wrap_submitter: credentials unavailable (fail-closed): {exc}"
                ) from exc
            q1_egress_evidence = _resolve_q1_egress_evidence_path(
                default=DEFAULT_Q1_EGRESS_EVIDENCE, env_name=Q1_EGRESS_EVIDENCE_ENV,
            )
            adapter = PolymarketV2Adapter(
                host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
                funder_address=creds["funder_address"],
                signer_key=creds["private_key"],
                chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                signature_type=_resolve_clob_v2_signature_type(),
                polygon_rpc_url=os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL),
                api_creds=creds.get("api_creds"),
                q1_egress_evidence_path=q1_egress_evidence,
            )
            # Derive signer EOA from private_key (same as redeem flow).
            # creds["funder_address"] is the Safe proxy address, NOT an owner EOA.
            # _wrap_via_safe validates signer_eoa against Safe.getOwners(), so
            # passing funder_address would always fail with WRAP_SAFE_OWNER_MISMATCH.
            from eth_account import Account as _Account  # type: ignore[import]
            signer_eoa = _Account.from_key(creds["private_key"]).address
            submitted = 0
            failed = 0
            for row in actionable:
                command_id = row["command_id"]
                amount_micro = row["amount_micro"]
                current_state = row["state"]
                tx_kind = "APPROVE" if current_state == WrapUnwrapState.WRAP_REQUESTED.value else "WRAP"
                try:
                    result = adapter._wrap_via_safe(
                        safe_address=creds["funder_address"],
                        amount_micro=amount_micro,
                        tx_kind=tx_kind,
                        signer_eoa=signer_eoa,
                    )
                    if result.get("errorCode") == "WRAP_DRY_RUN_LOGGED":
                        logger.info(
                            "wrap_submitter: dry_run command_id=%s tx_kind=%s fingerprint=%s",
                            command_id, tx_kind, result.get("dry_run_fingerprint"),
                        )
                        continue
                    if not result.get("success"):
                        raise RuntimeError(
                            f"_wrap_via_safe failed: {result.get('errorCode')} "
                            f"{result.get('errorMessage')}"
                        )
                    tx_hash = result["tx_hash"]
                    if tx_kind == "APPROVE":
                        mark_wrap_approve_tx_hashed(
                            command_id, tx_hash, conn=conn,
                        )
                    else:
                        mark_wrap_tx_hashed(command_id, tx_hash, conn=conn)
                    conn.commit()
                    submitted += 1
                    logger.info(
                        "wrap_submitter: command_id=%s tx_kind=%s tx_hash=%s",
                        command_id, tx_kind, tx_hash,
                    )
                except Exception as exc:  # noqa: BLE001
                    try:
                        conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    failed += 1
                    logger.error(
                        "wrap_submitter: command_id=%s tx_kind=%s error=%s",
                        command_id, tx_kind, exc,
                    )
                    try:
                        fail_wrap(
                            command_id,
                            error_payload={"error": str(exc), "tx_kind": tx_kind},
                            conn=conn,
                        )
                        conn.commit()
                    except Exception:  # noqa: BLE001
                        pass
            logger.info("wrap_submitter: submitted=%d failed=%d", submitted, failed)
            if failed:
                raise RuntimeError(f"wrap_submitter: submitted={submitted} failed={failed}")
        finally:
            conn.close()


def _wrap_reconciler_cycle() -> None:
    """Poll WRAP_APPROVE_TX_HASHED and WRAP_TX_HASHED rows; advance state on receipt.

    On WRAP_CONFIRMED, calls adapter.update_balance_allowance() to refresh CLOB ledger.
    Skipped in non-live mode.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.polymarket_client import (
        resolve_polymarket_credentials,
        _resolve_clob_v2_signature_type,
        _resolve_q1_egress_evidence_path,
    )
    from src.execution.wrap_unwrap_commands import (
        WrapUnwrapState,
        init_wrap_unwrap_schema,
        reconcile_pending_wraps,
    )
    from src.state.db import get_world_connection
    from src.venue.polymarket_v2_adapter import (
        DEFAULT_Q1_EGRESS_EVIDENCE,
        DEFAULT_POLYGON_RPC_URL,
        DEFAULT_V2_HOST,
        PolymarketV2Adapter,
        Q1_EGRESS_EVIDENCE_ENV,
    )

    if get_mode() != "live":
        logger.info("wrap_reconciler skipped_non_live mode=%s", get_mode())
        return

    with acquire_lock("wrap_reconciler") as acquired:
        if not acquired:
            logger.info("wrap_reconciler skipped_lock_held")
            return
        try:
            from web3 import Web3
        except ImportError:
            logger.info("wrap_reconciler: web3 not installed; skipping")
            return
        polygon_rpc_url = os.environ.get("POLYGON_RPC_URL", DEFAULT_POLYGON_RPC_URL)
        w3 = Web3(Web3.HTTPProvider(polygon_rpc_url, request_kwargs={"timeout": 15}))
        conn = get_world_connection()
        try:
            init_wrap_unwrap_schema(conn)
            reconcile_states = (
                WrapUnwrapState.WRAP_APPROVE_TX_HASHED.value,
                WrapUnwrapState.WRAP_TX_HASHED.value,
            )
            rows = conn.execute(
                "SELECT command_id FROM wrap_unwrap_commands WHERE state IN (?,?)",
                reconcile_states,
            ).fetchall()
            if not rows:
                logger.debug("wrap_reconciler: no rows to reconcile")
                return
            try:
                creds = resolve_polymarket_credentials()
            except RuntimeError as exc:
                raise RuntimeError(
                    f"wrap_reconciler: credentials unavailable (fail-closed): {exc}"
                ) from exc
            q1_egress_evidence = _resolve_q1_egress_evidence_path(
                default=DEFAULT_Q1_EGRESS_EVIDENCE, env_name=Q1_EGRESS_EVIDENCE_ENV,
            )
            adapter = PolymarketV2Adapter(
                host=os.environ.get("POLYMARKET_CLOB_V2_HOST", DEFAULT_V2_HOST),
                funder_address=creds["funder_address"],
                signer_key=creds["private_key"],
                chain_id=int(os.environ.get("POLYMARKET_CHAIN_ID", "137")),
                signature_type=_resolve_clob_v2_signature_type(),
                polygon_rpc_url=polygon_rpc_url,
                api_creds=creds.get("api_creds"),
                q1_egress_evidence_path=q1_egress_evidence,
            )
            try:
                results = reconcile_pending_wraps(w3, adapter, conn)
                conn.commit()
                logger.info(
                    "wrap_reconciler: reconciled=%d states=%s",
                    len(results), [r.get("state") for r in results],
                )
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                logger.error("wrap_reconciler: error=%s", exc)
                raise
        finally:
            conn.close()
