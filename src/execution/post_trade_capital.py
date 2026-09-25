# Created: 2026-06-08
# Last reused or audited: 2026-09-25
# Authority basis: docs/reference/design_system_decomposition_plan.md
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
  - ``_harvester_cycle``             — settlement P&L resolver (on-chain redemption
                                       decoupled entirely 2026-07-25; Polymarket settles
                                       win/loss on Zeus's behalf)
  - ``run_tier0_candidate_settlement_fold`` — exact VERIFIED settlement labels for
                                       prospective Tier-0 selection evidence
  - ``_wrap_intent_creator_cycle``   — enqueue WRAP_REQUESTED on balance threshold
  - ``_wrap_submitter_cycle``        — WRAP_REQUESTED/WRAP_APPROVED -> submit APPROVE/WRAP tx
  - ``_wrap_reconciler_cycle``       — WRAP_*_TX_HASHED -> advance on receipt

WHY THIS IS ITS OWN PROCESS (system_decomposition_plan §4.3 / §9):
  - ALWAYS_ON / POST_TRADE (criterion 1): a settled position must be harvested /
    externally redeemed /
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

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from src.config import get_mode

logger = logging.getLogger("zeus.post_trade_capital")

_TIER0_CANDIDATE_QUERY_CHUNK = 400
_TIER0_LABEL_WRITE_CHUNK = 200


def _load_tier0_candidate_rows(
    trade_conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Read immutable Tier-0 decisions whose final binary label is derived."""

    return [
        dict(row)
        for row in trade_conn.execute(
            """
            SELECT row_id, market_key, city, target_date, side, settled_y
              FROM tier0_candidate_set_provenance
             ORDER BY row_id
            """
        ).fetchall()
    ]


def _tier0_candidate_settlement_labels(
    forecast_conn: sqlite3.Connection,
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[int, int]], dict[str, int]]:
    """Grade candidate sides from VERIFIED settlement truth and market bounds.

    ``market_key`` is the decision-time condition id. The canonical forecast
    DB maps it to exact finite/open bin bounds; the family settlement supplies
    the verified value and unit. No label punctuation or date inference is
    used. Conflicting truth for one condition is excluded rather than guessed.
    """

    from src.config import runtime_cities_by_name
    from src.contracts.exceptions import SettlementPrecisionError
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.types.market import Bin

    market_keys = tuple(
        sorted(
            {
                str(candidate.get("market_key") or "").strip()
                for candidate in candidates
                if str(candidate.get("market_key") or "").strip()
            }
        )
    )
    truth_by_key: dict[tuple[str, str, str], int] = {}
    ambiguous_keys: set[tuple[str, str, str]] = set()
    invalid_truth_rows = 0
    cities = runtime_cities_by_name()

    for offset in range(0, len(market_keys), _TIER0_CANDIDATE_QUERY_CHUNK):
        chunk = market_keys[offset : offset + _TIER0_CANDIDATE_QUERY_CHUNK]
        for row in forecast_conn.execute(
            """
            SELECT me.condition_id, me.city, me.target_date,
                   me.temperature_metric, me.range_low, me.range_high,
                   so.settlement_value, so.settlement_unit
              FROM market_events me
              JOIN settlement_outcomes so
                ON so.city = me.city
               AND so.target_date = me.target_date
               AND so.temperature_metric = me.temperature_metric
             WHERE so.authority = 'VERIFIED'
               AND me.condition_id IN (SELECT value FROM json_each(?))
            """,
            (json.dumps(chunk),),
        ).fetchall():
            condition_id = str(row["condition_id"] or "").strip()
            city = str(row["city"] or "").strip()
            target_date = str(row["target_date"] or "").strip()
            key = (condition_id, city, target_date)
            try:
                unit = str(row["settlement_unit"] or "").strip().upper()
                city_contract = cities.get(city)
                if city_contract is None:
                    raise ValueError("settlement city contract is unavailable")
                semantics = SettlementSemantics.for_city(city_contract)
                if unit != semantics.measurement_unit:
                    raise ValueError("settlement unit disagrees with city contract")
                value = semantics.assert_settlement_value(
                    float(row["settlement_value"]),
                    context="tier0_candidate_settlement_fold",
                )
                bin_obj = Bin(
                    low=(
                        None
                        if row["range_low"] is None
                        else float(row["range_low"])
                    ),
                    high=(
                        None
                        if row["range_high"] is None
                        else float(row["range_high"])
                    ),
                    unit=unit,
                )
                yes_won = int(bin_obj.contains(value))
            except (SettlementPrecisionError, TypeError, ValueError):
                invalid_truth_rows += 1
                continue
            prior = truth_by_key.get(key)
            if prior is not None and prior != yes_won:
                ambiguous_keys.add(key)
                continue
            truth_by_key[key] = yes_won

    for key in ambiguous_keys:
        truth_by_key.pop(key, None)

    labels: list[tuple[int, int]] = []
    invalid_candidate_rows = 0
    for candidate in candidates:
        key = (
            str(candidate.get("market_key") or "").strip(),
            str(candidate.get("city") or "").strip(),
            str(candidate.get("target_date") or "").strip(),
        )
        yes_won = truth_by_key.get(key)
        if yes_won is None:
            continue
        side = str(candidate.get("side") or "").strip().upper()
        if side not in {"YES", "NO"}:
            invalid_candidate_rows += 1
            continue
        labels.append(
            (int(candidate["row_id"]), yes_won if side == "YES" else 1 - yes_won)
        )

    return labels, {
        "candidate_rows": len(candidates),
        "verified_market_labels": len(truth_by_key),
        "labels_ready": len(labels),
        "pending_rows": len(candidates) - len(labels),
        "ambiguous_markets": len(ambiguous_keys),
        "invalid_truth_rows": invalid_truth_rows,
        "invalid_candidate_rows": invalid_candidate_rows,
    }


def _tier0_candidate_label_changes(
    candidates: Sequence[Mapping[str, Any]],
    labels: Sequence[tuple[int, int]],
) -> list[tuple[int, int | None, int]]:
    """Return ``(row_id, prior, label)`` for labels that differ from the snapshot.

    ``candidates`` is the read-only snapshot the labels were derived from, so
    every label row_id is present in it.
    """

    prior_by_row = {
        int(candidate["row_id"]): (
            None
            if candidate.get("settled_y") is None
            else int(candidate["settled_y"])
        )
        for candidate in candidates
    }
    return [
        (row_id, prior_by_row[row_id], settled_y)
        for row_id, settled_y in labels
        if prior_by_row[row_id] != settled_y
    ]


def _apply_tier0_candidate_label_changes(
    changes: Sequence[tuple[int, int | None, int]],
) -> dict[str, int]:
    """Compare-and-set changed labels in short coordinated write transactions.

    A row whose ``settled_y`` moved since the read-only snapshot is left alone
    and counted ``cas_lost``; the next tick re-diffs it against current truth.
    """

    from src.state.db import connect_existing_trade_db_without_journal_bootstrap
    from src.state.write_coordinator import (
        DBIdentity,
        WritePriority,
        default_runtime_write_coordinator,
    )

    filled = corrected = cas_lost = 0
    coordinator = default_runtime_write_coordinator()
    for offset in range(0, len(changes), _TIER0_LABEL_WRITE_CHUNK):
        # STANDARD priority / deadline_ms=1_500 / max_hold_ms=500 mirror
        # chain_sync_read's coordinated TRADE write in this module.
        with coordinator.transaction(
            (DBIdentity.TRADE,),
            owner="tier0_candidate_settlement_fold",
            write_class="live",
            priority=WritePriority.STANDARD,
            deadline_ms=1_500,
            max_hold_ms=500,
            connection_factory=connect_existing_trade_db_without_journal_bootstrap,
        ) as tx:
            for row_id, prior, settled_y in changes[
                offset : offset + _TIER0_LABEL_WRITE_CHUNK
            ]:
                if not tx.connection.execute(
                    """
                    UPDATE tier0_candidate_set_provenance
                       SET settled_y = ?
                     WHERE row_id = ? AND settled_y IS ?
                    """,
                    (settled_y, row_id, prior),
                ).rowcount:
                    cas_lost += 1
                elif prior is None:
                    filled += 1
                else:
                    corrected += 1
    return {"filled": filled, "corrected": corrected, "cas_lost": cas_lost}


def run_tier0_candidate_settlement_fold() -> dict[str, int]:
    """Read-only diff, then compare-and-set writes of changed rows only.

    No transaction spans two DBs, and no read runs inside a write transaction:
    an unchanged fold never takes the trade-DB write lock.

    SCOPE: only ``tier0_candidate_set_provenance.settled_y`` rows whose exact
    condition has a VERIFIED canonical settlement and whose cached label
    differs from it. DRAIN: the post-trade five-minute job re-diffs every
    candidate row and writes the changes in coordinated transactions of at
    most ``_TIER0_LABEL_WRITE_CHUNK`` rows; a lost lease or lost CAS retries on
    the next tick. RESET: a later canonical correction deterministically
    replaces the derived label on the next tick.
    """

    from src.state.db import (
        get_forecasts_connection_read_only,
        get_trade_connection_read_only,
    )

    trade_read = get_trade_connection_read_only()
    try:
        candidates = _load_tier0_candidate_rows(trade_read)
    finally:
        trade_read.close()
    if not candidates:
        return {
            "candidate_rows": 0,
            "verified_market_labels": 0,
            "labels_ready": 0,
            "pending_rows": 0,
            "ambiguous_markets": 0,
            "invalid_truth_rows": 0,
            "invalid_candidate_rows": 0,
            "unchanged": 0,
            "filled": 0,
            "corrected": 0,
            "cas_lost": 0,
        }

    forecast_read = get_forecasts_connection_read_only()
    try:
        labels, stats = _tier0_candidate_settlement_labels(
            forecast_read,
            candidates,
        )
    finally:
        forecast_read.close()
    changes = _tier0_candidate_label_changes(candidates, labels)
    return {
        **stats,
        "unchanged": len(labels) - len(changes),
        **_apply_tier0_candidate_label_changes(changes),
    }


class CollateralSnapshotDegraded(RuntimeError):
    """The heartbeat completed mechanically but did not obtain authoritative collateral truth."""


class _CapturedCollateralAdapter:
    """Replay one completed network read into CollateralLedger on the caller thread."""

    def __init__(self, payload: dict | None, error: Exception | None) -> None:
        self._payload = payload
        self._error = error

    def get_collateral_payload(self) -> dict:
        if self._error is not None:
            raise self._error
        return dict(self._payload or {})


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
        chain_payload = getattr(self._adapter, "get_chain_pusd_collateral_payload", None)
        if callable(chain_payload):
            try:
                payload = dict(chain_payload() or {})
                if (
                    payload.get("pusd_balance_micro") is None
                    or payload.get("pusd_allowance_micro") is None
                    or payload.get("authority_tier") != "CHAIN"
                    or payload.get("pusd_balance_source") != "CHAIN"
                ):
                    raise ValueError("incomplete chain pUSD collateral payload")
                return payload
            except Exception as exc:  # noqa: BLE001 - bounded CLOB fallback remains fail-closed
                logger.warning(
                    "chain pUSD collateral batch unavailable; trying authenticated CLOB "
                    "balance with chain allowance fallback: %s",
                    exc,
                )
        pusd_payload = getattr(self._adapter, "get_pusd_collateral_payload", None)
        if callable(pusd_payload):
            # The CLOB ``balance-allowance/update`` endpoint is a cache-refresh
            # hint, not balance authority.  Calling it every 30 seconds can
            # consume the sidecar's whole deadline before the authoritative
            # reads happen.  Read the current CLOB balance directly and prove
            # allowance from chain when CLOB omits/zeros it.
            payload = dict(
                pusd_payload(
                    refresh_allowance=False,
                    allow_chain_allowance_fallback=True,
                )
                or {}
            )
        else:
            payload = dict(self._adapter.get_collateral_payload() or {})
        # This fallback's balance fact is always the authenticated CLOB cache.
        # A chain allowance may strengthen that one field, but it cannot promote
        # the composite snapshot or wallet head to chain balance authority.
        payload["pusd_balance_source"] = "CLOB"
        if payload.get("authority_tier") == "CHAIN":
            payload["authority_tier"] = "VENUE"
        return payload

    @property
    def wallet_address(self) -> str:
        """Funder address identifying this Zeus wallet (wallet_balance_head key).

        LX-T2-a: the head row is keyed by (wallet, asset); the underlying
        v2 adapter already carries funder_address for submission provenance,
        so this is a read-only passthrough, not a new identity source.
        """
        return str(getattr(self._adapter, "funder_address", "") or "")


# R-S (2026-09-13): fixed buffer the wrapper deadline carries above the venue
# socket-timeout floor, covering PolymarketClient construction + v2 adapter
# resolution -- overhead that runs BEFORE the socket timeout's own clock
# starts (see _post_trade_collateral_deadline_seconds). Not itself tunable by
# env var: the socket floor already is, and the deadline derives from it.
_COLLATERAL_DEADLINE_MARGIN_SECONDS = 3.0


def _post_trade_collateral_timeout_seconds() -> float:
    raw = os.environ.get("ZEUS_POST_TRADE_COLLATERAL_TIMEOUT_SECONDS")
    if raw in (None, ""):
        # The isolated heartbeat is always a cold process. Live VPN evidence on
        # 2026-07-28 put one successful TLS+batch chain read near 15s; 20s covers
        # that path while the independent 25s absolute deadline still fails closed.
        return 20.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid ZEUS_POST_TRADE_COLLATERAL_TIMEOUT_SECONDS=%r; using 20.0", raw)
        return 20.0
    if value <= 0:
        logger.warning("Invalid ZEUS_POST_TRADE_COLLATERAL_TIMEOUT_SECONDS=%r; using 20.0", raw)
        return 20.0
    return value


def _post_trade_collateral_deadline_seconds() -> float:
    # R-S (2026-09-13, fix-first on T-collateral's own follow-up): the wrapper
    # deadline must carry a real margin ABOVE the venue socket-timeout floor it
    # wraps, not equal it. run_with_timeout(_read, seconds=deadline) starts its
    # clock at submit() time -- strictly BEFORE _read() constructs
    # PolymarketClient and resolves the v2 adapter, i.e. before the socket
    # timeout's own clock even starts. A read that legitimately runs close to
    # its own socket timeout (T-collateral: live reads observed "near 15s"
    # against a 20s floor; construction overhead grows under exactly the same
    # daemon-wide scheduler pressure that caused the original incident) can
    # then lose the race to the wrapper BEFORE _read()'s own try/except gets a
    # chance to catch the socket timeout and return cleanly. Losing that race
    # turns a soft outcome (ledger.refresh() still persists a fresh DEGRADED
    # snapshot -- freshness is preserved even on a failed read) into a hard
    # TimeoutError with NO snapshot write at all, reintroducing the exact
    # staleness (CURRENT_WEALTH_COLLATERAL_EXPIRED) this fix exists to
    # prevent. Deriving the default from the socket-timeout floor (one
    # expression, no second literal) keeps the two from drifting apart if the
    # socket floor is ever retuned independently.
    default = (
        _post_trade_collateral_timeout_seconds()
        + _COLLATERAL_DEADLINE_MARGIN_SECONDS
    )
    raw = os.environ.get("ZEUS_POST_TRADE_COLLATERAL_DEADLINE_SECONDS")
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid ZEUS_POST_TRADE_COLLATERAL_DEADLINE_SECONDS=%r; using %.1f", raw, default
        )
        return default
    if value <= 0:
        logger.warning(
            "Invalid ZEUS_POST_TRADE_COLLATERAL_DEADLINE_SECONDS=%r; using %.1f", raw, default
        )
        return default
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
    # disable_wal_autocheckpoint=True (T-collateral, 2026-09-12): this write
    # commits every 30s from a killable one-shot child; leaving SQLite's
    # default per-connection autocheckpoint enabled meant an unlucky commit
    # right after a pinned external reader released could pay for draining
    # the entire un-checkpointed WAL backlog synchronously (9.9GB observed
    # live), freezing every sibling child on the same DB. The daemon's own
    # dedicated trades_wal_checkpoint job (post_trade_capital_daemon.py) now
    # owns that drain on a short, predictable interval instead.
    conn = get_trade_connection(write_class="bulk", disable_wal_autocheckpoint=True)
    try:
        ensure_table(conn)
        upsert_wallet_balance_head(
            conn,
            wallet=wallet_address,
            asset="PUSD",
            balance_micro=snapshot.pusd_balance_micro,
            allowance_micro=snapshot.pusd_allowance_micro,
            source="CHAIN" if snapshot.authority_tier == "CHAIN" else "CLOB",
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

    # Daemon pre-flight/migrations own schema readiness. Re-running idempotent
    # DDL on every 30-second refresh still needs the global SQLite writer and
    # can starve the very snapshot append this job exists to publish.
    ledger = CollateralLedger(
        db_path=_zeus_trade_db_path(),
        initialize_schema=False,
        # T-collateral (2026-09-12): see the matching comment on the
        # wallet_balance_head connection below -- this cycle's own
        # ledger.refresh() write is the other half of the same write path.
        disable_wal_autocheckpoint=True,
    )
    deadline_seconds = _post_trade_collateral_deadline_seconds()

    def _read():
        with PolymarketClient(public_http_timeout=_post_trade_collateral_timeout_seconds()) as clob:
            adapter = _PusdOnlyCollateralAdapter(clob._ensure_v2_adapter())
            try:
                payload = adapter.get_collateral_payload()
                error = None
            except Exception as exc:  # noqa: BLE001 - replayed through ledger fail-closed logic
                payload = None
                error = exc
            return payload, error, adapter.wallet_address

    try:
        payload, read_error, wallet_address = run_with_timeout(
            _read,
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
    # The timeout worker owns network reads only. Persist on this caller thread
    # after bounded completion, so a timed-out worker can never advance freshness.
    snapshot = ledger.refresh(_CapturedCollateralAdapter(payload, read_error))
    logger.info(
        "collateral_snapshot_refresh: authority=%s captured_at=%s pusd_available_micro=%s ctf_tokens=%d mode=pusd_only",
        snapshot.authority_tier,
        snapshot.captured_at.isoformat(),
        snapshot.available_pusd_micro,
        len(snapshot.ctf_token_balances),
    )
    # This is a delivery hint, never collateral authority.  The order daemon
    # reloads the same durable ``captured_at`` snapshot before restoring its
    # process-local allocator. CHAIN/VENUE are accepted by bankroll_provider;
    # DEGRADED wakes immediately revoke authority and are then acknowledged.
    if snapshot.authority_tier in {"CHAIN", "VENUE", "DEGRADED"}:
        try:
            from src.runtime.reactor_wake import (
                COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
                publish_reactor_wake,
            )

            publish_reactor_wake(
                source="post_trade_capital",
                reason=COLLATERAL_AUTHORITY_REFRESHED_WAKE_REASON,
                published_at=snapshot.captured_at,
            )
        except Exception:  # noqa: BLE001 - the periodic warm remains a recovery backstop
            logger.exception(
                "collateral_snapshot_refresh: canonical snapshot committed but allocator "
                "refresh wake publish failed"
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

    T-collateral-busy (2026-09-13): the reconcile DML and its commit run inside the
    TRADE write coordinator's admission lease, so this connection's ordinary commit
    is no longer invisible to ``collateral_snapshot_persist``'s zero-retry coordinator
    BEGIN (write_coordinator.py:~816-822). ``lease()`` (not ``transaction()``) is used
    so the connection above — carrying the sanctioned trade+world ATTACH — stays open
    for the whole cycle instead of being replaced by a fresh factory connection;
    ``bounded_sqlite_write`` wraps the DML AND the commit inside that lease (mirrors
    every other bare-``lease()`` caller in this repo, e.g. cycle_runtime.py's own
    ``_canonical_trade_write_lease`` use) so a BUSY collision is fail-fast and
    classified as ``WriteLeaseTimeout`` with proper coordinator telemetry, and the
    real SQLite write lock is never held after the coordinator's advisory gate is
    released. No HTTP runs inside the lease — it wraps only ``reconcile_with_chain``'s
    DML, never ``run_chain_sync``'s API call.

    Known, disclosed residual (R-AD review, 2026-09-13): ``reconcile_with_chain``
    (``src/state/chain_reconciliation.py``'s rescue-audit path, ~line 1435) contains
    its own internal ``conn.commit()`` on this SAME connection, wrapped in a local
    ``except Exception: logger.error(...)`` — pre-existing, undisturbed by this
    change, and explicitly documented there as deliberate ("rescue_events is an
    authoritative audit record ... durability is allowed here"). Under this lease
    that internal commit now also runs with busy_timeout=0, so a BUSY there is
    classified only by that module's own logger, not coordinator telemetry. No data
    is lost when it fires: SQLite does not discard a failed commit's pending rows,
    so they ride forward uncommitted to the next successful commit on the same
    connection — ultimately this cycle's own unconditional final commit below.
    Routing that specific internal commit through coordinator telemetry would require
    threading this lease into ``chain_reconciliation.reconcile()``'s signature, which
    is shared with the still-uncoordinated order-daemon chain-sync call site
    (cycle_runner.py:874) and is out of scope for this fix.
    """
    # Lazy imports (mirror src/main.py:_chain_sync_and_exit_monitor_cycle). The chain-sync
    # READ helpers live in the order-runtime cycle_runner; we import ONLY the read-phase
    # entry points (run_chain_sync + connection/portfolio helpers) and NEVER the monitoring
    # phase. Lazy so importing this module does not eagerly drag the trading lane into the
    # P4 process at import time (it is pulled only when the chain-sync job actually fires).
    from contextlib import contextmanager

    from src.data.polymarket_client import PolymarketClient
    from src.engine.cycle_runner import (
        _run_chain_sync,
        get_connection,
        load_portfolio,
        save_portfolio,
    )
    from src.ingest.post_trade_capital_daemon import _chain_sync_child_deadline_seconds
    from src.state.write_coordinator import (
        DBIdentity,
        WritePriority,
        bounded_sqlite_write,
        default_runtime_write_coordinator,
    )

    # T-chainsync2 (2026-09-13): derive ONE deadline_monotonic from the SAME
    # function the parent uses to size the subprocess kill timeout
    # (_chain_sync_child_deadline_seconds, post_trade_capital_daemon.py), so
    # every budget inside this child is a slice of the clock the parent kills
    # on. Before this, get_connection() below opened with no deadline at all,
    # so connect_or_degrade re-granted the default 30s busy_timeout to every
    # PRAGMA/ATTACH statement and the cutover-lease flock acquisition was
    # unbounded -- the child could spend its whole ~75s budget before its
    # first network call.
    cycle_started_at = time.monotonic()
    deadline_monotonic = cycle_started_at + _chain_sync_child_deadline_seconds()

    def _log_phase(phase: str, *, level: int = logging.INFO) -> None:
        logger.log(
            level,
            "chain_sync_read phase=%s elapsed_s=%.2f remaining_s=%.2f",
            phase,
            time.monotonic() - cycle_started_at,
            deadline_monotonic - time.monotonic(),
        )

    conn = get_connection(deadline_monotonic=deadline_monotonic)
    if conn is None:
        # R-AW MEDIUM (2026-09-13): connect_or_degrade swallows a deadline
        # TimeoutError into a plain None return, so this is the exact case
        # this fix exists to make observable -- the connect step itself
        # exhausted the child's budget. Log the same elapsed/remaining pair
        # _log_phase would have emitted on success, at ERROR, before raising.
        _log_phase("connect", level=logging.ERROR)
        raise RuntimeError("chain_sync_read: DB write-lock degraded before cycle")
    _log_phase("connect")

    # STANDARD priority / deadline_ms=1_500 / max_hold_ms=500 mirror the comparable
    # non-MONITOR TRADE sidecar convention in
    # src/engine/event_reactor_adapter.py::_persist_global_jit_authority_snapshot_isolated
    # (owner differs; parameters are the cited sibling's, not invented here).
    # These stay FIXED (not derived from deadline_monotonic, R-AW LOW 2026-09-13):
    # they bound the downstream committed-write admission window, not the
    # connect/read budget this fix addresses.
    #
    # R-AD fast-follow (2026-09-13): bounded_sqlite_write, not a hand-rolled
    # busy_timeout dance -- .lease() alone only RECORDS max_hold_ms in telemetry, it
    # never enforces it (write-lease-bounds-acquisition-not-hold). bounded_sqlite_write
    # is the same enforcement primitive write_coordinator.transaction() calls
    # internally (write_coordinator.py:805-814) and the convention every other
    # bare-.lease() caller in this repo uses (cycle_runtime.py:3505-3516,
    # exit_lifecycle.py:14456-14469, executor.py, chain_mirror_reconciler.py,
    # riskguard.py, global_batch_runtime.py) -- it forces busy_timeout=0 for the body,
    # classifies a BUSY collision as WriteLeaseTimeout with proper stage/error
    # telemetry (lease.record_stage/record_sqlite_error), and restores busy_timeout on
    # exit. .transaction() itself is not used here because it always opens its OWN
    # connection via connection_factory and unconditionally closes it in its own
    # finally: that would sever the INV-37 world+trade ATTACH this cycle's connection
    # carries, and would hit a closed connection at chain_sync_read_cycle's own
    # pre-existing outer safety-net conn.commit() below (which
    # test_chain_sync_read_failure_reaches_child_exit_status pins as firing
    # unconditionally), turning every ordinary successful cycle into a spurious
    # "chain_sync_read cycle failed".
    @contextmanager
    def _coordinated_reconcile_commit():
        coordinator = default_runtime_write_coordinator()
        with coordinator.lease(
            (DBIdentity.TRADE,),
            owner="chain_sync_read",
            write_class="live",
            priority=WritePriority.STANDARD,
            deadline_ms=1_500,
            max_hold_ms=500,
        ) as lease:
            with bounded_sqlite_write(conn, lease, max_hold_ms=500):
                # Unconditional commit preserves the pre-existing behavior of this
                # cycle: the reconcile writes are committed whether or not reconcile
                # raised, while still inside bounded_sqlite_write's busy_timeout=0
                # fence and the coordinator lease, so the real SQLite write lock is
                # released before the advisory gate is, and a commit-time BUSY is
                # classified the same way a DML-time BUSY is (WriteLeaseTimeout).
                try:
                    yield
                finally:
                    conn.commit()

    summary: dict = {}
    failure: Exception | None = None
    try:
        # T-chainsync (2026-09-13): reuse this cycle's already-ATTACHed trade+world
        # connection (conn, opened above via get_connection()) instead of letting
        # load_portfolio() open a second, independent write-class connection. The
        # second connection's bare sqlite3.connect()/PRAGMA journal_mode step (not
        # its query cost, already fixed by 0d749d879) was measured stalling up to
        # 2.6s under this daemon's own machine-gun commit cadence -- enough stalls
        # compound into the observed 15-77s chain-sync child kills (2,353 today).
        # A live A/B (15 min, 5s samples) showed a genuine mode=ro connection never
        # stalled while the write-capable shape did; passing connection=conn here
        # skips load_portfolio's redundant connect+ATTACH entirely (it detects
        # 'world' already attached via _attached_schema_names and skips its own
        # ATTACH), so no second connection is opened at all for this path.
        # X-BJ (2026-09-14): entry_proof_review=False skips
        # _query_edli_entry_proof_review_reasons (2.5-5.5s SELF time on the
        # unbounded load, per-open-EDLI-row venue_commands lookups). Verified
        # by reading this cycle's full body plus reconcile()'s: nothing this
        # subprocess runs before exit ever reads the EDLI-entry-proof-derived
        # chain_only_facts entries that computation produces (reconcile() only
        # APPENDS a different fact type at chain_reconciliation.py:3061; the
        # 48h-escalation reader check_quarantine_timeouts is called by
        # cycle_runner every cycle, not from here). The main cycle runner's
        # own unbounded load_portfolio() call (cycle_runner.py) is untouched
        # and keeps computing + alerting on this as before.
        #
        # recent_exits=False skips query_authoritative_settlement_rows(limit=None),
        # which defeats query_settlement_events's own limit=50 default and loads
        # every SETTLED position_events row ever (thousands), each fanning out
        # into _query_entry_execution_fill_hints. The result feeds ONLY
        # PortfolioState.recent_exits; verified (grep across src/) every reader:
        # save_portfolio's deprecated JSON cache (never called from this
        # function -- it imports but does not call save_portfolio) and
        # _track_exit (via compute_economic_close / compute_settlement_close /
        # mark_admin_closed / void_position), which only .append()s a fresh
        # exit record and never reads the list's pre-existing content --
        # reconcile() below does call void_position on this path, but that
        # append is unaffected by starting from an empty list. riskguard.py
        # independently computes and wholesale-replace()s recent_exits, never
        # reading the incoming value. Kept as a separate keyword from
        # entry_proof_review (see load_portfolio's docstring): the two gated
        # computations are unrelated (EDLI audit-trail vs. settlement/exit
        # history) that happen to both be dead work for this caller today.
        portfolio = load_portfolio(
            connection=conn,
            deadline_monotonic=deadline_monotonic,
            entry_proof_review=False,
            recent_exits=False,
        )
        _log_phase("load_portfolio")
        with PolymarketClient() as clob:
            # chain-truth sync — updates chain_shares / chain_avg_price / chain_state.
            # Degrades gracefully if Keychain funder_address is absent (REST call fails -> caught).
            # WAL WRITE-LOCK RELEASE (2026-06-08 riskguard-flaps structural fix, now §8 Step 2):
            # the chain-sync reconcile opened an implicit DEFERRED txn on the first DML
            # (chain_shares / chain_state updates) which upgrades to the exclusive WAL write
            # lock on zeus_trades.db. run_chain_sync commits (via write_scope, above) right
            # after the reconcile DML so the WAL write lock is released and the writes are
            # durable before this cycle does anything else. In the order daemon this commit
            # sat BETWEEN the two phases (before Phase-2 HTTP); in P4 there is no Phase-2, so
            # this commit is the cycle's final write and the lock is released on return.
            try:
                chain_stats, _ = _run_chain_sync(
                    portfolio, clob, conn, write_scope=_coordinated_reconcile_commit
                )
                if chain_stats:
                    summary["chain_sync"] = chain_stats
                _log_phase("chain_sync")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "chain_sync_read: chain sync failed: %s", exc, exc_info=True
                )
                summary["chain_sync_error"] = str(exc)
                failure = exc

            # Unconditional safety-net commit, preserved verbatim from the pre-coordinator
            # cycle: _run_chain_sync's own coordinated commit above already released the
            # WAL write lock in the normal path, so this is a no-op (no pending transaction)
            # whenever that ran. It stays because a caller-swapped/failed-before-DML chain
            # sync (e.g. HTTP failure, or the read-phase raising before reaching reconcile)
            # never enters the lease scope at all, and this is what finalizes/releases conn
            # in that case before close() below.
            try:
                conn.commit()
                _log_phase("commit")
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
    from src.data.job_lock import acquire_lock
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

# Redeem submission is absent by operator law 2026-06-10; on-chain redemption
# is decoupled entirely from Zeus (Polymarket settles win/loss on our behalf).
# No redeem-submitter or redeem-reconciler scheduler is registered in
# src/ingest/post_trade_capital_daemon.py. (2026-07-25: _redeem_reconciler_cycle
# was deleted here -- zero settlement_commands rows ever reached REDEEM_TX_HASHED
# in production, and harvester no longer enqueues REDEEM_INTENT_CREATED rows for
# it to eventually watch, so the poller was permanently a no-op.)


def _wrap_intent_creator_cycle() -> None:
    """Enqueue WRAP_REQUESTED if Safe USDC.e balance > threshold and no pending row.

    On-chain balance-driven (not journal-driven). Idempotent: skips if any
    non-terminal WRAP row already exists. Skipped in non-live mode.
    """
    from src.data.job_lock import acquire_lock
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
    from src.data.job_lock import acquire_lock
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
    from src.data.job_lock import acquire_lock
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
