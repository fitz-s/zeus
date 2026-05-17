"""Zeus main entry point — trading daemon only (Phase 3).

All discovery modes go through the same CycleRunner with different DiscoveryMode values.
The lifecycle is identical for all modes — only scanner parameters differ.

Phase 3: K2 ingest jobs removed. src/ingest_main.py owns all K2 ticks,
etl_recalibrate, ecmwf_open_data, automation_analysis, hole_scanner,
startup_catch_up, source_health_probe, drift_detector, ingest_status_rollup,
and harvester_truth_writer. Trading owns only discovery, harvester_pnl_resolver,
venue heartbeat, wallet gate, freshness gate (consumer), schema validator (consumer).

Advisory file lock infrastructure (src.data.dual_run_lock) is retained in code
— other daemons may be added in future. The K2 ticks that called it are removed.
"""

# Created: pre-Phase-0 (K2 scheduler wiring via 27bedbd; P9A run_mode observability via 7081634)
# Last reused/audited: 2026-05-16
# Authority basis: Phase 3 two-system independence — docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 3; docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md

import functools
import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from src.config import cities_by_name, get_mode, settings
from src.engine.cycle_runner import run_cycle
from src.engine.discovery_mode import DiscoveryMode
from src.observability.scheduler_health import _write_scheduler_health
from src.state.db import init_schema, get_world_connection, get_trade_connection

logger = logging.getLogger("zeus")

# Cross-mode lock: prevents two discovery modes from reading/writing portfolio concurrently
_cycle_lock = threading.Lock()
OPENING_HUNT_FIRST_DELAY_SECONDS = 30.0


def _utc_run_time_after(seconds: float) -> datetime:
    """Return a UTC first-run time for APScheduler interval jobs."""

    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _day0_first_delay_seconds(discovery: dict) -> float:
    """Stagger Day0 away from opening_hunt so equal-interval jobs do not race."""

    interval_seconds = float(discovery["day0_interval_min"]) * 60.0
    return OPENING_HUNT_FIRST_DELAY_SECONDS + (interval_seconds / 2.0)


def _scheduler_job(job_name: str):
    """Decorator: every scheduler.add_job(fn, ...) target in this module must
    wear this (B047 — see SCAFFOLD_B047_scheduler_observability.md).

    Wraps fn so that:
      - success → ``scheduler_jobs_health.json[job_name].status = OK`` + timestamp
      - exception → logged with traceback + ``status = FAILED`` + failure_reason

    Never re-raises (fail-open per K2 design in 27bedbd: daemon must keep
    running; OpenClaw supervisor relies on heartbeat). ``_write_heartbeat``
    is the sole scheduler target exempt from this decorator (it IS the
    coarse observability channel).
    """

    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                _write_scheduler_health(job_name, failed=False)
                return result
            except Exception as exc:
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                _write_scheduler_health(job_name, failed=True, reason=str(exc))

        return _wrapper

    return _decorator


@_scheduler_job("run_mode")
def _run_mode(mode: DiscoveryMode):
    """Wrapper with error handling and cycle lock for scheduler.

    Dual-signal observability: this wrapper writes to ``status_summary.json``
    via status_summary.write_status (the legacy mode-specific channel) AND
    the ``@_scheduler_job`` decorator independently writes to
    ``scheduler_jobs_health.json`` (B047 uniform channel). Non-conflicting.
    """
    acquired = _cycle_lock.acquire(blocking=False)
    if not acquired:
        logger.warning("%s skipped: another cycle is still running", mode.value)
        return
    try:
        summary = run_cycle(mode)
        logger.info("%s: %s", mode.value, summary)
    except Exception as e:
        logger.error("%s failed: %s", mode.value, e, exc_info=True)
        try:
            from src.observability.status_summary import write_status

            write_status(
                {
                    "mode": mode.value,
                    "failed": True,
                    "failure_reason": str(e),
                }
            )
        except Exception:
            logger.debug("failed to write error status for %s", mode.value, exc_info=True)
    finally:
        _cycle_lock.release()


@_scheduler_job("harvester")
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
            # K1 (2026-05-11): settlements → zeus-forecasts.db; pass forecasts conn.
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


# ---------------------------------------------------------------------------
# F14 + F16 cascade-liveness pollers (2026-05-16, SCAFFOLD §K v5)
# ---------------------------------------------------------------------------
# Per architecture/cascade_liveness_contract.yaml: each state-machine table
# with *_INTENT_CREATED / *_REQUESTED rows MUST have a registered scheduler
# poller. Without these, settlement_commands rows enqueued by
# harvester_pnl_resolver would sit forever (the F14 SEV-0 defect documented
# in docs/operations/task_2026-05-16_deep_alignment_audit/).
#
# _redeem_submitter_cycle: polls REDEEM_INTENT_CREATED, calls submit_redeem
#   (which transitions stub-deferred rows to REDEEM_OPERATOR_REQUIRED per
#   SCAFFOLD §K.3; operator then completes via scripts/operator_record_redeem.py).
# _redeem_reconciler_cycle: polls REDEEM_TX_HASHED, calls reconcile_pending_redeems
#   (no-op until web3 is wired — operator-recorded tx_hash sits in TX_HASHED
#   until PR-I.5 follow-up).
# _wrap_unwrap_liveness_guard_cycle: liveness_only mode; asserts table stays
#   empty until Z5 pUSD migration (per SCAFFOLD §E.2). Does NOT drive any
#   state transition (antibody test enforces this via ast walk).

@_scheduler_job("redeem_submitter")
def _redeem_submitter_cycle() -> None:
    """Poll settlement_commands for ALL _SUBMITTABLE_STATES rows + submit_redeem.

    PR #126 review-fix (Codex P1 + Copilot 3254021478): poll the full
    _SUBMITTABLE_STATES set (INTENT_CREATED + RETRYING), not just INTENT_CREATED.
    Without RETRYING in the query, rows that hit an adapter exception once
    and were durably moved to RETRYING by submit_redeem would never be
    re-attempted.

    PR #126 review-fix (Codex P1 + Copilot 3254021447/49): commit AFTER each
    submit_redeem call. submit_redeem only commits when own_conn=True; the
    poller passes conn=conn so own_conn=False; without an explicit commit
    the state transitions roll back when conn closes → INTENT_CREATED rows
    are re-processed every tick AND any real adapter tx_hash is not durably
    anchored. Per-row commit gives partial-failure tolerance.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.execution.settlement_commands import (
        _SUBMITTABLE_STATES,
        submit_redeem,
    )
    from src.state.db import get_trade_connection
    from src.venue.polymarket_v2_adapter import PolymarketV2Adapter

    with acquire_lock("redeem_submitter") as acquired:
        if not acquired:
            logger.info("redeem_submitter skipped_lock_held")
            return
        conn = get_trade_connection(write_class="live")
        try:
            # Poll ALL submittable states (INTENT_CREATED + RETRYING).
            placeholders = ",".join("?" * len(_SUBMITTABLE_STATES))
            state_values = tuple(s.value for s in _SUBMITTABLE_STATES)
            rows = conn.execute(
                f"""
                SELECT command_id FROM settlement_commands
                 WHERE state IN ({placeholders})
                 ORDER BY requested_at, command_id
                 LIMIT 32
                """,
                state_values,
            ).fetchall()
            if not rows:
                return
            adapter = PolymarketV2Adapter()
            submitted = 0
            failed = 0
            for row in rows:  # already capped at 32 via SQL LIMIT
                try:
                    result = submit_redeem(
                        row["command_id"], adapter, object(), conn=conn,
                    )
                    conn.commit()  # durable per-row commit; transitions stick
                    submitted += 1
                    logger.info(
                        "redeem_submitter: command_id=%s state=%s",
                        row["command_id"], result.state.value,
                    )
                except Exception as exc:  # noqa: BLE001 — fail-open per scheduler contract
                    # On exception submit_redeem may have committed an intermediate
                    # REDEEM_RETRYING via its own savepoint+commit (own_conn path
                    # closed it); for own_conn=False we still rollback in-flight
                    # uncommitted savepoints by closing the conn cleanly. Per-row
                    # rollback isolates failures from successful prior rows.
                    try:
                        conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    failed += 1
                    logger.error(
                        "redeem_submitter: command_id=%s error=%s",
                        row["command_id"], exc,
                    )
            logger.info(
                "redeem_submitter: submitted=%d failed=%d", submitted, failed,
            )
        finally:
            conn.close()


@_scheduler_job("redeem_reconciler")
def _redeem_reconciler_cycle() -> None:
    """Poll REDEEM_TX_HASHED rows + reconcile_pending_redeems against web3.

    NO-OP until web3 is installed + adapter wired (PR-I.5). Without web3,
    operator-recorded tx_hash rows sit in TX_HASHED indefinitely — that's
    expected per SCAFFOLD §I.2 ('redeem_reconciler: results=0').
    """
    from src.data.dual_run_lock import acquire_lock
    from src.execution.settlement_commands import (
        SettlementState,
        list_commands,
    )
    from src.state.db import get_trade_connection

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
                from web3 import Web3  # noqa: F401 — import probe only
            except ImportError:
                logger.info(
                    "redeem_reconciler: web3 not installed; rows=%d sitting in "
                    "TX_HASHED (expected pre-PR-I.5)", len(rows),
                )
                return
            # web3 provider wiring is PR-I.5 scope; in PR-I we declare the seam.
            logger.warning(
                "redeem_reconciler: web3 import succeeded but provider wiring "
                "is PR-I.5 scope; rows=%d not reconciled this tick.", len(rows),
            )
        finally:
            conn.close()


@_scheduler_job("wrap_unwrap_liveness_guard")
def _wrap_unwrap_liveness_guard_cycle() -> None:
    """liveness_only poller: assert wrap_unwrap_commands stays empty until Z5.

    Per SCAFFOLD §E.2: the wrap_unwrap_commands state machine exists in src/
    (defined but no production enqueue caller). Until pUSD migration ships,
    rows here are a bug. This poller counts and alerts; does NOT call any
    state-transition helper (antibody test enforces this via ast walk).
    """
    from src.state.db import get_world_connection

    conn = get_world_connection()
    try:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM wrap_unwrap_commands"
            ).fetchone()
        except Exception as exc:
            # Table may not exist in some envs; log + return (fail-open).
            logger.info("wrap_unwrap_liveness_guard: table missing (%s)", exc)
            return
        count = row[0] if row else 0
        if count > 0:
            logger.warning(
                "[WRAP_UNWRAP_LIVENESS_GUARD] %d unexpected rows in "
                "wrap_unwrap_commands; table must stay empty until Z5 per "
                "SCAFFOLD §E.2. Investigate before continuing.", count,
            )
        else:
            logger.debug("wrap_unwrap_liveness_guard: count=0 (expected)")
    finally:
        conn.close()


def _assert_cascade_liveness_contract(scheduler) -> None:
    """Boot-time mirror of tests/test_cascade_liveness_contract.py.

    Fail-closed: refuses to start the daemon if any required poller from
    architecture/cascade_liveness_contract.yaml is missing from scheduler.
    Guards against accidental edits that delete a job registration without
    updating the contract (or vice versa).
    """
    import pathlib
    import yaml

    contract_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "architecture"
        / "cascade_liveness_contract.yaml"
    )
    if not contract_path.exists():
        # Defensive: if contract YAML absent, skip — but log loudly so the
        # operator notices. Antibody test will still catch this in CI.
        logger.error(
            "_assert_cascade_liveness_contract: %s missing; skipping boot check",
            contract_path,
        )
        return
    contract = yaml.safe_load(contract_path.read_text())
    job_ids = {j.id for j in scheduler.get_jobs()}
    missing: list[tuple[str, str]] = []
    for sm in contract.get("state_machines", []) or []:
        for poller in sm.get("required_pollers", []) or []:
            if poller["id"] not in job_ids:
                missing.append((sm["table"], poller["id"]))
    if missing:
        raise SystemExit(
            f"FATAL: cascade_liveness_contract violation: missing pollers "
            f"{missing!r}. Refusing to boot. Either register the job in "
            f"src/main.py OR remove the contract entry in "
            f"architecture/cascade_liveness_contract.yaml."
        )


def run_single_cycle():
    """Run one complete cycle of all modes. For testing, not production."""
    logger.info("=== SINGLE CYCLE TEST ===")
    for mode in DiscoveryMode:
        logger.info("[%s]...", mode.value)
        _run_mode(mode)
    _harvester_cycle()
    logger.info("=== SINGLE CYCLE COMPLETE ===")


_heartbeat_fails = 0

def _write_heartbeat() -> None:
    """Write a heartbeat JSON to state/ every 60s so operators can detect silent crashes."""
    global _heartbeat_fails
    from src.config import state_path
    path = state_path("daemon-heartbeat.json")
    try:
        import json
        payload = {
            "alive": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": get_mode(),
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        _heartbeat_fails = 0
    except Exception as exc:
        _heartbeat_fails += 1
        logger.error("Heartbeat write failed (%d/3): %s", _heartbeat_fails, exc)
        try:
            from src.observability.status_summary import write_status
            write_status({
                "daemon_health": "FAULT",
                "failure_reason": f"heartbeat_write_failed: {exc}"
            })
        except Exception:
            pass

        if _heartbeat_fails >= 3:
            logger.critical("FATAL: Heartbeat failed 3 consecutive times. Halting daemon to prevent zombie state.")
            import os
            os._exit(1)


_venue_heartbeat_supervisor = None
_venue_heartbeat_adapter = None
_last_collateral_heartbeat_refresh_attempt_at = None
COLLATERAL_HEARTBEAT_REFRESH_SECONDS = 30.0


def _refresh_global_collateral_snapshot_if_due(
    adapter,
    *,
    now: datetime | None = None,
) -> bool:
    """Keep live collateral truth fresh without polling every heartbeat tick."""

    if adapter is None:
        return False
    try:
        from src.state.collateral_ledger import get_global_ledger

        ledger = get_global_ledger()
        if ledger is None:
            return False
        global _last_collateral_heartbeat_refresh_attempt_at
        current = now or datetime.now(timezone.utc)
        last_attempt = _last_collateral_heartbeat_refresh_attempt_at
        if last_attempt is not None:
            if last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=timezone.utc)
            attempt_age_seconds = (
                current - last_attempt.astimezone(timezone.utc)
            ).total_seconds()
            if 0 <= attempt_age_seconds < COLLATERAL_HEARTBEAT_REFRESH_SECONDS:
                return False
        snapshot = ledger.snapshot()
        captured_at = snapshot.captured_at
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        age_seconds = (current - captured_at.astimezone(timezone.utc)).total_seconds()
        if (
            snapshot.authority_tier != "DEGRADED"
            and age_seconds >= 0
            and age_seconds < COLLATERAL_HEARTBEAT_REFRESH_SECONDS
        ):
            return False
        _last_collateral_heartbeat_refresh_attempt_at = current
        ledger.refresh(adapter)
        return True
    except Exception as exc:
        logger.warning("CollateralLedger heartbeat refresh failed closed: %s", exc)
        return False


def _run_ws_gap_reconcile_if_required(
    adapter,
    *,
    conn_factory=None,
    ws_guard=None,
    now: datetime | None = None,
) -> dict:
    """Consume the M5 latch with a fresh read-only venue reconciliation sweep."""

    if adapter is None:
        return {"status": "adapter_unavailable"}
    if _cycle_lock.locked():
        return {"status": "deferred_cycle_running"}
    if ws_guard is None:
        from src.control import ws_gap_guard as ws_guard
    current = now or datetime.now(timezone.utc)
    try:
        summary = ws_guard.summary(now=current)
    except TypeError:
        summary = ws_guard.summary()
    if not bool(summary.get("m5_reconcile_required", False)):
        return {"status": "not_required"}

    owns_connection = conn_factory is None
    conn = None
    try:
        from src.execution.exchange_reconcile import run_ws_gap_reconcile_and_clear
        from src.state.db import get_trade_connection

        conn = (conn_factory or (lambda: get_trade_connection(write_class="live")))()
        result = run_ws_gap_reconcile_and_clear(
            adapter,
            conn,
            ws_guard=ws_guard,
            observed_at=current,
        )
        conn.commit()
        if result.get("status") == "cleared":
            logger.info("M5 WS-gap reconcile cleared submit latch: %s", result)
        else:
            logger.info("M5 WS-gap reconcile kept submit latch closed: %s", result)
        return result
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning("M5 WS-gap reconcile failed closed: %s", exc)
        return {"status": "failed_closed", "error": str(exc)}
    finally:
        if owns_connection and conn is not None:
            conn.close()


_user_channel_ingestor = None
_user_channel_thread = None


USER_CHANNEL_REQUIRED_ENV_VARS = (
    "ZEUS_USER_CHANNEL_WS_ENABLED",
    "POLYMARKET_USER_WS_CONDITION_IDS",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_API_PASSPHRASE",
)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "0").strip().lower() in {"1", "true", "yes", "on"}


def _auto_derive_user_channel_condition_ids() -> list[str]:
    """Derive the user-channel WS subscription set from the live market scanner.

    Wraps ``src.data.market_scanner.find_weather_markets`` + the
    ``extract_executable_condition_ids`` helper. Lives in main.py rather than
    market_scanner.py to keep the scanner free of side-effecting boot-time
    logging concerns; the helper itself is pure.

    On scanner failure we log + return [] rather than raising — boot must
    continue (the daemon will stay reduce_only without WS, which is the
    fail-closed posture the WS guard records as ``not_configured``).
    """
    try:
        from src.data.market_scanner import (
            extract_executable_condition_ids,
            find_weather_markets,
        )

        # min_hours_to_resolution=0.0: include day0 markets (<6h to settlement).
        # The scanner's default of 6.0 would silently drop day0 condition_ids
        # from the WS subscription set, so order/fill updates for day0 trades
        # — which Zeus actively trades via DiscoveryMode.DAY0_CAPTURE — would
        # be missed while the WS guard reports healthy. (PR #34 codex P1.)
        events = find_weather_markets(min_hours_to_resolution=0.0)
        return extract_executable_condition_ids(events)
    except Exception as exc:
        logger.warning(
            "user-channel WS auto-derive failed: %s; "
            "daemon stays in reduce_only=True mode",
            exc,
        )
        return []


def _start_user_channel_ingestor_if_enabled() -> None:
    """Start M3 Polymarket user-channel ingest in a daemon thread when enabled.

    Disabled by default so M3 adds no live WebSocket side effect until an
    operator explicitly enables `ZEUS_USER_CHANNEL_WS_ENABLED=1` and supplies
    condition IDs plus L2 API credentials. If enabled but misconfigured, the
    WS guard records an auth/config gap so new submits fail closed.

    Live-blockers 2026-05-01: when the WS is NOT enabled (or required env
    vars are missing) we now emit a single CLEAR WARNING line listing every
    missing var. Today the silent skip leaves operators with the cryptic
    ``ws_user_channel.gap_reason='not_configured'`` symptom and no surface
    explanation of which env vars to add to the launchd plist before the
    daemon can leave reduce_only mode.

    Auto-derive (2026-05-01): when ``ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1`` is
    set together with the master toggle and ``POLYMARKET_USER_WS_CONDITION_IDS``
    is empty, the subscription list is derived from the live market scanner
    so the daemon subscribes to exactly the markets it can trade, without
    a hardcoded plist value that would drift from on-chain truth as markets
    rotate (operator directive 2026-05-01: hardcoded values are structural
    failures). Operator can still pin a list via the env var; a non-empty
    env var always wins. Auto-derive returning 0 markets is a WARNING, not
    an error — the daemon stays in reduce_only mode, the WS guard reports
    ``condition_ids_missing``, and no exception escapes boot.
    """
    global _user_channel_ingestor, _user_channel_thread
    if not _truthy_env("ZEUS_USER_CHANNEL_WS_ENABLED"):
        missing = [
            name for name in USER_CHANNEL_REQUIRED_ENV_VARS
            if not (os.environ.get(name) or "").strip()
        ]
        logger.warning(
            "user-channel WS not configured: missing env vars %s; "
            "daemon stays in reduce_only=True mode",
            missing,
        )
        return
    if _user_channel_thread is not None and _user_channel_thread.is_alive():
        return

    raw_markets = os.environ.get("POLYMARKET_USER_WS_CONDITION_IDS", "")
    condition_ids = [m.strip() for m in raw_markets.split(",") if m.strip()]
    auto_derived = False
    if not condition_ids and _truthy_env("ZEUS_USER_CHANNEL_WS_AUTO_DERIVE"):
        condition_ids = _auto_derive_user_channel_condition_ids()
        auto_derived = True
        logger.info(
            "user-channel WS auto-derive yielded %d condition_ids "
            "(POLYMARKET_USER_WS_CONDITION_IDS empty, ZEUS_USER_CHANNEL_WS_AUTO_DERIVE=1)",
            len(condition_ids),
        )

    if not condition_ids:
        from src.control.ws_gap_guard import record_gap

        record_gap("condition_ids_missing", subscription_state="MARKET_MISMATCH")
        if auto_derived:
            logger.warning(
                "user-channel WS auto-derive yielded 0 condition_ids; daemon stays "
                "in reduce_only=True mode. Markets may be empty or the gamma query "
                "failed; check src.data.market_scanner."
            )
            return
        raise RuntimeError("POLYMARKET_USER_WS_CONDITION_IDS is required when ZEUS_USER_CHANNEL_WS_ENABLED=1")

    from src.data.polymarket_client import PolymarketClient
    from src.control.ws_gap_guard import record_gap
    from src.ingest.polymarket_user_channel import PolymarketUserChannelIngestor, WSAuth

    adapter = PolymarketClient()._ensure_v2_adapter()

    # Source L2 API credentials from the adapter's SDK client, which derives them
    # via create_or_derive_api_key().  This is the ONLY correct source of truth for
    # L2 creds — the plist env vars (POLYMARKET_API_KEY etc.) may be stale or absent
    # (operator directive 2026-05-01: on-chain derivation is the canonical source).
    # WSAuth.from_env() is intentionally NOT used here for the live daemon path.
    try:
        sdk_client = adapter._sdk_client()
        sdk_creds = sdk_client.creds
        ws_auth = WSAuth(
            api_key=sdk_creds.api_key,
            secret=sdk_creds.api_secret,
            passphrase=sdk_creds.api_passphrase,
        )
    except Exception as exc:
        record_gap(f"user_channel_start_failed:creds_unavailable", subscription_state="AUTH_FAILED")
        logger.error(
            "M3 user-channel ingestor could not obtain L2 creds from adapter: %s; "
            "daemon stays in reduce_only=True mode",
            exc,
        )
        return

    try:
        _user_channel_ingestor = PolymarketUserChannelIngestor(adapter, condition_ids, auth=ws_auth)
    except Exception as exc:
        record_gap(f"user_channel_start_failed:{type(exc).__name__}", subscription_state="AUTH_FAILED")
        raise

    _WS_RETRY_BASE_SECONDS = 5
    _WS_RETRY_MAX_SECONDS = 300  # cap at 5 minutes

    def _runner() -> None:
        import asyncio
        import math

        attempt = 0
        while True:
            try:
                asyncio.run(_user_channel_ingestor.start())
                # start() returned cleanly — server closed the connection gracefully.
                logger.warning("M3 user-channel ingestor exited cleanly; reconnecting")
            except Exception as exc:
                logger.error("M3 user-channel ingestor stopped: %s", exc, exc_info=True)
            attempt += 1
            backoff = min(
                _WS_RETRY_BASE_SECONDS * (2 ** min(attempt - 1, 6)),
                _WS_RETRY_MAX_SECONDS,
            )
            logger.info(
                "M3 user-channel ingestor will reconnect in %.0fs (attempt %d)",
                backoff,
                attempt,
            )
            import time as _time
            _time.sleep(backoff)

    _user_channel_thread = threading.Thread(
        target=_runner,
        name="polymarket-user-channel",
        daemon=True,
    )
    _user_channel_thread.start()
    logger.info(
        "M3 user-channel ingestor started for %d condition_ids (auto_derived=%s)",
        len(condition_ids),
        auto_derived,
    )


@_scheduler_job("venue_heartbeat")
def _write_venue_heartbeat() -> None:
    """Post the Polymarket venue heartbeat required for live resting orders."""
    global _venue_heartbeat_supervisor, _venue_heartbeat_adapter
    import asyncio

    from src.control.heartbeat_supervisor import (
        HeartbeatHealth,
        HeartbeatSupervisor,
        configure_global_supervisor,
        heartbeat_cadence_seconds_from_env,
    )

    try:
        if _venue_heartbeat_supervisor is None:
            from src.data.polymarket_client import PolymarketClient

            adapter = PolymarketClient()._ensure_v2_adapter()
            _venue_heartbeat_adapter = adapter
            _venue_heartbeat_supervisor = HeartbeatSupervisor(
                adapter,
                cadence_seconds=heartbeat_cadence_seconds_from_env(),
            )
            configure_global_supervisor(_venue_heartbeat_supervisor)
    except Exception as exc:
        if _venue_heartbeat_supervisor is None:
            _venue_heartbeat_supervisor = HeartbeatSupervisor(
                adapter=None,
                cadence_seconds=heartbeat_cadence_seconds_from_env(),
            )
            configure_global_supervisor(_venue_heartbeat_supervisor)
        _venue_heartbeat_supervisor.record_failure(exc)
        logger.error("Venue heartbeat failed closed: %s", exc)
        raise

    try:
        status = asyncio.run(_venue_heartbeat_supervisor.run_once())
    except Exception as exc:
        _venue_heartbeat_supervisor.record_failure(exc)
        logger.error("Venue heartbeat failed closed: %s", exc)
        raise
    if status.health is not HeartbeatHealth.HEALTHY:
        raise RuntimeError(
            f"venue heartbeat unhealthy: health={status.health.value}; "
            f"error={status.last_error or ''}"
        )
    _run_ws_gap_reconcile_if_required(_venue_heartbeat_adapter)
    _refresh_global_collateral_snapshot_if_due(_venue_heartbeat_adapter)


def _startup_freshness_check() -> None:
    """§3.1: data freshness gate at boot — uses evaluate_freshness_at_boot.

    §3.7 gate split:
    - Data freshness gate: degrade-or-warn on STALE. Operator may override
      individual sources via state/control_plane.json::force_ignore_freshness.
    - Wallet gate (_startup_wallet_check): NEVER overridable; hard exit on
      failure.

    Boot behavior (driven by evaluate_freshness_at_boot):
    - FRESH: log at INFO, proceed.
    - STALE: log warning with per-source details, proceed (degraded mode).
    - ABSENT: retry every BOOT_RETRY_INTERVAL_SECONDS up to
      BOOT_RETRY_MAX_ATTEMPTS, then SystemExit. The boot helper handles retry
      internally and never returns an ABSENT verdict to this caller.

    Codex PR #31 (P1) fix 2026-05-01: previously called
    evaluate_freshness_mid_run, which synthesizes ABSENT into a degraded
    all-STALE verdict. That made the `if branch == "ABSENT"` retry path here
    unreachable and silently weakened the boot safety contract — a missing
    source_health.json proceeded immediately as degraded instead of
    triggering the retry-then-FATAL window. Switching to the boot helper
    restores the design §3.1 contract.
    """
    from src.config import STATE_DIR
    from src.control.freshness_gate import evaluate_freshness_at_boot

    # evaluate_freshness_at_boot handles retry + SystemExit on ABSENT internally.
    verdict = evaluate_freshness_at_boot(STATE_DIR)

    if verdict.branch == "STALE":
        logger.warning(
            "Freshness gate STALE at boot: stale_sources=%s day0_capture_disabled=%s "
            "ensemble_disabled=%s (trading continues in degraded mode)",
            verdict.stale_sources, verdict.day0_capture_disabled, verdict.ensemble_disabled,
        )
    elif verdict.branch == "FRESH":
        logger.info("Freshness gate: FRESH — all sources within budget")


def _startup_world_schema_ready_check() -> None:
    """Design §4.2: trading boot retries then FAILs if DB schema readiness is not proven.

    Mirrors _startup_freshness_check retry pattern (30 × 10s = 5 min).
    Fail-closed: raises SystemExit if direct world or forecast DB schema checks
    cannot prove current `PRAGMA user_version` after retries.
    This is the Phase 2→Phase 3 enforcement promotion per architect audit A-2.

    K1 split 2026-05-11: this function now delegates to _startup_db_schema_ready_check,
    which checks both canonical DB files directly. The old data-ingest sentinel
    is no longer authority for live boot because live forecast production moved
    to forecast-live while com.zeus.data-ingest is not a required live process.
    Kept for API compat; do not remove.
    """
    _startup_db_schema_ready_check()


def _startup_world_db_schema_ready_check() -> str:
    """Read-only world DB schema currency check for live startup."""
    import sqlite3

    from src.state.db import ZEUS_WORLD_DB_PATH, assert_schema_current

    if not ZEUS_WORLD_DB_PATH.exists():
        raise FileNotFoundError(f"{ZEUS_WORLD_DB_PATH} does not exist")
    conn = sqlite3.connect(
        f"file:{ZEUS_WORLD_DB_PATH.resolve()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        conn.execute("PRAGMA query_only = ON")
        assert_schema_current(conn)
        row = conn.execute("PRAGMA user_version").fetchone()
        return str(row[0] if row else "unknown")
    finally:
        conn.close()


def _startup_forecasts_schema_ready_check() -> str:
    """Read-only forecast DB schema currency check for forecast-live split authority."""
    import sqlite3

    from src.state.db import ZEUS_FORECASTS_DB_PATH, assert_schema_current_forecasts

    if not ZEUS_FORECASTS_DB_PATH.exists():
        raise FileNotFoundError(f"{ZEUS_FORECASTS_DB_PATH} does not exist")
    conn = sqlite3.connect(
        f"file:{ZEUS_FORECASTS_DB_PATH.resolve()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        conn.execute("PRAGMA query_only = ON")
        assert_schema_current_forecasts(conn)
        row = conn.execute("PRAGMA user_version").fetchone()
        return str(row[0] if row else "unknown")
    finally:
        conn.close()


def _startup_db_schema_ready_check() -> None:
    """K1 split: directly verify world and forecast DB schema currency.

    Replaces _startup_world_schema_ready_check (retained above as a thin shim).
    Schema currency is verified directly against zeus-world.db and
    zeus-forecasts.db. This avoids binding live startup to stale JSON sentinels
    from retired or split data-daemon processes.

    Retry pattern: 30 × 10s = 5 min (mirrors _startup_freshness_check).
    """
    import time
    from src.control.freshness_gate import BOOT_RETRY_INTERVAL_SECONDS, BOOT_RETRY_MAX_ATTEMPTS

    for attempt in range(1, BOOT_RETRY_MAX_ATTEMPTS + 1):
        missing = []
        try:
            world_schema_version = _startup_world_db_schema_ready_check()
            logger.info(
                "world DB schema current: user_version=%s",
                world_schema_version,
            )
        except Exception as exc:
            logger.warning("world DB schema readiness check failed: %s — retrying", exc)
            missing.append("world")
        try:
            forecast_schema_version = _startup_forecasts_schema_ready_check()
            logger.info(
                "forecasts DB schema current: user_version=%s",
                forecast_schema_version,
            )
        except Exception as exc:
            logger.warning("forecasts DB schema readiness check failed: %s — retrying", exc)
            missing.append("forecasts")

        if not missing:
            return  # World and forecast DB schemas are current.

        if attempt < BOOT_RETRY_MAX_ATTEMPTS:
            logger.info(
                "DB schema checks missing=%s at boot — retry %d/%d in %ds",
                missing, attempt, BOOT_RETRY_MAX_ATTEMPTS, BOOT_RETRY_INTERVAL_SECONDS,
            )
            time.sleep(BOOT_RETRY_INTERVAL_SECONDS)

    raise SystemExit(
        "FATAL: DB schema readiness not proven within 5 min "
        "(zeus-world.db + zeus-forecasts.db user_version). "
        "Check direct DB schema initialization and launchctl list com.zeus.forecast-live"
    )


def _startup_wallet_check(clob=None):
    """P7: Fail-closed wallet gate. Live daemon refuses to start if wallet query fails.

    Accepts an optional clob for testing. In production, creates a live
    PolymarketClient.

    Also installs the process-wide CollateralLedger singleton with a
    persistent ledger-owned conn (2026-05-13 remediation). Prior to this
    the singleton was published from `PolymarketClient.get_balance()` while
    that wrapper still owned the conn — the wrapper's `finally: conn.close()`
    immediately poisoned the singleton, blocking every downstream
    `assert_buy_preflight` / `assert_sell_preflight` with
    `collateral_ledger_unconfigured` or `sqlite3.ProgrammingError`.
    """
    if clob is None:
        from src.data.polymarket_client import PolymarketClient
        clob = PolymarketClient()
    try:
        balance = float(clob.get_balance())
        logger.info("Startup wallet check: $%.2f pUSD available", balance)
    except Exception as exc:
        logger.critical("FAIL-CLOSED: wallet query failed at daemon start: %s", exc)
        sys.exit("FATAL: Cannot start — wallet unreachable. Fix credentials or network and restart.")

    # Install the process-wide collateral ledger singleton with a ledger-owned
    # persistent conn so downstream executor / riskguard preflight callers do
    # not race against transient conn close. Failures here are non-fatal at
    # boot — preflight will surface `collateral_ledger_unconfigured` if the
    # singleton is missing, which is already the existing fail-closed code
    # path for any operator misconfiguration.
    try:
        from src.state.collateral_ledger import (
            CollateralLedger,
            configure_global_ledger,
        )
        from src.state.db import _zeus_trade_db_path

        ledger = CollateralLedger(db_path=_zeus_trade_db_path())
        configure_global_ledger(ledger)
        logger.info(
            "CollateralLedger global singleton installed (db=%s)",
            _zeus_trade_db_path(),
        )
    except Exception as exc:
        logger.warning(
            "CollateralLedger global singleton install failed (preflight will fail-closed): %s",
            exc,
        )


def _startup_data_health_check(conn):
    """Warn about deferred data actions on every startup.

    This exists because bias correction activation and Platt recompute
    are easy to forget. The warnings persist until the actions are taken.
    """
    try:
        # 1. Bias correction reminder
        bias_enabled = settings.bias_correction_enabled
        bias_data = conn.execute(
            "SELECT COUNT(*) FROM model_bias WHERE source='ecmwf' AND n_samples >= 20"
        ).fetchone()[0]

        if not bias_enabled and bias_data > 0:
            logger.warning(
                "⚠ DEFERRED ACTION: bias_correction_enabled=false but %d ECMWF bias "
                "entries ready. To activate: 1) Recompute calibration_pairs with bias "
                "correction 2) Refit Platt models 3) Set bias_correction_enabled=true "
                "4) Run test_cross_module_invariants.py",
                bias_data,
            )

        forecast_city_count = conn.execute(
            "SELECT COUNT(DISTINCT city) FROM forecast_skill"
        ).fetchone()[0]
        bias_city_count = conn.execute(
            "SELECT COUNT(DISTINCT city) FROM model_bias WHERE source='ecmwf' AND n_samples >= 20"
        ).fetchone()[0]
        configured_city_count = len(cities_by_name)
        if forecast_city_count < configured_city_count or bias_city_count < configured_city_count:
            logger.warning(
                "⚠ DATA QUALITY GAP: forecast_skill covers %d/%d configured cities; "
                "mature ECMWF model_bias covers %d/%d. Missing bias data falls back "
                "to raw ensemble member maxes, archive quality is incomplete (raw ensemble member maxes only).",
                forecast_city_count,
                configured_city_count,
                bias_city_count,
                configured_city_count,
            )

        # 2. Data freshness check
        from datetime import datetime, timezone, timedelta

        stale_tables = []
        for table, col in [
            ("asos_wu_offsets", None),
            ("observation_instants", None),
            ("diurnal_curves", None),
            ("diurnal_peak_prob", None),
            ("temp_persistence", None),
            ("solar_daily", None),
        ]:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if n == 0:
                    stale_tables.append(f"{table} (empty)")
            except Exception:
                stale_tables.append(f"{table} (missing)")

        if stale_tables:
            logger.warning(
                "⚠ DATA GAPS: %s — run ETL scripts to populate",
                ", ".join(stale_tables),
            )

        # 3. Assumption manifest validation
        try:
            from scripts.validate_assumptions import run_validation

            validation = run_validation()
            if not validation["valid"]:
                logger.warning(
                    "⚠ ASSUMPTION MISMATCHES: %s",
                    " | ".join(validation["mismatches"]),
                )
        except Exception as e:
            logger.warning("⚠ Assumption validation failed to run: %s", e)

    except Exception as e:
        logger.debug("Startup health check failed: %s", e)


def _assert_live_safe_strategies_or_exit(*, refresh_state: bool = True) -> None:
    """G6 boot guard: refuse live launch when a non-allowlisted strategy is enabled.

    Composes the production-path enabled set:
      enabled = {s for s in KNOWN_STRATEGIES if is_strategy_enabled(s)}
    where ``is_strategy_enabled`` reads ``_control_state["strategy_gates"]`` —
    which is empty until ``refresh_control_state()`` hydrates it from the
    ``control_overrides`` table. Without that hydration, every strategy looks
    enabled (default-True) and the guard would refuse every launch regardless
    of operator configuration. So the helper hydrates first by default.

    ``refresh_state=False`` is reserved for tests that supply pre-populated
    state via monkeypatch; production callers should always leave the default.

    On success: returns silently. On refusal: SystemExit with FATAL message
    naming offending strategies (matches src/main.py:472-477 pattern).
    """
    from src.control.control_plane import (
        assert_live_safe_strategies_under_live_mode,
        is_strategy_enabled,
        refresh_control_state,
    )
    from src.engine.cycle_runner import KNOWN_STRATEGIES
    if refresh_state:
        refresh_control_state()
    enabled_strategies = {s for s in KNOWN_STRATEGIES if is_strategy_enabled(s)}
    assert_live_safe_strategies_under_live_mode(enabled_strategies)


def main():
    mode = get_mode()
    once = "--once" in sys.argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Zeus starting in %s mode%s", mode, " (single cycle)" if once else "")
    # Proxy health gate: strip dead HTTP_PROXY so data-only mode works
    # without VPN. Must precede any HTTP call (PolymarketClient wallet check, etc).
    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()
    # Capital truth: query on-chain wallet via bankroll_provider. Startup must
    # never log retired config-literal capital as if it were wallet truth.
    try:
        from src.runtime.bankroll_provider import current as _bankroll_current
        _record = _bankroll_current()
        _capital_str = f"${_record.value_usd:.2f}" if _record else "<wallet_unreachable>"
    except Exception as _exc:
        _capital_str = f"<wallet_query_error: {_exc}>"
    logger.info("Capital (on-chain): %s | Kelly: %.0f%%",
                _capital_str,
                settings["sizing"]["kelly_multiplier"] * 100)

    # §4.2 DB schema-ready gate — fail-closed (Phase 3 enforcement).
    # Must run before the first world DB open/read so missing or uninitialized
    # DBs go through the retry/FATAL authority path rather than raw SQLite errors.
    # Directly verifies world/forecast DB schema versions. Older JSON sentinels
    # from data-ingest are not live boot authority after the forecast-live split.
    _startup_world_schema_ready_check()

    # Daemon is a read-only consumer of world DB. Schema currency was proven
    # above by direct read-only user_version checks on the canonical DB files.
    # Opening without write_class avoids the v4 LIVE flock and never acquires
    # a SQLite writer lock for read-only ops below — so a concurrent ingest
    # or backfill cannot starve daemon startup.
    conn = get_world_connection()
    # Read-only smoke: confirm world DB is reachable.
    conn.execute("SELECT COUNT(*) FROM settlements LIMIT 1").fetchone()

    # Ensure trade DB has all tables (prevents lazy-creation gaps)
    trade_conn = get_trade_connection(write_class="live")
    init_schema(trade_conn)
    trade_conn.close()

    # Startup health check: warn about deferred data actions
    _startup_data_health_check(conn)

    # world_schema_manifest.yaml + validate_world_schema_at_boot RETIRED in P2
    # (2026-05-14 K1 followups plan §5.5 D5). assert_db_matches_registry() exists
    # (src/state/table_registry.py) but boot wiring is deferred — not called here.
    conn.close()

    # §3.1 Data freshness gate — WARN-only at boot (Phase 2: warn; Phase 3: enforce).
    # Runs BEFORE strategy gate so operator sees freshness diagnostics even when
    # strategy gate refuses. GATE SPLIT (§3.7): data gate is operator-overridable
    # via state/control_plane.json::force_ignore_freshness: ["source_name"].
    # Wallet gate (_startup_wallet_check below) is NEVER overridable.
    # Absent source_health.json → 5-min retry then FATAL (see freshness_gate.py).
    # Stale source_health.json → degrade per source family; trading continues.
    # Phase 3 will promote ABSENT result here to a hard FATAL (currently warn).
    _startup_freshness_check()

    # G6 antibody (2026-04-26, fixed 2026-04-26 per con-nyx CONDITION C1):
    # Refuse boot if any non-allowlisted strategy is enabled. Must run AFTER
    # init_schema (so control_overrides table exists) and BEFORE wallet check
    # (no point spending HTTP if guard refuses). The helper hydrates
    # _control_state from durable storage before composing the enabled set —
    # without hydration, every strategy reads as enabled (default-True) and
    # operator-set gates from prior `set_strategy_gate` invocations are not
    # visible. See _assert_live_safe_strategies_or_exit() docstring above.
    _assert_live_safe_strategies_or_exit()

    # P7: Fail-closed wallet gate — must run before first cycle.
    # GATE SPLIT (§3.7): wallet failure is ALWAYS fatal, no operator override.
    _startup_wallet_check()
    _start_user_channel_ingestor_if_enabled()

    if once:
        run_single_cycle()
        return

    # APScheduler loop mode.
    # P0 invariant: scheduler MUST run in UTC. Cron expressions like
    # ``hour=7,9,19,21`` for update_reaction_times_utc are written
    # against UTC; without an explicit timezone= kwarg APScheduler
    # falls back to the host's local tz (CDT/CST on the deployment
    # box), shifting every cron job by 5h. See ``docs/operations/
    # task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md`` §P0
    # (the file is at v3 per its §0.1 changelog) and §4 D-D drift +
    # operator directive 2026-05-04 "所有的执行时间都需要严格统一用utc".
    scheduler = BlockingScheduler(timezone=ZoneInfo("UTC"))
    discovery = settings["discovery"]

    # All modes use the SAME CycleRunner with different DiscoveryMode values
    # max_instances=1: prevent concurrent execution if previous cycle still running
    scheduler.add_job(
        lambda: _run_mode(DiscoveryMode.OPENING_HUNT), "interval",
        minutes=discovery["opening_hunt_interval_min"], id="opening_hunt",
        next_run_time=_utc_run_time_after(OPENING_HUNT_FIRST_DELAY_SECONDS),
        max_instances=1, coalesce=True,
    )
    for time_str in discovery["update_reaction_times_utc"]:
        h, m = time_str.split(":")
        scheduler.add_job(
            lambda: _run_mode(DiscoveryMode.UPDATE_REACTION), "cron",
            hour=int(h), minute=int(m), id=f"update_reaction_{time_str}",
            max_instances=1, coalesce=True,
        )
    scheduler.add_job(
        lambda: _run_mode(DiscoveryMode.DAY0_CAPTURE), "interval",
        minutes=discovery["day0_interval_min"], id="day0_capture",
        next_run_time=_utc_run_time_after(_day0_first_delay_seconds(discovery)),
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(_harvester_cycle, "interval", hours=1, id="harvester")
    scheduler.add_job(_write_heartbeat, "interval", seconds=60, id="heartbeat",
                      max_instances=1, coalesce=True)
    from src.control.heartbeat_supervisor import heartbeat_cadence_seconds_from_env
    scheduler.add_job(
        _write_venue_heartbeat,
        "interval",
        seconds=heartbeat_cadence_seconds_from_env(),
        id="venue_heartbeat",
        max_instances=1,
        coalesce=True,
    )

    # 2026-05-16 PR-I C3 — F14 + F16 cascade-liveness pollers per SCAFFOLD §K v5
    # + architecture/cascade_liveness_contract.yaml. Insertion site is here per
    # SCAFFOLD §K.8 v5 (after L988 venue_heartbeat block; pre-existing K2 jobs
    # below were already migrated to src/ingest_main.py).
    scheduler.add_job(
        _redeem_submitter_cycle, "interval", minutes=5, id="redeem_submitter",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _redeem_reconciler_cycle, "interval", minutes=10, id="redeem_reconciler",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        _wrap_unwrap_liveness_guard_cycle, "interval", minutes=30,
        id="wrap_unwrap_liveness_guard", max_instances=1, coalesce=True,
    )

    # Boot-time fail-closed cascade-liveness contract check. MUST run AFTER
    # all scheduler.add_job calls so it sees the complete job set, and
    # BEFORE scheduler.start() so a contract violation prevents booting.
    _assert_cascade_liveness_contract(scheduler)

    # Phase 3: K2 ingest jobs removed from this scheduler block.
    # All K2 ticks, etl_recalibrate, ecmwf_open_data, automation_analysis,
    # hole_scanner, startup_catch_up, source_health_probe, drift_detector,
    # ingest_status_rollup, and harvester_truth_writer are now owned by
    # com.zeus.data-ingest (src/ingest_main.py).
    # See design §5 Phase 3 and antibody #8 (tests/test_main_module_scope.py).

    jobs = [j.id for j in scheduler.get_jobs()]
    logger.info("Scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
