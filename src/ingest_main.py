# Lifecycle: created=2026-04-30; last_reviewed=2026-07-16; last_reused=2026-07-16
# Authority basis: docs/archive/2026-Q2/task_2026-05-14_data_daemon_live_efficiency/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md
#   Phase 2 legacy OpenData mutual exclusion with forecast-live-daemon; 2026-05-20
#   live stability hotfix keeps SIGTERM scheduler shutdown exit code clean.
#   2026-06-08 thepath/audit-realign Fitz #5: bare no-timeout sqlite3.connect on
#   the K2 BULK hko_tick write path now carries the configured busy_timeout.
"""Zeus data-ingest daemon entry point.

Runs all K2 ingest jobs and supporting cycles on an independent APScheduler.
Does NOT import from src.engine, src.execution, src.strategy, src.control, or
src.main — those are trading-lane only. The Day0 source-clock job may emit the
canonical source-derived opportunity event through src.events after the source
fact is durable; it never evaluates, risks, or submits an order.

Boot sequence:
1. Proxy health check (strip dead proxy).
2. init_schema on world connection.
3. Write state/world_schema_ready sentinel (atomic).
4. Register SIGTERM handler for graceful shutdown.
5. Start APScheduler.
6. Start 60s heartbeat tick writing state/daemon-heartbeat-ingest.json.

Each K2 tick acquires the per-table advisory lock from src.data.dual_run_lock
before running. If the monolith also tries to run the same tick, it will see
the lock held and skip silently (skipped_lock_held). When the monolith is
shut down (Phase 3), the ingest daemon acquires locks uncontested.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("zeus.ingest")

# ---------------------------------------------------------------------------
# Module-level scheduler reference for SIGTERM handler
# ---------------------------------------------------------------------------
_scheduler: Any | None = None
FORECAST_LIVE_OWNER_ENV = "ZEUS_FORECAST_LIVE_OWNER"
REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV = "ZEUS_REPLACEMENT_AVAILABILITY_POLL_SECONDS"
REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV = "ZEUS_REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS"
REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV = "ZEUS_REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS"
DAY0_METAR_POLL_SECONDS_ENV = "ZEUS_DAY0_METAR_POLL_SECONDS"
DAY0_METAR_WRITE_BUDGET_MS_ENV = "ZEUS_DAY0_METAR_WRITE_BUDGET_MS"
DAY0_METAR_COMMIT_RETRY_SECONDS = 0.25
_ORACLE_BRIDGE_LOCK = threading.Lock()
_ORACLE_SNAPSHOT_LOCK = threading.Lock()
_DAY0_METAR_EMITTER: Any | None = None
_DAY0_METAR_COMMIT_LOCK = threading.Lock()
_DAY0_METAR_PENDING_COMMITS: list[tuple[Any, str, bool]] = []
_REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC = 0.0

# SIGTERM-unif (WAVE-4): captured at module load so the forensic elapsed
# computed in _graceful_shutdown matches what src/main.py and
# src/riskguard/riskguard.py emit. See WAVE3_BATCH_C_PER_FINDING_ACCOUNTING.md
# carry-forward #5.
_PROCESS_START = time.monotonic()


def _forecast_live_owner() -> str:
    return os.environ.get(FORECAST_LIVE_OWNER_ENV, "ingest_main").strip().lower() or "ingest_main"


def _ingest_main_owns_opendata() -> bool:
    # PR4 data_temporal_kernel: route ownership through the single registry authority so the
    # registry and the daemons can never disagree. Behavior-identical to the prior
    # `_forecast_live_owner() != "forecast_live"` (active_opendata_owner returns "ingest_main"
    # iff the env token is not "forecast_live").
    from src.data.source_job_registry import active_opendata_owner

    return active_opendata_owner(_forecast_live_owner()) == "ingest_main"


def _day0_metar_poll_seconds() -> float:
    raw = os.environ.get(DAY0_METAR_POLL_SECONDS_ENV, "").strip()
    if not raw:
        return 5.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning(
            "invalid %s=%r; using 5s Day0 METAR source-clock cadence",
            DAY0_METAR_POLL_SECONDS_ENV,
            raw,
        )
        return 5.0


def _day0_metar_write_budget_seconds() -> float:
    raw = os.environ.get(DAY0_METAR_WRITE_BUDGET_MS_ENV, "").strip()
    if not raw:
        return 0.2
    try:
        milliseconds = float(raw)
    except ValueError:
        logger.warning(
            "invalid %s=%r; using 200ms Day0 METAR write budget",
            DAY0_METAR_WRITE_BUDGET_MS_ENV,
            raw,
        )
        return 0.2
    return max(0.001, min(milliseconds / 1000.0, 1.0))


def _day0_metar_emitter():
    global _DAY0_METAR_EMITTER
    if _DAY0_METAR_EMITTER is None:
        from src.data.day0_fast_obs import Day0FastObsEmitter

        # APScheduler owns this lane's cadence and max_instances=1 prevents
        # overlap. A matching start-to-start throttle turns scheduler jitter
        # into skipped polls, stretching the effective source clock to 10s.
        _DAY0_METAR_EMITTER = Day0FastObsEmitter(min_fetch_interval_s=0.0)
    return _DAY0_METAR_EMITTER


def _stage_day0_metar_commit(
    prefetch: Any,
    *,
    received_at: str,
    day0_is_tradeable: bool,
) -> None:
    with _DAY0_METAR_COMMIT_LOCK:
        staged = (
            prefetch,
            received_at,
            day0_is_tradeable,
        )
        if len(_DAY0_METAR_PENDING_COMMITS) < 2:
            _DAY0_METAR_PENDING_COMMITS.append(staged)
        else:
            _DAY0_METAR_PENDING_COMMITS[-1] = staged


def _commit_pending_day0_metar(*, origin: str) -> dict:
    """Commit an already-fetched METAR delta without repeating network I/O."""

    if not _DAY0_METAR_COMMIT_LOCK.acquire(blocking=False):
        return {"status": "COMMIT_ACTIVE"}

    conn = None
    mutex = None
    acquired = False
    emitted = 0
    inserted_event_ids: list[str] = []
    inserted_families: list[tuple[str, str, str]] = []
    pending_reports = 0
    try:
        if not _DAY0_METAR_PENDING_COMMITS:
            return {"status": "SOURCE_CURRENT"}
        staged = _DAY0_METAR_PENDING_COMMITS[0]
        prefetch, received_at, day0_is_tradeable = staged
        pending_reports = len(tuple(prefetch.ledger_reports or ()))
        if pending_reports == 0:
            del _DAY0_METAR_PENDING_COMMITS[0]
            return {"status": "SOURCE_CURRENT"}

        import sqlite3

        from src.runtime.reactor_wake import publish_reactor_wake
        from src.state.db import get_world_connection, world_write_mutex

        conn = get_world_connection(write_class="live")
        write_budget_s = _day0_metar_write_budget_seconds()
        conn.execute(f"PRAGMA busy_timeout = {max(1, int(write_budget_s * 1000.0))}")
        mutex = world_write_mutex()
        acquired = mutex.acquire(timeout=write_budget_s)
        if not acquired:
            logger.info(
                "DAY0_METAR_COMMIT_DEFERRED origin=%s reason=world_writer_busy "
                "pending_reports=%d budget_ms=%d",
                origin,
                pending_reports,
                int(write_budget_s * 1000.0),
            )
            return {
                "status": "WRITE_CONTENDED",
                "pending_reports": pending_reports,
            }

        try:
            conn.execute("BEGIN IMMEDIATE")
            emitted = _day0_metar_emitter().emit_prefetched(
                world_conn=conn,
                prefetch=prefetch,
                received_at=received_at,
                limit=max(50, len(prefetch.eligible) * 2),
                day0_is_tradeable=day0_is_tradeable,
                inserted_event_ids=inserted_event_ids,
                inserted_families=inserted_families,
            )
            conn.commit()
            del _DAY0_METAR_PENDING_COMMITS[0]
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                logger.info(
                    "DAY0_METAR_COMMIT_DEFERRED origin=%s reason=sqlite_busy "
                    "pending_reports=%d exc=%r",
                    origin,
                    pending_reports,
                    exc,
                )
                return {
                    "status": "WRITE_CONTENDED",
                    "pending_reports": pending_reports,
                }
            raise
        except BaseException:
            conn.rollback()
            raise
    finally:
        if acquired and mutex is not None:
            mutex.release()
        if conn is not None:
            conn.close()
        _DAY0_METAR_COMMIT_LOCK.release()

    if emitted:
        try:
            publish_reactor_wake(
                source="day0_metar_source_clock",
                reason="day0_extreme_event_committed",
                event_ids=tuple(inserted_event_ids),
                forecast_families=tuple(dict.fromkeys(inserted_families)),
            )
        except Exception:
            logger.warning(
                "DAY0_METAR_REACTOR_WAKE_FAILED events_emitted=%d; "
                "periodic reactor scan remains authoritative",
                emitted,
                exc_info=True,
            )
    logger.info(
        "DAY0_METAR_COMMIT_COMPLETED origin=%s pending_reports=%d emitted=%d",
        origin,
        pending_reports,
        emitted,
    )
    return {
        "status": "COMMITTED",
        "pending_reports": pending_reports,
        "events_emitted": emitted,
    }


def _schedule_day0_metar_commit_retry() -> bool:
    """Schedule one retry only while a prefetched canonical write is pending."""

    if _scheduler is None:
        logger.error("DAY0_METAR_COMMIT_RETRY_NOT_SCHEDULED reason=scheduler_unavailable")
        return False
    _scheduler.add_job(
        _day0_metar_commit_retry_tick,
        "date",
        run_date=datetime.now(timezone.utc)
        + timedelta(seconds=DAY0_METAR_COMMIT_RETRY_SECONDS),
        id="ingest_day0_metar_commit_retry",
        executor="source_clock_db",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1,
        replace_existing=True,
    )
    return True


def _commit_or_schedule_day0_metar(*, origin: str) -> dict:
    result = _commit_pending_day0_metar(origin=origin)
    if result.get("status") in {"COMMIT_ACTIVE", "WRITE_CONTENDED"}:
        _schedule_day0_metar_commit_retry()
    return result


def _replacement_availability_poll_seconds() -> int:
    """Fast source-clock poll cadence for replacement raw-input downloads.

    Open-Meteo model-update metadata is cheap and parallelized. Fifteen seconds
    bounds unchanged-state detection lag without repeating the heavier current-
    target maintenance, which is independently throttled below.
    """
    raw = os.environ.get(REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV, "").strip()
    if not raw:
        return 15
    try:
        return max(15, int(raw))
    except ValueError:
        logger.warning(
            "invalid %s=%r; using 15s replacement availability poll cadence",
            REPLACEMENT_AVAILABILITY_POLL_SECONDS_ENV,
            raw,
        )
        return 15


def _replacement_source_clock_download_budget_seconds(poll_seconds: int | None = None) -> float:
    """Wall-clock budget for the source-clock scoped download body.

    A scoped download may span multiple probe intervals; APScheduler's single
    instance/coalescing prevents overlap. Keep the established 45-second budget
    so a faster metadata probe does not turn useful downloads into retry loops.
    """
    default_s = 45.0
    raw = os.environ.get(REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV, "").strip()
    if not raw:
        return default_s
    try:
        requested = float(raw)
    except ValueError:
        logger.warning(
            "invalid %s=%r; using %.1fs replacement source-clock download budget",
            REPLACEMENT_SOURCE_CLOCK_DOWNLOAD_BUDGET_SECONDS_ENV,
            raw,
            default_s,
        )
        return default_s
    return max(1.0, min(requested, 60.0))


def _replacement_current_target_poll_timeout_seconds(poll_seconds: int | None = None) -> float:
    """Bound the periodic current-target maintenance substep.

    Maintenance runs only on an unchanged-source tick and at most once per
    minute, so its useful 20-second budget is independent of the 15-second
    metadata cadence.
    """
    default_s = 20.0
    raw = os.environ.get(REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV, "").strip()
    if not raw:
        return default_s
    try:
        requested = float(raw)
    except ValueError:
        logger.warning(
            "invalid %s=%r; using %.1fs replacement current-target poll timeout",
            REPLACEMENT_CURRENT_TARGET_POLL_TIMEOUT_SECONDS_ENV,
            raw,
            default_s,
        )
        return default_s
    return max(1.0, min(requested, 60.0))


def _replacement_maintenance_due(*, now_monotonic: float | None = None) -> bool:
    global _REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    if now < _REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC:
        return False
    _REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC = now + max(
        60.0,
        float(_replacement_availability_poll_seconds()),
    )
    return True


def _defer_replacement_maintenance(
    seconds: float,
    *,
    now_monotonic: float | None = None,
) -> None:
    global _REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    _REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC = max(
        _REPLACEMENT_MAINTENANCE_NEXT_MONOTONIC,
        now + max(0.0, float(seconds)),
    )


def _graceful_shutdown(signum, frame) -> None:
    """SIGTERM handler — wait for in-flight jobs then exit 0.

    Emits two log lines:
    1. The legacy `received SIGTERM` line (INFO → .log) — preserves
       backward compat with operator grep tooling installed before
       WAVE-4 SIGTERM-unif.
    2. The unified `SIGTERM_RECEIVED pid=... ppid=... elapsed=...s`
       token (ERROR → .err) — same forensic shape that src/main.py,
       src/riskguard/riskguard.py and src/control/heartbeat_supervisor.py
       emit, so a single grep across all 5 daemons returns parity hits.
    """
    logger.info("data-ingest daemon received SIGTERM; shutting down scheduler")
    logger.error(
        "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
        os.getpid(), os.getppid(), int(time.monotonic() - _PROCESS_START),
    )
    try:
        _shutdown_scheduler_if_running(_scheduler, wait=True)
    except Exception as exc:
        logger.warning("Scheduler shutdown error: %s", exc)
    sys.exit(0)


def _shutdown_scheduler_if_running(scheduler: Any | None, *, wait: bool = True) -> None:
    """Stop APScheduler during process exit without converting SIGTERM to exit 1."""
    if scheduler is None:
        return
    from apscheduler.schedulers.base import SchedulerNotRunningError

    try:
        scheduler.shutdown(wait=wait)
    except SchedulerNotRunningError:
        logger.info("Scheduler already stopped during shutdown")


# ---------------------------------------------------------------------------
# Decorator — mirrors src/main.py:_scheduler_job
# ---------------------------------------------------------------------------

_TRUTHFUL_FAIL_STATUSES = frozenset({
    "download_failed",
    "empty_ingest",
    "extract_failed",
    "market_events_persistence_failed",
    "market_scan_failed",
    "paused_mars_credentials",
    "bad_target_date",
})


def _scheduler_job(job_name: str):
    """Uniform error-swallowing wrapper for all APScheduler targets.

    Truthfulness contract (2026-05-01): if the job returns a status dict
    whose ``status`` indicates a structural failure (one of
    ``_TRUTHFUL_FAIL_STATUSES``) OR whose insert counters are all zero
    while the dict reports a rejection reason, the wrapper writes a FAILED
    entry instead of OK. This closes the antibody that previously let
    silent zero-row runs masquerade as healthy.

    On success: writes scheduler_jobs_health.json OK entry.
    On exception: logs + writes FAILED entry; does NOT re-raise.
    On structural-failure status dict: writes FAILED entry with the dict's
    own reason.
    """
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                from src.observability.scheduler_health import _write_scheduler_health
                _write_scheduler_health(job_name, failed=False, started=True)
                result = fn(*args, **kwargs)
                failed, reason = _classify_result(result)
                _write_scheduler_health(job_name, failed=failed, reason=reason)
                return result
            except Exception as exc:
                logger.error("%s failed: %s", job_name, exc, exc_info=True)
                try:
                    from src.observability.scheduler_health import _write_scheduler_health
                    _write_scheduler_health(job_name, failed=True, reason=str(exc))
                except Exception:
                    pass
        return _wrapper
    return _decorator


def _classify_result(result) -> tuple[bool, str | None]:
    """Map a job-return value to (failed?, reason). Truthfulness antibody.

    ``None`` / non-dict returns are treated as success — most ticks return
    None. Dict returns get inspected: any structural-failure status, or
    a paused-by-control-plane response with a non-zero ``error`` field, is
    flagged FAILED so operators see the truth.
    """
    if not isinstance(result, dict):
        return False, None
    status = str(result.get("status", "")).lower()
    if status in _TRUTHFUL_FAIL_STATUSES:
        return True, status + (": " + str(result.get("error")) if result.get("error") else "")
    # Inserted=0 on a stage-failure dict (e.g., DR-33-A flag-off harvester)
    # is a legitimate noop — control_plane pause + empty noop must NOT be
    # tagged failed.
    if status in {"paused_by_control_plane", "noop_no_dates"}:
        return False, None
    # Inserted/snapshots_inserted == 0 alone is not a failure (idempotent
    # re-run) unless paired with a structural error. Check stages for any
    # ``ok=False`` entries.
    stages = result.get("stages") or []
    for stage in stages:
        if isinstance(stage, dict) and stage.get("ok") is False:
            return True, f"stage_failed:{stage.get('label', '?')}:{stage.get('error', '?')}"
    return False, None


def _assert_forecasts_schema_ready_for_ingest() -> None:
    """Fail ingest boot before forecast-class writer jobs start on stale schema."""

    from src.state.db import (
        assert_schema_current_forecasts,
        get_forecasts_connection,
        init_schema_forecasts,
    )

    conn = get_forecasts_connection(write_class="bulk")
    try:
        if _forecasts_schema_current_lightweight():
            logger.info(
                "init_schema_forecasts skipped: fast forecast schema probe passed; "
                "running full schema assertion"
            )
        else:
            init_schema_forecasts(conn)
        assert_schema_current_forecasts(conn)
        conn.commit()
    finally:
        conn.close()


def _forecasts_schema_current_lightweight() -> bool:
    """Read-only live-required forecast schema check for fast daemon restarts."""
    import sqlite3

    from src.state.db import ZEUS_FORECASTS_DB_PATH

    required_indexes = {
        "idx_forecast_posteriors_live_family_cycle",
        "idx_raw_model_forecasts_endpoint_family_cycle_members",
    }
    try:
        uri = f"file:{ZEUS_FORECASTS_DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            indexes = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
            if required_indexes - indexes:
                return False
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in ("forecast_posteriors", "raw_model_forecasts"):
                if table not in tables:
                    return False
                columns = {
                    str(row[1])
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "trade_authority_status" in columns:
                    return False
            return True
        finally:
            conn.close()
    except Exception:
        return False


def _is_source_paused(source_id: str) -> bool:
    """Check if a source is paused by an operator directive in control_plane.json.

    Reads state/control_plane.json on each call (cheap JSON read).
    Returns True → caller should skip the tick and emit paused_by_control_plane status.
    """
    try:
        from src.control.control_plane import read_ingest_control_state
        state = read_ingest_control_state()
        return source_id in state.get("paused_sources", set())
    except Exception as exc:
        logger.warning("_is_source_paused check failed for %s: %s", source_id, exc)
        return False


def _etl_subprocess_python() -> str:
    candidate = Path(__file__).parent.parent / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

_ingest_heartbeat_fails = 0


def _write_ingest_heartbeat() -> None:
    """Write daemon-heartbeat-ingest.json every 60s (design §4.5d)."""
    global _ingest_heartbeat_fails
    from src.config import state_path
    path = state_path("daemon-heartbeat-ingest.json")
    try:
        payload = {
            "daemon": "data-ingest",
            "alive_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
        }
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        _ingest_heartbeat_fails = 0
    except Exception as exc:
        _ingest_heartbeat_fails += 1
        logger.error("Ingest heartbeat write failed (%d): %s", _ingest_heartbeat_fails, exc)


# ---------------------------------------------------------------------------
# Sentinel write (design §4.2)
# ---------------------------------------------------------------------------

def _write_world_schema_ready_sentinel() -> None:
    """Atomically write state/world_schema_ready.json after init_schema succeeds.

    B2 (2026-05-28): schema_version field now contains the content-hash fingerprint
    from architecture/_schema_fingerprint.txt instead of the legacy yaml version.
    world_schema_version.yaml deleted; yaml reader removed.
    """
    from src.config import state_path

    schema_fingerprint: str = "unknown_fingerprint"
    fingerprint_path = Path(__file__).parent.parent / "architecture" / "_schema_fingerprint.txt"
    if fingerprint_path.exists():
        try:
            schema_fingerprint = fingerprint_path.read_text().strip()
        except Exception:
            pass

    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": schema_fingerprint,
        "ingest_pid": os.getpid(),
        "init_schema_returned_ok": True,
    }
    path = state_path("world_schema_ready.json")
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)
    logger.info("Wrote world_schema_ready sentinel: schema_fingerprint=%s", schema_fingerprint)


def _world_schema_ready_sentinel_current() -> bool:
    """True when a prior successful world init already matches the pinned schema fingerprint."""
    from src.config import state_path

    fingerprint_path = Path(__file__).parent.parent / "architecture" / "_schema_fingerprint.txt"
    try:
        expected = fingerprint_path.read_text().strip()
        payload = json.loads(state_path("world_schema_ready.json").read_text())
    except Exception:
        return False
    return (
        bool(expected)
        and payload.get("schema_version") == expected
        and payload.get("init_schema_returned_ok") is True
    )


def _world_schema_current_lightweight() -> bool:
    """Read-only live-required world schema check for fast data-ingest restarts."""
    import sqlite3

    from src.state.db import ZEUS_WORLD_DB_PATH, assert_schema_current

    required_tables = frozenset(
        {
            "decision_events",
            "position_current",
            "trade_decisions",
        }
    )
    required_indexes = frozenset(
        {
            "idx_opportunity_events_day0_family_extreme",
            "idx_opportunity_event_processing_pending_retry_floor",
            "idx_opportunity_event_processing_stale_claim",
            "idx_opportunity_event_processing_status",
        }
    )
    try:
        uri = f"file:{ZEUS_WORLD_DB_PATH.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA busy_timeout=2000")
            assert_schema_current(conn)
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            indexes = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            return required_tables.issubset(tables) and required_indexes.issubset(indexes)
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("world schema lightweight probe failed: %s", exc)
        return False


def _world_schema_boot_requires_init() -> bool:
    if _world_schema_ready_sentinel_current():
        logger.info("init_schema skipped: current world_schema_ready sentinel matches pinned fingerprint")
        return False
    if _world_schema_current_lightweight():
        logger.info(
            "init_schema skipped: lightweight world schema probe passed; refreshing sentinel"
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Ingest tick functions
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_k2_daily_obs")
def _k2_daily_obs_tick():
    """K2 daily-observations tick (ingest daemon copy).

    Acquires advisory lock before running. If monolith holds lock, skips silently.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.daily_obs_append import daily_tick
    # K1 P0: observations is forecasts-class BUT _write_atom_with_coverage also
    # writes data_coverage (world-class) in the same SAVEPOINT.  Use the
    # ATTACH helper so bare table names resolve to the right physical DB.
    from src.state.db import get_forecasts_connection_with_world
    with acquire_lock("daily_obs") as acquired:
        if not acquired:
            logger.info("ingest k2_daily_obs_tick skipped_lock_held")
            return
        with get_forecasts_connection_with_world(write_class="bulk") as conn:
            result = daily_tick(conn)
    logger.info("K2 daily_obs_tick: %s", result)


@_scheduler_job("ingest_k2_hourly_instants")
def _k2_hourly_instants_tick():
    """K2 hourly Open-Meteo archive tick (ingest daemon copy).

    Acquires advisory lock before running.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.hourly_instants_append import hourly_tick
    from src.state.db import get_world_connection
    with acquire_lock("hourly_instants") as acquired:
        if not acquired:
            logger.info("ingest k2_hourly_instants_tick skipped_lock_held")
            return
        conn = get_world_connection(write_class="bulk")
        try:
            result = hourly_tick(conn)
        finally:
            conn.close()
    logger.info("K2 hourly_instants_tick: %s", result)


@_scheduler_job("ingest_k2_solar_daily")
def _k2_solar_daily_tick():
    """K2 daily sunrise/sunset refresh (ingest daemon copy).

    Acquires advisory lock before running.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.solar_append import daily_tick
    from src.state.db import get_world_connection
    with acquire_lock("solar_daily") as acquired:
        if not acquired:
            logger.info("ingest k2_solar_daily_tick skipped_lock_held")
            return
        conn = get_world_connection(write_class="bulk")
        try:
            result = daily_tick(conn)
        finally:
            conn.close()
    logger.info("K2 solar_daily_tick: %s", result)


@_scheduler_job("ingest_k2_forecasts_daily")
def _k2_forecasts_daily_tick():
    """K2 daily NWP forecasts refresh (ingest daemon copy).

    Acquires advisory lock before running.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.forecasts_append import daily_tick
    from src.state.db import get_world_connection
    with acquire_lock("forecasts_daily") as acquired:
        if not acquired:
            logger.info("ingest k2_forecasts_daily_tick skipped_lock_held")
            return
        conn = get_world_connection(write_class="bulk")
        try:
            result = daily_tick(conn)
        finally:
            conn.close()
    logger.info("K2 forecasts_daily_tick: %s", result)


@_scheduler_job("ingest_k2_hole_scanner")
def _k2_hole_scanner_tick():
    """K2 hole scanner daily patrol (ingest daemon copy).

    Acquires advisory lock before running.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.hole_scanner import HoleScanner
    from src.state.db import get_world_connection, get_forecasts_connection
    with acquire_lock("hole_scanner") as acquired:
        if not acquired:
            logger.info("ingest k2_hole_scanner_tick skipped_lock_held")
            return
        conn = get_world_connection(write_class="bulk")
        forecasts_conn = get_forecasts_connection()
        try:
            scanner = HoleScanner(conn, forecasts_conn=forecasts_conn)
            results = scanner.scan_all()
            for r in results:
                logger.info("K2 hole_scanner %s: %s", r.data_table.value, r.as_dict())
        finally:
            conn.close()
            forecasts_conn.close()


@_scheduler_job("ingest_k2_obs_tick")
def _k2_obs_tick():
    """Rolling 7-day live ingest for observation_instants (F44 fix).

    Fetches recent hourly observations for all WU_ICAO + OGIMET_METAR cities
    via the source-tier-correct clients and writes through the typed obs writer.
    HKO_NATIVE (Hong Kong) is handled by hko_ingest_tick.py --project-only.

    Runs hourly at minute=15, offset from hourly_instants (:07) and other ticks.
    Advisory lock 'obs' prevents concurrent runs from ingest_main restart.

    Renamed from _k2_obs_v2_tick / 'obs_v2' lock in the 2026-05-29
    observation_instants consolidation. Boot-guard lockstep: decorator id,
    add_job id (ingest_k2_obs), table_registry.get_job_id_matches mapping, and
    the db_table_ownership.yaml daemon_writer field all move together.
    """
    from src.data.dual_run_lock import acquire_lock
    from pathlib import Path

    with acquire_lock("obs") as acquired:
        if not acquired:
            logger.info("ingest k2_obs_tick skipped_lock_held")
            return
        import sys as _sys
        _REPO_ROOT = Path(__file__).resolve().parent.parent
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from scripts.obs_live_tick import run_live_tick
        from src.config import STATE_DIR
        # run_live_tick fetches upstream data lock-free and opens short
        # per-city db_writer_lock connections only for insert_rows + commit.
        # Do NOT create a second get_world_connection here.
        results = run_live_tick(days_back=7, db_path=STATE_DIR / "zeus-world.db")
        written = sum(r.rows_written for r in results if not r.skipped_hko)
        failed = [r.city for r in results if r.failure_reason]
        logger.info("K2 obs_tick: written=%d failed=%s", written, failed or "none")
        _raise_if_all_obs_tick_attempts_failed("ingest_k2_obs", results)


def _active_window_cities(now_utc: "datetime | None" = None) -> list[str]:
    """Return city names whose local time is in the intraday active window.

    Active window: local time is between 00:00 and peak_hour+6h (inclusive).
    This covers the entire period during which the running extreme can move
    and during which the day0 entry/monitor gate may query fresh observations.
    Cities outside this window (local middle of night) are skipped so the
    15-min fast tick does not issue unnecessary HTTP calls.

    Option-C per day0_obs_fastlane_plan §4.3.
    """
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    from src.config import cities_by_name as _cbn

    ref = (now_utc or _dt.now(__import__("datetime").timezone.utc))
    active: list[str] = []
    for city in _cbn.values():
        if not city.timezone:
            continue
        try:
            city_clock = ref.astimezone(_ZI(city.timezone))
            clock_hour = city_clock.hour + city_clock.minute / 60.0
            # Active window: [0, peak_hour + 6] local time.
            window_end = float(getattr(city, "historical_peak_hour", 14.0) or 14.0) + 6.0
            if 0.0 <= clock_hour <= window_end:
                active.append(city.name)
        except Exception:
            continue
    return active


@_scheduler_job("ingest_k2_obs_fast_tick")
def _k2_obs_fast_tick():
    """15-min fast ingest tick for observation_instants (Option C, day0_obs_fastlane_plan §4.3).

    Runs every 15 minutes (at :02/:17/:32/:47) — 4× finer than the hourly
    obs tick — for cities in the intraday active window (local time 00:00 to
    peak_hour+6h). Reduces observation_instants ingest lag from 50–135 min
    median to ~40–55 min by shrinking the polling-grid component from ±60 min
    to ±15 min. The WU 40-min publication floor remains (this tick does NOT
    beat the WU floor; only Option B's METAR fast lane does that).

    Connection discipline (three-phase law): run_live_tick fetches upstream
    data without a DB writer lock and opens short per-city db_writer_lock
    connections only for insert_rows + commit. This tick holds no DB connection
    across the HTTP fetch loop.

    Advisory lock "obs_fast": separate from "obs" (hourly tick) to avoid
    starving it. If the hourly tick is running when the fast tick fires the
    fast tick skips silently (not an error — the hourly tick is a superset).

    Boot-guard lockstep: decorator id "ingest_k2_obs_fast_tick",
    add_job id (spec) "ingest_k2_obs_fast_tick", no new db_table_ownership.yaml
    entry needed (supplemental writer to the existing observation_instants
    table whose daemon_writer is already ingest_k2_obs_tick).
    """
    from src.data.dual_run_lock import acquire_lock
    from pathlib import Path
    from datetime import datetime as _dt, timezone as _tz

    with acquire_lock("obs_fast") as acquired:
        if not acquired:
            logger.info("ingest k2_obs_fast_tick skipped_lock_held")
            return
        import sys as _sys
        _REPO_ROOT = Path(__file__).resolve().parent.parent
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from scripts.obs_live_tick import run_live_tick
        from src.config import STATE_DIR

        now_utc = _dt.now(_tz.utc)
        city_filter = _active_window_cities(now_utc)
        if not city_filter:
            logger.info("K2 obs_fast_tick: no cities in active window, skipping")
            return

        # Rotate the city order per tick: the fetch loop runs alphabetically and
        # upstream rate limiting truncates the TAIL of every run, so a fixed
        # order permanently starves the same tail cities (2026-06-12: two runs,
        # both failed exactly the post-cutoff alphabetic tail; Denver among
        # them on its settlement day). A 15-min rotation guarantees every city
        # reaches the front of the queue within len/step ticks.
        offset = (int(now_utc.timestamp()) // 900) % len(city_filter)
        city_filter = city_filter[offset:] + city_filter[:offset]

        results = run_live_tick(
            days_back=1,
            city_filter=city_filter,
            db_path=STATE_DIR / "zeus-world.db",
        )
        written = sum(r.rows_written for r in results if not r.skipped_hko)
        failed = [r.city for r in results if r.failure_reason]
        # Log the failure REASONS, not just names — two incident rounds were
        # spent re-deriving "rate limited" because only city names were logged.
        reasons = {r.city: str(r.failure_reason)[:80] for r in results if r.failure_reason}
        logger.info(
            "K2 obs_fast_tick: cities=%d written=%d failed=%s reasons=%s",
            len(city_filter), written, failed or "none", reasons or "none",
        )
        _raise_if_all_obs_tick_attempts_failed("ingest_k2_obs_fast_tick", results)


@_scheduler_job("ingest_day0_metar_source_clock")
def _day0_metar_source_clock_tick():
    """Capture newly published AWC METAR reports and emit moved Day0 extremes.

    The HTTP batch runs before any DB lock. Cold start loads the full observation
    window; steady-state polls request and merge only the recent publication
    delta. Unchanged reports perform no SQLite work. A new publication gets one
    short live-writer attempt; lock contention is bounded and retried every
    250ms without another HTTP fetch. The emitter does not acknowledge its
    publication identity until the ledger write succeeds.
    """
    from src.config import runtime_cities, settings
    from src.events.event_priority import day0_is_tradeable_for_scope
    from src.state.db import (
        get_world_connection_read_only,
    )

    edli_cfg = settings["edli"]
    if not (
        edli_cfg.get("enabled")
        and edli_cfg.get("event_writer_enabled")
        and edli_cfg.get("day0_extreme_trigger_enabled")
        and edli_cfg.get("day0_fast_obs_lane_enabled", True)
    ):
        return {"status": "DISABLED"}

    decision_time = datetime.now(timezone.utc)
    emitter = _day0_metar_emitter()
    cities = runtime_cities()
    if not emitter.ledger_report_keys_loaded():
        read_conn = get_world_connection_read_only()
        try:
            seeded_keys = emitter.sync_ledger_report_keys(
                read_conn,
                cities,
                as_of=decision_time,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "DAY0_METAR_SOURCE_CLOCK_DEFERRED reason=ledger_identity_sync_failed "
                "exc=%s: %s",
                type(exc).__name__,
                exc,
            )
            return {"status": "LEDGER_SYNC_FAILED"}
        finally:
            read_conn.close()
        logger.info("DAY0_METAR_LEDGER_IDENTITIES_SYNCED count=%d", seeded_keys)
    prefetch = emitter.prefetch(
        cities=cities,
        decision_time=decision_time,
        anomaly_check=None,
    )
    pending_reports = tuple(prefetch.ledger_reports or ())
    if not pending_reports:
        return {
            "status": "SOURCE_CURRENT",
            "freshness_status": prefetch.freshness_status,
            "reports": len(prefetch.reports),
        }
    _stage_day0_metar_commit(
        prefetch,
        received_at=decision_time.isoformat(),
        day0_is_tradeable=day0_is_tradeable_for_scope(
            str(edli_cfg.get("edli_live_scope") or "forecast_plus_day0")
        ),
    )
    return _commit_or_schedule_day0_metar(origin="source_clock")


@_scheduler_job("ingest_day0_metar_commit_retry")
def _day0_metar_commit_retry_tick():
    """Retry a pending canonical write once without repeating network I/O."""

    return _commit_or_schedule_day0_metar(origin="commit_retry")


@_scheduler_job("ingest_day0_oracle_anomaly")
def _day0_oracle_anomaly_tick():
    """Cross-check one cached METAR family without delaying source ingestion."""

    import sqlite3

    from src.config import runtime_cities, settings
    from src.data.day0_oracle_anomaly import (
        apply_day0_oracle_anomaly_action,
        wu_metar_anomaly_action,
    )
    from src.state.db import get_world_connection, world_write_mutex

    edli_cfg = settings["edli"]
    if not (
        edli_cfg.get("enabled")
        and edli_cfg.get("event_writer_enabled")
        and edli_cfg.get("day0_extreme_trigger_enabled")
        and edli_cfg.get("day0_fast_obs_lane_enabled", True)
    ):
        return {"status": "DISABLED"}

    actions = _day0_metar_emitter().cached_anomaly_actions(
        cities=runtime_cities(),
        decision_time=datetime.now(timezone.utc),
        anomaly_check=wu_metar_anomaly_action,
        max_cities=1,
    )
    if not actions:
        return {"status": "CURRENT"}

    conn = get_world_connection(write_class="live")
    write_budget_s = _day0_metar_write_budget_seconds()
    conn.execute(f"PRAGMA busy_timeout = {max(1, int(write_budget_s * 1000.0))}")
    mutex = world_write_mutex()
    acquired = mutex.acquire(timeout=write_budget_s)
    if not acquired:
        conn.close()
        return {"status": "WRITE_CONTENDED", "actions": len(actions)}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for action in actions:
            apply_day0_oracle_anomaly_action(action, conn=conn)
        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return {"status": "WRITE_CONTENDED", "actions": len(actions)}
        raise
    except BaseException:
        conn.rollback()
        raise
    finally:
        mutex.release()
        conn.close()

    logger.info("DAY0_ORACLE_ANOMALY_ACTIONS_COMMITTED count=%d", len(actions))
    return {"status": "COMMITTED", "actions": len(actions)}


def _raise_if_all_obs_tick_attempts_failed(job_id: str, results: list[object]) -> None:
    """Fail scheduler health when an obs tick made no successful city attempt."""

    attempted = [r for r in results if not bool(getattr(r, "skipped_hko", False))]
    if not attempted:
        return
    failed = [r for r in attempted if getattr(r, "failure_reason", None)]
    if len(failed) != len(attempted):
        return
    reasons = {
        str(getattr(r, "city", "<unknown>")): str(getattr(r, "failure_reason", ""))[:160]
        for r in failed[:10]
    }
    raise RuntimeError(
        f"{job_id} all attempted observation cities failed "
        f"(failed={len(failed)} sample_reasons={reasons})"
    )


@_scheduler_job("ingest_k2_hko_tick")
def _k2_hko_tick():
    """HKO hourly accumulator fetch + v2 projection for Hong Kong.

    Runs hourly at minute=30, offset from obs (:15) and harvester (:45).
    Decoupled from the trading daemon per operator directive 2026-04-23
    ("daemon-live和polymarket数据/天气数据采集本不应该混为一谈").

    scripts/hko_ingest_tick.py acquires db_writer_lock(BULK) only in its
    CLI main() path. When called via module-level functions (as done here),
    the lock is NOT acquired inside those functions — ingest_main is
    responsible for any lock coordination at this call site (currently
    guarded by acquire_lock("hko_tick") above). Called as functions to
    avoid subprocess overhead and inherit the daemon's DB path resolution.
    """
    from src.data.dual_run_lock import acquire_lock
    from pathlib import Path

    with acquire_lock("hko_tick") as acquired:
        if not acquired:
            logger.info("ingest k2_hko_tick skipped_lock_held")
            return
        _REPO_ROOT = Path(__file__).resolve().parent.parent
        from src.config import STATE_DIR
        db_path = STATE_DIR / "zeus-world.db"
        # Import the standalone script's two entry-point functions directly.
        # hko_ingest_tick.py is already in SQLITE_CONNECT_ALLOWLIST.
        import sys as _sys
        if str(_REPO_ROOT) not in _sys.path:
            _sys.path.insert(0, str(_REPO_ROOT))
        from scripts.hko_ingest_tick import (
            tick_accumulator,
            project_accumulator_to_v2,
            DEFAULT_LOG_PATH,
        )
        from src.state.db_writer_lock import WriteClass, db_writer_lock
        import os as _os
        import sqlite3 as _sqlite3
        data_version = "v1.wu-native"
        # CATEGORY ANTIBODY (Fitz #5): bare no-timeout connect was a lock-loser on
        # this BULK ingest write path. Apply the configured busy_timeout (ms→s) so
        # WAL contention WAITS instead of raising "database is locked".
        _busy_ms = int(_os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
        with db_writer_lock(db_path, WriteClass.BULK):
            conn = _sqlite3.connect(str(db_path), timeout=_busy_ms / 1000.0)
            conn.execute("PRAGMA busy_timeout = %d" % _busy_ms)
            try:
                tick_result = tick_accumulator(conn, DEFAULT_LOG_PATH)
                project_result = project_accumulator_to_v2(
                    conn, data_version, DEFAULT_LOG_PATH
                )
            finally:
                conn.close()
        logger.info(
            "K2 hko_tick: tick_ok=%s candidates=%s written=%s build_errors=%s",
            tick_result.get("tick_ok"),
            project_result.get("candidates"),
            project_result.get("written"),
            project_result.get("build_errors"),
        )
        return {**tick_result, **project_result}


# Staleness threshold for boot-time force-fetch.  A once-per-day cron
# (forecasts at 07:30 UTC, solar at 00:30 UTC) that was missed while the
# daemon was offline leaves the table stale.  If max captured_at / fetched_at
# is older than this many hours on boot, we call daily_tick immediately rather
# than waiting for the next scheduled cron.
_BOOT_FRESHNESS_THRESHOLD_HOURS = 18


@_scheduler_job("ingest_k2_startup_catch_up")
def _k2_startup_catch_up():
    """K2 boot-time hole filler — runs once at ingest daemon start.

    Two-phase:

    Phase 1 — hole filler (unchanged): fills MISSING/retry-ready FAILED rows
    for the last 30 days across all four K2 tables via catch_up_missing.

    Phase 2 — staleness guard (new): for each once-per-day table
    (forecasts, solar_daily) checks whether the most-recent row is older than
    _BOOT_FRESHNESS_THRESHOLD_HOURS.  If stale, calls daily_tick immediately
    so the live evaluator is never starved after an overnight outage.
    APScheduler coalesce=True correctly skips missed cron runs; this guard is
    the explicit catch-up path for that gap.
    """
    from src.data.daily_obs_append import catch_up_missing as catch_up_obs
    from src.data.hourly_instants_append import catch_up_missing as catch_up_hourly
    from src.data.solar_append import catch_up_missing as catch_up_solar
    from src.data.forecasts_append import catch_up_missing as catch_up_forecasts
    from src.data.forecasts_append import daily_tick as forecasts_daily_tick
    from src.data.solar_append import daily_tick as solar_daily_tick
    # K1 P0: observations is forecasts-class BUT catch_up_obs also writes
    # data_coverage (world-class) in the same SAVEPOINT.  Use the ATTACH helper
    # so bare table names resolve to the right physical DB.
    # Phase 2 staleness probes (forecasts table, data_coverage) stay on world conn.
    from src.state.db import get_world_connection, get_forecasts_connection_with_world  # K1 P0

    conn = get_world_connection(write_class="bulk")
    try:
        # ---- Phase 2 probe: capture staleness timestamps BEFORE Phase 1 ----
        # Phase 1 (catch_up_missing) can introduce fresh rows for historical
        # slots, causing MAX(captured_at/fetched_at) to look fresh even after
        # an overnight outage where the *daily* tick was missed.  Snapshot now
        # so Phase 2 can decide purely on pre-boot data.
        now_utc = datetime.now(timezone.utc)
        threshold_h = _BOOT_FRESHNESS_THRESHOLD_HOURS
        from dateutil.parser import parse as _parse_dt

        row = conn.execute(
            "SELECT MAX(captured_at) FROM forecasts"
        ).fetchone()
        _pre_phase1_max_captured = row[0] if row else None

        # Filter to status='WRITTEN' only — FAILED/MISSING rows also bump
        # fetched_at, which can falsely mask real data staleness.
        row = conn.execute(
            "SELECT MAX(fetched_at) FROM data_coverage"
            " WHERE data_table = 'solar_daily' AND status = 'WRITTEN'"
        ).fetchone()
        _pre_phase1_max_solar = row[0] if row else None

        # ---- Phase 1: hole filler (existing semantics, unchanged) -----------
        logger.info("K2 startup catch-up: observations")
        with get_forecasts_connection_with_world(write_class="bulk") as obs_conn:
            logger.info("  %s", catch_up_obs(obs_conn, days_back=30))
        logger.info("K2 startup catch-up: observation_instants")
        logger.info("  %s", catch_up_hourly(conn, days_back=30))
        logger.info("K2 startup catch-up: solar_daily")
        logger.info("  %s", catch_up_solar(conn, days_back=30))
        logger.info("K2 startup catch-up: forecasts")
        logger.info("  %s", catch_up_forecasts(conn, days_back=30))

        # ---- Phase 2: staleness guard for once-per-day tables ---------------
        # Uses pre-Phase-1 timestamps so catch-up backfills cannot mask gaps.

        # forecasts — has captured_at column written by the appender
        max_captured = _pre_phase1_max_captured
        if max_captured is None:
            staleness_h = float("inf")
        else:
            staleness_h = (now_utc - _parse_dt(max_captured)).total_seconds() / 3600
        if staleness_h > threshold_h:
            logger.warning(
                "forecasts stale (%.1fh > %dh threshold) on boot — forcing daily_tick",
                staleness_h, threshold_h,
            )
            from src.data.dual_run_lock import acquire_lock
            with acquire_lock("forecasts_daily") as acquired:
                if not acquired:
                    logger.info("boot-forced forecasts daily_tick skipped_lock_held")
                else:
                    result = forecasts_daily_tick(conn)
                    logger.info("boot-forced forecasts daily_tick: %s", result)
        else:
            logger.info(
                "forecasts fresh (%.1fh <= %dh threshold) — skipping boot force-fetch",
                staleness_h, threshold_h,
            )

        # solar_daily — no captured_at column; use data_coverage.fetched_at
        # (status='WRITTEN' only; FAILED/MISSING rows also bump fetched_at)
        max_solar_fetched = _pre_phase1_max_solar
        if max_solar_fetched is None:
            solar_staleness_h = float("inf")
        else:
            solar_staleness_h = (
                (now_utc - _parse_dt(max_solar_fetched)).total_seconds() / 3600
            )
        if solar_staleness_h > threshold_h:
            logger.warning(
                "solar_daily stale (%.1fh > %dh threshold) on boot — forcing daily_tick",
                solar_staleness_h, threshold_h,
            )
            from src.data.dual_run_lock import acquire_lock
            with acquire_lock("solar_daily") as acquired:
                if not acquired:
                    logger.info("boot-forced solar daily_tick skipped_lock_held")
                else:
                    result = solar_daily_tick(conn)
                    logger.info("boot-forced solar daily_tick: %s", result)
        else:
            logger.info(
                "solar_daily fresh (%.1fh <= %dh threshold) — skipping boot force-fetch",
                solar_staleness_h, threshold_h,
            )
    finally:
        conn.close()


@_scheduler_job("ingest_opendata_daily_mx2t6")
def _opendata_mx2t6_cycle():
    """ECMWF Open Data daily HIGH track ingest.

    Open Data ENS posts 00Z runs by ~07:00 UTC (latency 6-8h). This job runs
    at 07:30 UTC and writes ``ecmwf_opendata_mx2t3_local_calendar_day_max_v1``
    rows to ``ensemble_snapshots`` (post-2026-05-07 mx2t3 cutover; the
    schedule job name retains the legacy ``mx2t6`` slug for back-compat with
    ops dashboards).
    """
    result = _run_opendata_track("mx2t6_high")
    logger.info("ECMWF Open Data mx2t6: %s",
                {k: v for k, v in result.items() if k != "stages"})
    return result


@_scheduler_job("ingest_opendata_daily_mn2t6")
def _opendata_mn2t6_cycle():
    """ECMWF Open Data daily LOW track ingest.

    Runs at 07:35 UTC (5-min offset from the HIGH job to space out downloads).
    Writes ``ecmwf_opendata_mn2t3_local_calendar_day_min_v1`` rows to
    ``ensemble_snapshots`` (post-2026-05-07 mn2t3 cutover; the schedule
    job name retains the legacy ``mn2t6`` slug for back-compat with ops
    dashboards).
    """
    result = _run_opendata_track("mn2t6_low")
    logger.info("ECMWF Open Data mn2t6: %s",
                {k: v for k, v in result.items() if k != "stages"})
    return result


def _run_opendata_track(
    track: str,
    *,
    _locks_dir_override: Path | None = None,
    _collector=None,
) -> dict:
    """Legacy ingest-main OpenData wrapper kept mutually exclusive with forecast-live-daemon."""
    from src.data.dual_run_lock import acquire_opendata_track_lock
    from src.data.ecmwf_open_data import SOURCE_ID, collect_open_ens_cycle

    if _is_source_paused(SOURCE_ID):
        logger.info("_run_opendata_track(%s): paused_by_control_plane", track)
        return {"status": "paused_by_control_plane", "source": SOURCE_ID, "track": track}
    with acquire_opendata_track_lock(
        track,
        _locks_dir_override=_locks_dir_override,
    ) as (acquired, held_lock_key):
        if not acquired:
            logger.info(
                "_run_opendata_track(%s): skipped_lock_held key=%s",
                track,
                held_lock_key,
            )
            return {"status": "skipped_lock_held", "source": SOURCE_ID, "track": track}
        collector = _collector or collect_open_ens_cycle
        return collector(track=track)


@_scheduler_job("ingest_opendata_startup_catch_up")
def _opendata_startup_catch_up():
    """Boot-time catch-up for both Open Data tracks.

    Fires once at daemon start; pulls the latest release-calendar-approved
    full-horizon source run for both tracks. Re-runs after a brief restart are
    nearly idempotent thanks to ``INSERT OR IGNORE``.
    """
    for track in ("mx2t6_high", "mn2t6_low"):
        result = _run_opendata_track(track)
        logger.info("Open Data startup catch-up %s: %s", track,
                    {k: v for k, v in result.items() if k != "stages"})


@_scheduler_job("ingest_tigge_archive_backfill")
def _tigge_archive_backfill_cycle():
    """TIGGE MARS archive backfill (T-2 issue date) cycle.

    The TIGGE public archive has a 48-hour embargo (confirmed via
    confluence.ecmwf.int) so this job CANNOT serve same-day trading. It is
    a 2-day-lagged backfill that supplements the live Open Data feed and
    feeds the Platt training set.

    Schedule: 14:00 UTC daily — well after the embargo on (today - 2)'s 00Z
    has lifted. We pass ``target_date = today - 2 days`` so the pipeline
    always asks for a date the archive has already released.

    Honors control_plane pause_source ('tigge_mars'). On MARS credential
    failure, the cycle pauses itself so subsequent ticks short-circuit
    until operator restores credentials.
    """
    if _is_source_paused("tigge_mars"):
        logger.info("_tigge_archive_backfill_cycle: paused_by_control_plane")
        return {"status": "paused_by_control_plane", "source": "tigge_mars"}
    from src.data.tigge_pipeline import run_tigge_daily_cycle
    target = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    result = run_tigge_daily_cycle(target_date=target)
    logger.info("TIGGE archive backfill (target=%s): %s", target,
                {k: v for k, v in result.items() if k != "stages"})
    return result


@_scheduler_job("ingest_tigge_startup_catch_up")
def _tigge_startup_catch_up():
    """TIGGE archive boot-time catch-up.

    Fills any missed issue dates between MAX(issue_time) in the DB and
    ``today - 2 days``, capped at src.data.tigge_pipeline.MAX_LOOKBACK_DAYS.
    Anything within the 48-hour embargo window (i.e., today and yesterday)
    is intentionally skipped — that's the live-ingest pipeline's territory.
    """
    if _is_source_paused("tigge_mars"):
        logger.info("_tigge_startup_catch_up: paused_by_control_plane")
        return
    from src.data.tigge_pipeline import run_tigge_daily_cycle
    # determine_catch_up_dates internally returns up-to-yesterday but the
    # archive embargo means yesterday will fail the MARS request. Bound the
    # window explicitly to today-2 by passing the target_date for that day
    # when the catch-up dates list reduces to a single most-recent missing.
    result = run_tigge_daily_cycle()
    logger.info("TIGGE startup catch-up: %s", {k: v for k, v in result.items() if k != "stages"})


@_scheduler_job("ingest_etl_recalibrate")
def _etl_recalibrate():
    """Daily recalibration cycle (ingest daemon copy).

    Acquires advisory lock before running subprocess scripts.
    """
    from src.data.dual_run_lock import acquire_lock
    with acquire_lock("etl_recalibrate") as acquired:
        if not acquired:
            logger.info("ingest _etl_recalibrate skipped_lock_held")
            return
        _etl_recalibrate_body()


def _etl_recalibrate_body():
    """Inner body for ETL recalibration — shared with lock wrapper."""
    from src.state.db_writer_lock import WriteClass, subprocess_run_with_write_class
    venv_python = _etl_subprocess_python()
    scripts_dir = Path(__file__).parent.parent / "scripts"
    results = {}

    for script in [
        "etl_diurnal_curves.py",
        "etl_temp_persistence.py",
    ]:
        script_path = scripts_dir / script
        if script_path.exists():
            try:
                r = subprocess_run_with_write_class(
                    [venv_python, str(script_path)],
                    WriteClass.BULK,
                    capture_output=True, text=True, timeout=300,
                )
                # ANTI-SILENT-SINK (2026-06-09, same class as the materializer-queue fix):
                # capture_output=True swallowed every WARNING the ETL emitted on rc==0 —
                # a degradation antibody that warns into a void is structurally deaf.
                # Re-emit WARNING/ERROR lines at the daemon level (fail-soft).
                try:
                    for stream in (r.stderr or "", r.stdout or ""):
                        for line in stream.splitlines():
                            if "WARNING" in line or "ERROR" in line:
                                logger.warning("etl[%s] %s", script, line.strip()[:500])
                except Exception:
                    pass
                results[script] = "OK" if r.returncode == 0 else f"FAIL: {r.stderr[-200:]}"
            except Exception as e:
                results[script] = f"ERROR: {e}"

    results["calibration_pairs"] = "SKIP: run rebuild_calibration_pairs_canonical post-fillback"
    results["platt_refit"] = "SKIP: run explicit post-fillback canonical refit"

    # Replay is diagnostic, scans the complete historical WORLD DB, and writes
    # replay_results only after the scan. Running it inside the live data-ingest
    # daemon consumed a CPU and page cache for ten minutes every day while adding
    # no source truth. Keep scripts/run_replay.py operator/offline-only.
    results["replay_audit"] = "SKIP: operator_offline_only"

    logger.info("ETL recalibration: %s", results)


@_scheduler_job("ingest_harvester_truth_writer")
def _harvester_truth_writer_tick():
    """Phase 1.5 harvester split — ingest-side forecasts settlement writer.

    Acquires advisory lock before running. Runs hourly. Writes settlement truth
    to forecasts DB independent of the trading daemon's lifecycle.
    Feature-flagged: ZEUS_HARVESTER_LIVE_ENABLED must equal "1" to do real work.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.ingest.harvester_truth_writer import write_settlement_truth_for_open_markets
    from src.state.db import get_forecasts_connection
    with acquire_lock("harvester_truth") as acquired:
        if not acquired:
            logger.info("ingest harvester_truth_writer_tick skipped_lock_held")
            return
        conn = get_forecasts_connection(write_class="bulk")
        try:
            result = write_settlement_truth_for_open_markets(conn)
        finally:
            conn.close()
    logger.info("harvester_truth_writer_tick: %s", result)


@_scheduler_job("ingest_replacement_availability_poll")
def _replacement_availability_poll_tick():
    """Fast source-clock poll for replacement raw-input fetches.

    OPERATOR DIRECTIVE 2026-06-11 ("下载有自己的daemon"): weather downloading lives in
    the data-ingest daemon — ITS OWN download daemon — decoupled from forecast-live /
    live-trading restarts. The in-daemon forecast-live copy of this job kept dying with
    that daemon's restarts: a 10-40min extras pass with an end-of-pass insert was rolled
    back to zero three times in one morning. data-ingest is restart-quiet, so the pass
    survives. Fail-soft: any error logs and the next tick retries; every lane it calls
    is idempotent per persisted row/manifest.
    """
    from src.data.replacement_forecast_production import (  # noqa: PLC0415
        _download_bayes_precision_fusion_source_clock_raw_inputs_if_needed,
        _download_replacement_forecast_current_targets_if_needed,
        _enqueue_cycle_advance_reseeds_if_needed,
        _enqueue_fusion_upgrade_reseeds_if_needed,
        _replacement_forecast_live_materialization_queue_config,
    )
    from src.data.bayes_precision_fusion_download import (  # noqa: PLC0415
        bayes_precision_fusion_quota_cooldown_seconds,
    )
    from src.data.source_clock_update_probe import (  # noqa: PLC0415
        advance_source_clock_cursor,
        probe_openmeteo_source_clock_updates,
        source_clock_scoped_download_cursor_sources,
    )

    cfg = _replacement_forecast_live_materialization_queue_config()
    if not bool(cfg.get("download_current_targets_enabled", False)):
        return None
    cooldown_seconds = bayes_precision_fusion_quota_cooldown_seconds()
    if cooldown_seconds > 0:
        # No source-clock payload can land during provider cooldown. Re-probing
        # the unchanged metadata cursor only rediscovers the same blocked work.
        _defer_replacement_maintenance(float(cooldown_seconds))
        report = {
            "status": "SOURCE_CLOCK_BPF_SCOPED_QUOTA_COOLDOWN_SKIPPED",
            "cooldown_seconds": cooldown_seconds,
            "reseed_maintenance_status": "RESEED_MAINTENANCE_NOT_DUE",
        }
        logger.info("replacement source-clock quota cooldown: %s", report)
        return report

    def _compact_current_target_report(download_report):
        if not isinstance(download_report, dict):
            return None
        compact = {
            "status": download_report.get("status"),
            "available_cycle": download_report.get("available_cycle"),
            "downloaded_cycle": download_report.get("downloaded_cycle"),
            "candidate_row_count": download_report.get("candidate_row_count"),
            "written_row_count": download_report.get("written_row_count"),
            "target_count": download_report.get("target_count"),
            "timeout_seconds": download_report.get("timeout_seconds"),
            "timeboxed_incomplete": download_report.get("timeboxed_incomplete"),
            "unattempted_target_count": download_report.get("unattempted_target_count"),
            "max_wall_clock_seconds": download_report.get("max_wall_clock_seconds"),
            "error": download_report.get("error"),
        }
        coverage = download_report.get("coverage")
        if isinstance(coverage, dict):
            compact["coverage"] = {
                key: coverage.get(key)
                for key in (
                    "status",
                    "target_count",
                    "covered_count",
                    "missing_coverage_count",
                    "can_seed_count",
                    "missing_openmeteo_manifest_count",
                    "day0_observed_extreme_required_count",
                )
            }
        errors = download_report.get("transport_errors")
        if errors:
            compact["transport_errors"] = tuple(errors)[:3]
        return {k: v for k, v in compact.items() if v is not None}

    def _attach_reseed_reports(
        report: dict[str, object],
        *,
        scopes: tuple[tuple[str, str, str], ...] | None = None,
        changed_sources: tuple[str, ...] | None = None,
        include_cycle_advance: bool = True,
    ) -> dict[str, object]:
        upgrade_report = (
            _enqueue_fusion_upgrade_reseeds_if_needed(cfg)
            if scopes is None
            else _enqueue_fusion_upgrade_reseeds_if_needed(
                cfg,
                scopes=scopes,
                changed_sources=changed_sources,
            )
        )
        if upgrade_report is not None:
            report["fusion_upgrade_status"] = upgrade_report.get("status")
            report["fusion_upgrade_seeds_enqueued"] = upgrade_report.get("seeds_enqueued")
        if not include_cycle_advance:
            return report
        cycle_advance_report = (
            _enqueue_cycle_advance_reseeds_if_needed(cfg)
            if scopes is None
            else _enqueue_cycle_advance_reseeds_if_needed(cfg, scopes=scopes)
        )
        if cycle_advance_report is not None:
            report["cycle_advance_status"] = cycle_advance_report.get("status")
            report["cycle_advance_seeds_enqueued"] = cycle_advance_report.get("seeds_enqueued")
            if cycle_advance_report.get("advances_detected"):
                report["cycle_advance_detail"] = {
                    k: cycle_advance_report.get(k)
                    for k in (
                        "freshest_materializable_cycle",
                        "scopes_checked",
                        "advances_detected",
                        "held_advances_detected",
                        "seeds_enqueued",
                        "held_seeds_enqueued",
                        "already_enqueued",
                        "manifest_missing",
                        "leg_artifact_missing",
                        "family_cycle_missing",
                        "family_cycle_not_newer",
                        "day0_skipped",
                        "comparison_failed",
                        "family_scope_check_failed",
                        "seed_build_failed",
                        "enqueued",
                    )
                }
        return report

    def _download_current_targets():
        current_target_timeout = _replacement_current_target_poll_timeout_seconds(
            _replacement_availability_poll_seconds()
        )
        try:
            return _download_replacement_forecast_current_targets_if_needed(
                cfg, max_wall_clock_seconds=current_target_timeout
            )
        except TimeoutError as exc:
            report = {
                "status": "CURRENT_TARGET_DOWNLOAD_TIMEOUT",
                "timeout_seconds": current_target_timeout,
                "error": str(exc)[:240],
            }
            logger.warning("replacement current-target download timeboxed: %s", report)
            return report
        except Exception as exc:  # noqa: BLE001 - source-clock/reseed must still run.
            logger.warning(
                "replacement current-target download failed fail-soft: %s",
                exc,
                exc_info=True,
            )
            return {
                "status": "CURRENT_TARGET_DOWNLOAD_FAILSOFT",
                "error": f"{type(exc).__name__}: {str(exc)[:220]}",
            }

    # The public source clock owns this latency path. Generic target repair may
    # consume most of the poll cadence, so it must never delay detecting a new run.
    source_clock_report = probe_openmeteo_source_clock_updates(advance_cursor=False)
    source_clock_payload = source_clock_report.as_dict()
    if not source_clock_report.updated_sources:
        report: dict[str, object] = {
            "status": "SOURCE_CLOCK_POLL_CURRENT",
            "source_clock_status": source_clock_payload.get("status"),
            "source_clock_updated_sources": source_clock_payload.get("updated_sources", []),
            "source_clock_affected_cities": source_clock_payload.get("affected_cities", []),
            "source_clock_error": source_clock_payload.get("error"),
        }
        if _replacement_maintenance_due():
            current_target_download_compact = _compact_current_target_report(
                _download_current_targets()
            )
            if current_target_download_compact is not None:
                report["current_target_download"] = current_target_download_compact
            report = _attach_reseed_reports(report)
        else:
            report["current_target_download"] = {
                "status": "CURRENT_TARGET_MAINTENANCE_NOT_DUE"
            }
        logger.info("replacement source-clock poll current: %s", report)
        return report
    logger.info("replacement source-clock update detected; running download path: %s", source_clock_payload)
    notified_source_scopes: set[tuple[str, str, str, str]] = set()
    anchor_scopes_attempted: set[tuple[str, str, str]] = set()
    fallback_reseed_published = False

    def _publish_committed_source(
        source: str,
        task_report: object,
    ) -> None:
        nonlocal fallback_reseed_published
        raw_scopes = (
            task_report.get("committed_families", ())
            if isinstance(task_report, dict)
            else ()
        )
        scopes = tuple(
            scope
            for scope in (
                (str(city), str(target_date), str(metric))
                for city, target_date, metric in raw_scopes
            )
            if (source, *scope) not in notified_source_scopes
        )
        if scopes:
            notified_source_scopes.update((source, *scope) for scope in scopes)
        elif fallback_reseed_published:
            return
        report = {
            "status": "SOURCE_CLOCK_PARTIAL_RAW_INPUTS_COMMITTED",
            "source": source,
            "written_row_count": (
                task_report.get("written_row_count")
                if isinstance(task_report, dict)
                else None
            ),
            "committed_family_count": len(scopes),
        }
        if scopes:
            anchor_scopes = tuple(
                scope for scope in scopes if scope not in anchor_scopes_attempted
            )
            anchor_scopes_attempted.update(anchor_scopes)
            if anchor_scopes:
                anchor_report = _download_replacement_forecast_current_targets_if_needed(
                    cfg,
                    max_wall_clock_seconds=min(
                        10.0,
                        _replacement_current_target_poll_timeout_seconds(
                            _replacement_availability_poll_seconds()
                        ),
                    ),
                    required_scopes=anchor_scopes,
                )
                if isinstance(anchor_report, dict):
                    report["anchor_scope_status"] = anchor_report.get("status")
                    report["anchor_scope_manifest_count"] = anchor_report.get(
                        "written_manifest_count"
                    )
            _attach_reseed_reports(
                report,
                scopes=scopes,
                changed_sources=(source,),
            )
        else:
            _attach_reseed_reports(report)
            fallback_reseed_published = True
        logger.info(
            "replacement source-clock committed families published reseeds: %s",
            report,
        )

    report = _download_bayes_precision_fusion_source_clock_raw_inputs_if_needed(
        cfg,
        source_clock_report=source_clock_report,
        max_wall_clock_seconds=_replacement_source_clock_download_budget_seconds(
            _replacement_availability_poll_seconds()
        ),
        on_source_commit=_publish_committed_source,
    )
    if report is None:
        report = {
            "status": "SOURCE_CLOCK_SCOPED_DOWNLOAD_SKIPPED",
            "source_clock_status": source_clock_payload.get("status"),
            "source_clock_updated_sources": source_clock_payload.get("updated_sources", []),
            "source_clock_affected_cities": source_clock_payload.get("affected_cities", []),
            "source_clock_error": source_clock_payload.get("error"),
        }
    # No raw input can land while the provider quota is cooling down. Run one
    # catch-up scan, then suppress identical JSON-heavy reseed scans until the
    # downloader can make progress again.
    if (
        report.get("status")
        == "SOURCE_CLOCK_BPF_SCOPED_QUOTA_COOLDOWN_SKIPPED"
    ):
        if _replacement_maintenance_due():
            report = _attach_reseed_reports(report)
        else:
            report["reseed_maintenance_status"] = (
                "RESEED_MAINTENANCE_NOT_DUE"
            )
        _defer_replacement_maintenance(
            float(report.get("cooldown_seconds") or 0)
        )
    else:
        # Publish changed-source work before generic current-target maintenance.
        # The forecast materializer can consume these idempotent seeds immediately.
        report = _attach_reseed_reports(report)
    cursor_sources = source_clock_scoped_download_cursor_sources(report)
    advanced_sources = (
        advance_source_clock_cursor(source_clock_report, sources=cursor_sources)
        if cursor_sources
        else ()
    )
    report["source_clock_cursor_advanced_sources"] = advanced_sources
    report["source_clock_cursor_deferred_sources"] = tuple(
        sorted(set(source_clock_report.updated_sources) - set(advanced_sources))
    )
    logger.info("replacement source-clock scoped download report: %s", report)
    return report


@_scheduler_job("ingest_automation_analysis")
def _automation_analysis_cycle():
    """Daily automation analysis diagnostic (ingest daemon copy)."""
    import subprocess
    venv_python = _etl_subprocess_python()
    script = Path(__file__).parent.parent / "scripts" / "automation_analysis.py"
    r = subprocess.run(
        [venv_python, str(script)],
        capture_output=True, text=True, timeout=60,
    )
    output = r.stdout.strip()
    if output:
        logger.info("[automation_analysis]\n%s", output)
    if r.returncode != 0 and r.stderr:
        logger.warning("[automation_analysis] errors: %s", r.stderr[-300:])


# ---------------------------------------------------------------------------
# Phase 2: Source health probe (§2.1) — appended END of scheduled-jobs section
# ---------------------------------------------------------------------------

# Lock-contention retry budget for the ALL-source probe. The forecast-live
# daemon refreshes its OpenData subset under the SAME "source_health" advisory
# lock on the SAME 10-minute cadence; a single non-retried skip here permanently
# STARVES the all-source probe — and open_meteo_archive / wu_pws are refreshed
# ONLY here, so their last_success_at never advances, drifts > 6h stale, and the
# boot freshness gate disables DAY0_CAPTURE (killing the entire settlement-day
# edge lane). The contending forecast-live write is sub-second, so retry briefly
# instead of abandoning the cycle. ~6 × 2.5s = up to 15s, well under the 10-min
# interval. (2026-06-14 day0-edge-lane revival.)
_SOURCE_HEALTH_LOCK_RETRIES = 6
_SOURCE_HEALTH_LOCK_RETRY_SLEEP_S = 2.5


@_scheduler_job("ingest_source_health_probe")
def _source_health_probe_tick():
    """Source health probe every 10 minutes (design §2.1).

    Probes all upstream sources and writes state/source_health.json.
    Acquires advisory lock so only one process probes at a time — retrying
    briefly on contention (see ``_SOURCE_HEALTH_LOCK_RETRIES``) rather than
    abandoning the cycle, because this is the SOLE refresher of
    open_meteo_archive / wu_pws and a skipped cycle starves DAY0_CAPTURE.
    """
    import time as _time
    from src.data.dual_run_lock import acquire_lock
    from src.data.source_health_probe import probe_all_sources, write_source_health
    from src.config import state_path
    import json
    from pathlib import Path as _Path

    for _attempt in range(_SOURCE_HEALTH_LOCK_RETRIES):
        with acquire_lock("source_health") as acquired:
            if acquired:
                # Load prior state for accumulation of consecutive_failures
                prior_state: dict = {}
                try:
                    existing = state_path("source_health.json")
                    if _Path(existing).exists():
                        data = json.loads(_Path(existing).read_text())
                        prior_state = data.get("sources", {})
                except Exception:
                    pass

                results = probe_all_sources(10.0, _prior_state=prior_state)
                write_source_health(results)
                logger.info("Source health probe complete: %d sources", len(results))
                return
        # Lock released by the context exit; the contending forecast-live
        # OpenData refresh holds it for < 1s — wait and retry so the all-source
        # probe is not starved into staleness.
        _time.sleep(_SOURCE_HEALTH_LOCK_RETRY_SLEEP_S)

    logger.warning(
        "ingest _source_health_probe_tick skipped_lock_held after %d retries "
        "(open_meteo_archive/wu_pws refresh starved -> DAY0_CAPTURE freshness risk)",
        _SOURCE_HEALTH_LOCK_RETRIES,
    )


# ---------------------------------------------------------------------------
# 2026-05-01: Station-migration drift probe (Invariant F)
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_station_migration_probe")
def _station_migration_probe_tick():
    """Hourly drift probe — gamma resolutionSource vs. cities.json::wu_station.

    Writes ``state/station_migration_alerts.json`` and bumps the per-city
    primary-source ``degraded_since`` on a mismatch. Never auto-rewrites
    cities.json — operator approves migrations consciously.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.station_migration_probe import run_probe

    with acquire_lock("station_migration_probe") as acquired:
        if not acquired:
            logger.info("ingest _station_migration_probe_tick skipped_lock_held")
            return
        result = run_probe()
        logger.info("Station-migration probe: %s",
                    {k: v for k, v in result.items() if k != "alerts"})


# ---------------------------------------------------------------------------
# Phase 2: Drift detector for Platt (§2.2) — appended END
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_drift_detector")
def _drift_detector_tick():
    """Daily drift detector for Platt refit (design §2.2).

    Runs at UTC 06:00 (before K2 forecasts tick at 07:30) so refit can
    happen overnight. Writes state/refit_armed.json.
    Acquires advisory lock.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.calibration.drift_refit_arm import check_and_arm_refit
    from src.state.db import get_world_connection

    with acquire_lock("drift_detector") as acquired:
        if not acquired:
            logger.info("ingest _drift_detector_tick skipped_lock_held")
            return
        conn = get_world_connection(write_class="bulk")
        try:
            result = check_and_arm_refit(conn)
            logger.info(
                "Drift detector: %d REFIT_NOW, %d WATCH, %d OK",
                result["n_refit_now"], result["n_watch"], result["n_ok"],
            )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Task #2 (2026-05-07): UMA Optimistic Oracle resolution listener tick
# ---------------------------------------------------------------------------

# Default block-window per tick when no cursor exists yet (operator can override
# via settings["uma"]["initial_lookback_blocks"]). Polygon mints ~2 blocks/sec
# → 50 000 blocks ≈ 7h, comfortably wider than UMA's ~14h post-endDate settle
# latency window for any single tick, but bounded so eth_getLogs does not scan
# from genesis (PR #82 Copilot review: from_block=0 every tick scans full chain).
_UMA_DEFAULT_INITIAL_LOOKBACK_BLOCKS = 50_000
# Max blocks to advance per tick once cursor exists. Provider-friendly chunking;
# any backlog drains over multiple ticks while keeping each request bounded.
_UMA_MAX_BLOCKS_PER_TICK = 100_000
# Once the cursor passes era_end_block, the UMA era is exhausted for this process: latch so
# subsequent ticks return immediately without repeating the eth_blockNumber RPC + DB open
# (PR review #329). Reset only on process restart; default-off path never sets it.
_uma_era_exhausted = False


def _uma_optional_settings() -> tuple[str, str, int, int]:
    """Read optional uma config without touching ``Settings._data`` private state.

    Returns ``(polygon_rpc_url, oo_contract_address, initial_lookback, max_per_tick)``.
    Empty strings / 0 means "not configured" — caller treats as default-OFF.
    """
    from src.config import settings

    try:
        uma_cfg = settings["uma"]
    except KeyError:
        return ("", "", _UMA_DEFAULT_INITIAL_LOOKBACK_BLOCKS, _UMA_MAX_BLOCKS_PER_TICK)
    if not isinstance(uma_cfg, dict):
        return ("", "", _UMA_DEFAULT_INITIAL_LOOKBACK_BLOCKS, _UMA_MAX_BLOCKS_PER_TICK)
    return (
        str(uma_cfg.get("polygon_rpc_url", "") or ""),
        str(uma_cfg.get("oo_contract_address", "") or ""),
        int(uma_cfg.get("initial_lookback_blocks", _UMA_DEFAULT_INITIAL_LOOKBACK_BLOCKS)),
        int(uma_cfg.get("max_blocks_per_tick", _UMA_MAX_BLOCKS_PER_TICK)),
    )


def _uma_era_end_block() -> int:
    """Optional UMA-era end block (PR5 data_temporal_kernel).

    UMA OO V2 weather settlement is HISTORICAL: post-2026-02-21 Polymarket uses the internal
    automatic resolver (Gamma is canonical). Scanning Polygon past the UMA era wastes RPC
    budget on blocks that can hold no relevant UMA settle event. When the operator configures
    ``settings["uma"]["era_end_block"]`` (> 0), the listener will not scan past it.

    Returns 0 when unconfigured — the listener then behaves EXACTLY as before (no era cap).
    """
    from src.config import settings

    try:
        uma_cfg = settings["uma"]
    except KeyError:
        return 0
    if not isinstance(uma_cfg, dict):
        return 0
    try:
        return max(0, int(uma_cfg.get("era_end_block", 0) or 0))
    except (TypeError, ValueError):
        return 0


@_scheduler_job("ingest_uma_resolution_listener")
def _uma_resolution_listener_tick():
    """Poll Polygon RPC for UMA OO Settle events — 5-min interval.

    Reads condition_ids from market_events, then calls poll_uma_resolutions
    with the configured RPC client. When settings["uma"]["polygon_rpc_url"] is
    absent or empty, the listener short-circuits (returns [] without writing)
    per the default-OFF design in uma_resolution_listener.py.

    Block window: the listener uses a persisted last-scanned-block cursor
    (``uma_resolution_cursor`` table) to scan only new blocks per tick. First
    tick after enabling: scans the most-recent ``initial_lookback_blocks``
    (default 50 000 ≈ 7h on Polygon). Subsequent ticks advance the cursor and
    cap the per-tick window at ``max_blocks_per_tick`` (default 100 000) so
    backlogged ticks drain incrementally without blowing past RPC log limits.

    Runs on "fast" executor: reads on-chain (HTTP), writes at most 1 row per
    resolved market — no risk of DB writer starvation against the single-writer
    default executor pool. Condition_id lookup uses a fresh read-only connection
    that does not block writers.
    """
    global _uma_era_exhausted
    # Era already exhausted this process — skip the RPC + DB open entirely (PR review #329).
    if _uma_era_exhausted:
        return

    from src.state.uma_resolution_listener import (
        UmaHttpRpcClient,
        get_last_scanned_block,
        poll_uma_resolutions,
        run_late_revalidation_pass,
        set_last_scanned_block,
    )
    from src.state.db import get_world_connection, ZEUS_FORECASTS_DB_PATH
    import sqlite3

    # Load optional uma settings (default-OFF when absent).
    try:
        polygon_rpc_url, oo_contract_address, initial_lookback, max_per_tick = (
            _uma_optional_settings()
        )
    except Exception as exc:
        logger.warning("ingest_uma_resolution_listener: settings load failed: %s", exc)
        return

    if not polygon_rpc_url or not oo_contract_address:
        logger.debug(
            "ingest_uma_resolution_listener: no RPC config; listener is default-OFF "
            "(set settings.uma.polygon_rpc_url + oo_contract_address to activate)"
        )
        return

    # Collect tracked condition_ids from market_events (read-only, forecasts DB post-K1).
    condition_ids: list[str] = []
    try:
        ro_conn = sqlite3.connect(str(ZEUS_FORECASTS_DB_PATH), timeout=10)
        ro_conn.row_factory = sqlite3.Row
        try:
            rows = ro_conn.execute(
                "SELECT DISTINCT condition_id FROM market_events "
                "WHERE condition_id IS NOT NULL AND condition_id != ''"
            ).fetchall()
            condition_ids = [str(r["condition_id"]) for r in rows]
        finally:
            ro_conn.close()
    except Exception as exc:
        logger.warning("ingest_uma_resolution_listener: condition_id fetch failed: %s", exc)
        return

    if not condition_ids:
        logger.debug("ingest_uma_resolution_listener: no tracked condition_ids yet")
        return

    # Resolve block window via persisted cursor + RPC head, then poll.
    try:
        rpc_client = UmaHttpRpcClient(polygon_rpc_url)

        # eth_blockNumber — head of chain.
        head_block: int | None = None
        try:
            import httpx  # type: ignore[import]
            resp = httpx.post(
                polygon_rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
                timeout=10.0,
            )
            resp.raise_for_status()
            head_hex = resp.json().get("result")
            if isinstance(head_hex, str):
                head_block = int(head_hex, 16)
        except Exception as exc:  # noqa: BLE001 — fail-soft to skip-tick
            logger.warning("ingest_uma_resolution_listener: eth_blockNumber failed: %s", exc)
            return

        if not head_block or head_block <= 0:
            logger.warning("ingest_uma_resolution_listener: invalid head_block=%r", head_block)
            return

        write_conn = get_world_connection()
        try:
            cursor = get_last_scanned_block(write_conn, oo_contract_address)
            if cursor is None:
                from_block = max(head_block - initial_lookback, 0)
            else:
                from_block = cursor + 1
            to_block = min(from_block + max_per_tick - 1, head_block)

            # PR5 era guard: UMA OO V2 is historical (pre-2026-02-21 cutover to Gamma).
            # When era_end_block is configured, never scan past it. era_end_block=0 disables
            # the guard (behavior-identical to pre-PR5).
            era_end_block = _uma_era_end_block()
            if era_end_block > 0:
                if from_block > era_end_block:
                    _uma_era_exhausted = True   # latch: no RPC+DB on subsequent ticks
                    logger.info(
                        "ingest_uma_resolution_listener: from_block=%s past era_end_block=%s; "
                        "UMA era exhausted — latching off for this process",
                        from_block, era_end_block,
                    )
                    return
                to_block = min(to_block, era_end_block)

            if to_block < from_block:
                logger.debug(
                    "ingest_uma_resolution_listener: nothing to scan (cursor=%s head=%s)",
                    cursor, head_block,
                )
                return

            resolutions = poll_uma_resolutions(
                condition_ids=condition_ids,
                contract_address=oo_contract_address,
                rpc_client=rpc_client,
                conn=write_conn,
                from_block=from_block,
                to_block=to_block,
            )
            # Late-revalidation pass: check tentative rows (confirmations < required)
            # against the chain. Any reorged rows are marked is_valid=0 so they
            # cannot be used as settlement evidence via lookup_resolution().
            invalidated = run_late_revalidation_pass(write_conn, rpc_client=rpc_client)
            if invalidated:
                logger.warning(
                    "ingest_uma_resolution_listener: %d tentative row(s) invalidated "
                    "by late-revalidation pass (probable Polygon reorg)",
                    invalidated,
                )
            # Advance cursor regardless of resolution count — empty windows are
            # legitimate progress and re-scanning them wastes RPC budget.
            set_last_scanned_block(write_conn, oo_contract_address, to_block)
            write_conn.commit()
            if resolutions:
                logger.info(
                    "ingest_uma_resolution_listener: %d new resolution(s) "
                    "(blocks %d→%d, head=%d)",
                    len(resolutions), from_block, to_block, head_block,
                )
            else:
                logger.debug(
                    "ingest_uma_resolution_listener: no new resolutions "
                    "(blocks %d→%d, head=%d)",
                    from_block, to_block, head_block,
                )
        finally:
            write_conn.close()
    except Exception as exc:
        logger.warning("ingest_uma_resolution_listener tick error: %s", exc)


# ---------------------------------------------------------------------------
# Task #4 (2026-05-07): forecast_skill ETL scheduler tick
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_etl_forecast_skill")
def _etl_forecast_skill_tick():
    """Daily materialization of forecast_skill + model_bias from local forecasts table.

    Runs scripts/etl_forecast_skill_from_forecasts.py as a subprocess so it
    inherits the venv Python and produces its own log output. Idempotent — the
    script uses INSERT OR REPLACE; repeated runs are safe.

    Runs on default executor (it opens a write connection to zeus-world.db).
    """
    from src.state.db_writer_lock import WriteClass, subprocess_run_with_write_class
    venv_python = _etl_subprocess_python()
    script = Path(__file__).parent.parent / "scripts" / "etl_forecast_skill_from_forecasts.py"
    if not script.exists():
        logger.warning("ingest_etl_forecast_skill: script not found at %s", script)
        return
    r = subprocess_run_with_write_class(
        [venv_python, str(script)],
        WriteClass.BULK,
        capture_output=True, text=True, timeout=300,
    )
    output = r.stdout.strip()
    if output:
        logger.info("[etl_forecast_skill]\n%s", output[-2000:])
    if r.returncode != 0:
        logger.warning(
            "[etl_forecast_skill] FAILED (exit=%d): %s",
            r.returncode, r.stderr[-500:] if r.stderr else "",
        )
    else:
        logger.info("[etl_forecast_skill] OK (exit=0)")


# ---------------------------------------------------------------------------
# STALE fix (2026-05-07): market_events scan tick — feeds from Gamma API
# so ingest daemon populates market_events when trading daemon is down.
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_market_scan")
def _market_scan_tick():
    """Periodic Gamma API market scan to keep market_events fresh.

    find_weather_markets() calls _persist_market_events_to_db internally; it
    is idempotent (INSERT OR IGNORE on (market_slug, condition_id)).
    Running this from the ingest daemon ensures market_events stays updated
    even when the trading daemon (src/main.py) is paused.

    Runs on default executor (writes to zeus-forecasts.db via _persist_market_events_to_db).
    """
    try:
        from src.data.market_scanner import (
            MarketEventsPersistenceError,
            find_weather_markets_or_raise,
        )
        markets = find_weather_markets_or_raise()
        logger.info("ingest_market_scan: found %d active weather markets", len(markets))
        return {
            "status": "ok",
            "market_count": len(markets),
        }
    except MarketEventsPersistenceError as exc:
        logger.warning("ingest_market_scan persistence failure: %s", exc)
        return {
            "status": "market_events_persistence_failed",
            "error": exc.persistence_error or str(exc),
        }
    except Exception as exc:
        logger.warning("ingest_market_scan tick error: %s", exc)
        return {"status": "market_scan_failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 2: Ingest status rollup (§2.5) — appended END
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_status_rollup")
def _ingest_status_rollup_tick():
    """Ingest status rollup every 5 minutes (design §2.5).

    Writes state/ingest_status.json. Also called post-K2 tick completion
    (see write_ingest_status calls in K2 ticks below).
    Acquires advisory lock.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.ingest_status_writer import write_ingest_status
    from src.state.db import get_world_connection

    with acquire_lock("ingest_status") as acquired:
        if not acquired:
            logger.info("ingest _ingest_status_rollup_tick skipped_lock_held")
            return
        conn = get_world_connection(write_class="bulk")
        try:
            write_ingest_status(conn)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# F35: Oracle bridge tick — daily 10:05 UTC
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_oracle_bridge")
def _bridge_oracle_tick():
    """F35: Run bridge_oracle_to_calibration.py daily at 10:05 UTC.

    Eliminates the cross-repo cron entry that would otherwise be required in
    ~/.openclaw/cron/jobs.json.  The bridge script writes data/oracle_error_rates.json
    (file-only, no DB write) so plain subprocess.run is sufficient — no write-class
    lock needed.  Script is idempotent; repeated runs are safe.

    Runs on default executor (low frequency; subprocess, not DB writer).
    """
    _run_bridge_oracle_script()


def _run_bridge_oracle_script() -> str:
    """Run the oracle bridge subprocess once."""
    if not _ORACLE_BRIDGE_LOCK.acquire(blocking=False):
        logger.info("[BRIDGE_ORACLE_TICK] skipped lock_held")
        return "skipped_lock_held"
    try:
        venv_python = _etl_subprocess_python()
        script = Path(__file__).parent.parent / "scripts" / "bridge_oracle_to_calibration.py"
        if not script.exists():
            logger.warning("ingest_oracle_bridge: script not found at %s", script)
            return "missing_script"
        import subprocess
        r = subprocess.run(
            [venv_python, str(script)],
            capture_output=True, text=True, timeout=300,
        )
        stdout_tail = r.stdout[-500:] if r.stdout else ""
        if r.returncode != 0:
            logger.warning(
                "[BRIDGE_ORACLE_TICK] FAILED (exit=%d): %s",
                r.returncode, r.stderr[-500:] if r.stderr else "",
            )
            return "failed_subprocess"
        logger.info("[BRIDGE_ORACLE_TICK] OK (exit=0) stdout=%r", stdout_tail)
        return "ok"
    except Exception:
        logger.exception("[BRIDGE_ORACLE_TICK] FAILED exception")
        return "failed_exception"
    finally:
        _ORACLE_BRIDGE_LOCK.release()


# ---------------------------------------------------------------------------
# Oracle snapshot tick — daily 10:00 UTC (5 min before the bridge)
# ---------------------------------------------------------------------------

@_scheduler_job("ingest_oracle_snapshot")
def _oracle_snapshot_tick():
    """Capture WU/HKO oracle-time snapshots daily at 10:00 UTC.

    Promotes oracle_snapshot_listener.py into the ingest_main scheduler
    (same F35 pattern used by _bridge_oracle_tick) so the snapshot job is
    co-located with the bridge it feeds and survives daemon restarts without
    a separate crontab entry.

    Must run at 10:00 UTC — 5 min before the bridge at 10:05 — so today's
    snapshot is present before the bridge computes comparisons.  The
    listener is idempotent: re-running with the same target date overwrites
    the file atomically.

    Zero coupling to any DB — reads only config/cities.json and writes to
    raw/oracle_time_snapshots/.  subprocess.run (not DB executor).
    """
    _run_oracle_snapshot_script()


def _run_oracle_snapshot_script() -> str:
    """Run oracle_snapshot_listener.py once as a subprocess."""
    if not _ORACLE_SNAPSHOT_LOCK.acquire(blocking=False):
        logger.info("[ORACLE_SNAPSHOT_TICK] skipped lock_held")
        return "skipped_lock_held"
    try:
        venv_python = _etl_subprocess_python()
        script = Path(__file__).parent.parent / "scripts" / "oracle_snapshot_listener.py"
        if not script.exists():
            logger.warning("[ORACLE_SNAPSHOT_TICK] script not found at %s", script)
            return "missing_script"
        import subprocess
        r = subprocess.run(
            [venv_python, str(script)],
            capture_output=True, text=True, timeout=300,
        )
        stdout_tail = r.stdout[-500:] if r.stdout else ""
        if r.returncode != 0:
            logger.warning(
                "[ORACLE_SNAPSHOT_TICK] FAILED (exit=%d): %s",
                r.returncode, r.stderr[-500:] if r.stderr else "",
            )
            return "failed_subprocess"
        logger.info("[ORACLE_SNAPSHOT_TICK] OK (exit=0) stdout=%r", stdout_tail)
        return "ok"
    except Exception:
        logger.exception("[ORACLE_SNAPSHOT_TICK] FAILED exception")
        return "failed_exception"
    finally:
        _ORACLE_SNAPSHOT_LOCK.release()


def _latest_oracle_snapshot_mtime() -> float | None:
    """Return latest oracle-time snapshot mtime, or None when no snapshots exist."""
    try:
        from src.state.paths import oracle_snapshot_dir
        snapshot_dir = oracle_snapshot_dir()
        if not snapshot_dir.exists():
            return None
        latest: float | None = None
        for snapshot in snapshot_dir.glob("*/*.json"):
            try:
                mtime = snapshot.stat().st_mtime
            except OSError:
                continue
            latest = mtime if latest is None else max(latest, mtime)
        return latest
    except Exception as exc:
        logger.warning("ingest_oracle_bridge_startup: snapshot freshness check failed: %s", exc)
        return None


def _oracle_bridge_artifact_mtimes() -> tuple[float, ...]:
    """Return mtimes for all bridge outputs that must be current together."""
    try:
        from src.state.paths import oracle_artifact_heartbeat_path, oracle_error_rates_path
        mtimes: list[float] = []
        for artifact in (oracle_error_rates_path(), oracle_artifact_heartbeat_path()):
            try:
                mtimes.append(artifact.stat().st_mtime)
            except OSError:
                continue
        return tuple(mtimes)
    except Exception as exc:
        logger.warning("ingest_oracle_bridge_startup: artifact freshness check failed: %s", exc)
        return ()


def _oracle_bridge_artifact_lags_snapshots() -> bool:
    """True when snapshots exist and the bridge artifact is absent or older."""
    latest_snapshot = _latest_oracle_snapshot_mtime()
    if latest_snapshot is None:
        return False
    artifact_mtimes = _oracle_bridge_artifact_mtimes()
    if len(artifact_mtimes) < 2:
        return True
    return any(mtime < latest_snapshot for mtime in artifact_mtimes)


@_scheduler_job("ingest_oracle_bridge_startup_catch_up")
def _bridge_oracle_startup_catch_up():
    """Run oracle bridge at daemon boot if the daily cron was missed."""
    if not _oracle_bridge_artifact_lags_snapshots():
        logger.info("[BRIDGE_ORACLE_STARTUP] skip artifact_current")
        return {"status": "skipped_current"}
    logger.info("[BRIDGE_ORACLE_STARTUP] running bridge because snapshots are newer than artifact")
    bridge_status = _run_bridge_oracle_script()
    if bridge_status != "ok":
        return {"status": bridge_status}
    return {"status": "ran"}


# ---------------------------------------------------------------------------
# F9: Calibration auto-promote tick — weekly Sun 04:30 UTC
# ---------------------------------------------------------------------------

_CALIBRATION_AUTO_PROMOTE_ENV = "ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED"
_CALIBRATION_STAGE_DB_ENV = "ZEUS_CALIBRATION_STAGE_DB_PATH"


@_scheduler_job("ingest_calibration_auto_promote")
def _calibration_auto_promote_tick():
    """F9: Auto-promote calibration_pairs when the readiness gate passes.

    Gate: invokes ``promote_calibration.py inspect`` as a subprocess.
    If the inspect exit code is 0 (all sentinels complete), invokes
    ``promote_calibration.py promote --commit``.

    Guarded by two env flags:

    * ``ZEUS_CALIBRATION_AUTO_PROMOTE_ENABLED=true`` — must be set by the
      operator after the first successful manual promotion validates the gate.
      Default OFF to prevent accidental production writes before the gate is
      verified.
    * ``ZEUS_CALIBRATION_STAGE_DB_PATH`` — absolute path to the STAGE_DB that
      was produced by ``rebuild_calibration_pairs.py``.  Must be set when
      ENABLED=true; tick aborts with a warning if unset.

    Runs on default executor (subprocess writes to zeus-forecasts.db via
    the promote script; serialised with other DB writers via write-class lock).
    """
    import subprocess

    enabled = os.environ.get(_CALIBRATION_AUTO_PROMOTE_ENV, "false").lower() == "true"
    if not enabled:
        logger.info(
            "[AUTO_PROMOTE] skipped: %s not set to 'true'",
            _CALIBRATION_AUTO_PROMOTE_ENV,
        )
        return

    stage_db = os.environ.get(_CALIBRATION_STAGE_DB_ENV, "").strip()
    if not stage_db:
        logger.warning(
            "[AUTO_PROMOTE] aborted: %s not set; cannot auto-promote without stage DB path",
            _CALIBRATION_STAGE_DB_ENV,
        )
        return

    venv_python = _etl_subprocess_python()
    script = Path(__file__).parent.parent / "scripts" / "promote_calibration.py"
    if not script.exists():
        logger.warning("[AUTO_PROMOTE] script not found at %s", script)
        return

    # Phase 1: inspect — readiness gate (read-only, no lock needed)
    inspect_r = subprocess.run(
        [venv_python, str(script), "inspect", "--stage-db", stage_db],
        capture_output=True, text=True, timeout=120,
    )
    if inspect_r.returncode != 0:
        logger.info(
            "[AUTO_PROMOTE] gate NOT READY (inspect exit=%d); skipping promote.\n%s",
            inspect_r.returncode,
            inspect_r.stdout[-500:] if inspect_r.stdout else "",
        )
        return

    logger.info("[AUTO_PROMOTE] gate READY (inspect exit=0); invoking promote --commit")

    # Phase 2: promote --commit (DB writer; serialise via write-class lock)
    from src.state.db_writer_lock import WriteClass, subprocess_run_with_write_class
    promote_r = subprocess_run_with_write_class(
        [venv_python, str(script), "promote", "--stage-db", stage_db, "--commit"],
        WriteClass.BULK,
        capture_output=True, text=True, timeout=600,
    )
    if promote_r.returncode != 0:
        logger.warning(
            "[AUTO_PROMOTE] FAILED (exit=%d): %s",
            promote_r.returncode, promote_r.stderr[-500:] if promote_r.stderr else "",
        )
    else:
        logger.info("[AUTO_PROMOTE] SUCCESS (exit=0)")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _ingest_main_job_specs() -> list[tuple]:
    """Every ingest_main scheduled job as (callable, trigger, kwargs) — the ONE source consumed by
    BOTH the legacy add_job loop and the registry builder (PR #329 A). OpenData jobs are conditional
    on _ingest_main_owns_opendata() exactly as the hand-coded scheduler was, so the live OpenData
    singleton is preserved. Trigger params are byte-identical to the pre-#329 add_job calls; the
    registry path additionally normalizes executor-lane + concurrency from the build spec (the
    intended PR8/F10 behavior)."""
    from datetime import datetime as _dt_now

    now = _dt_now.now()
    replacement_availability_poll_seconds = _replacement_availability_poll_seconds()
    day0_metar_poll_seconds = _day0_metar_poll_seconds()
    specs: list[tuple] = [
        (_k2_daily_obs_tick, "cron", dict(minute=0, id="ingest_k2_daily_obs",
            max_instances=1, coalesce=True, misfire_grace_time=1800)),
        (_k2_hourly_instants_tick, "cron", dict(minute=7, id="ingest_k2_hourly_instants",
            max_instances=1, coalesce=True, misfire_grace_time=1800)),
        (_k2_solar_daily_tick, "cron", dict(hour=0, minute=30, id="ingest_k2_solar_daily",
            max_instances=1, coalesce=True, misfire_grace_time=3600)),
        (_k2_forecasts_daily_tick, "cron", dict(hour=7, minute=30, id="ingest_k2_forecasts_daily",
            max_instances=1, coalesce=True, misfire_grace_time=3600)),
        (_k2_hole_scanner_tick, "cron", dict(hour=4, minute=0, id="ingest_k2_hole_scanner",
            max_instances=1, coalesce=True, misfire_grace_time=3600)),
        (_k2_obs_tick, "cron", dict(minute=15, id="ingest_k2_obs",
            max_instances=1, coalesce=True, misfire_grace_time=3600)),
        # Option-C fast tick: every 15 min, active-window cities only (day0_obs_fastlane_plan §4.3).
        # Supplemental writer for observation_instants; no new db_table_ownership.yaml entry
        # needed (primary daemon_writer remains ingest_k2_obs_tick).
        (_k2_obs_fast_tick, "interval", dict(minutes=15, id="ingest_k2_obs_fast_tick",
            max_instances=1, coalesce=True, misfire_grace_time=300)),
        (_day0_metar_source_clock_tick, "interval", dict(seconds=day0_metar_poll_seconds,
            id="ingest_day0_metar_source_clock", max_instances=1, coalesce=True,
            misfire_grace_time=max(5, int(day0_metar_poll_seconds * 2)),
            next_run_time=now)),
        (_day0_oracle_anomaly_tick, "interval", dict(seconds=10,
            id="ingest_day0_oracle_anomaly", max_instances=1, coalesce=True,
            misfire_grace_time=30, next_run_time=now + timedelta(seconds=2.5))),
        (_k2_hko_tick, "cron", dict(minute=30, id="ingest_k2_hko_tick",
            max_instances=1, coalesce=True, misfire_grace_time=3600)),
        (_etl_recalibrate, "cron", dict(hour=6, minute=0, id="ingest_etl_recalibrate")),
        (_harvester_truth_writer_tick, "cron", dict(minute=45, id="ingest_harvester_truth_writer",
            max_instances=1, coalesce=True, misfire_grace_time=1800)),
        (_automation_analysis_cycle, "cron", dict(hour=9, minute=0, id="ingest_automation_analysis",
            max_instances=1, coalesce=True)),
        # OPERATOR DIRECTIVE 2026-06-11 + source-clock upgrade 2026-06-25:
        # downloads live in the data-ingest daemon, first fire IMMEDIATE at boot
        # (next_run_time=now), then on a fast source-clock cadence. Downloading
        # never waits on a daemon's first interval, never dies with trading
        # restarts, and does not sit behind the old 5-minute publication poll.
        (_replacement_availability_poll_tick, "interval", dict(seconds=replacement_availability_poll_seconds,
            id="ingest_replacement_availability_poll", max_instances=1, coalesce=True,
            misfire_grace_time=max(120, replacement_availability_poll_seconds * 2),
            next_run_time=now)),
    ]

    # ECMWF Open Data daily live jobs — conditional on ingest_main owning OpenData (singleton).
    if _ingest_main_owns_opendata():
        specs += [
            (_opendata_mx2t6_cycle, "cron", dict(hour=7, minute=30, id="ingest_opendata_daily_mx2t6",
                max_instances=1, coalesce=True, misfire_grace_time=3600)),
            (_opendata_mn2t6_cycle, "cron", dict(hour=7, minute=35, id="ingest_opendata_daily_mn2t6",
                max_instances=1, coalesce=True, misfire_grace_time=3600)),
        ]
    else:
        logger.info("OpenData daily jobs not registered in ingest_main: %s=%s",
                    FORECAST_LIVE_OWNER_ENV, _forecast_live_owner())

    specs += [
        (_tigge_archive_backfill_cycle, "cron", dict(hour=14, minute=0,
            id="ingest_tigge_archive_backfill", max_instances=1, coalesce=True, misfire_grace_time=3600)),
        (_k2_startup_catch_up, "date", dict(run_date=now, id="ingest_k2_startup_catch_up",
            max_instances=1, coalesce=True, misfire_grace_time=None)),
        (_tigge_startup_catch_up, "date", dict(run_date=now, id="ingest_tigge_startup_catch_up",
            max_instances=1, coalesce=True, misfire_grace_time=None)),
    ]

    # OpenData boot-time catch-up — conditional on ownership (matches the daily jobs above).
    if _ingest_main_owns_opendata():
        specs.append(
            (_opendata_startup_catch_up, "date", dict(run_date=now, id="ingest_opendata_startup_catch_up",
                max_instances=1, coalesce=True, misfire_grace_time=None)))
    else:
        logger.info("OpenData startup job not registered in ingest_main: %s=%s",
                    FORECAST_LIVE_OWNER_ENV, _forecast_live_owner())

    specs += [
        (_source_health_probe_tick, "interval", dict(minutes=10, id="ingest_source_health_probe",
            max_instances=1, coalesce=True, executor="fast")),
        (_station_migration_probe_tick, "interval", dict(minutes=60, id="ingest_station_migration_probe",
            max_instances=1, coalesce=True)),
        (_drift_detector_tick, "cron", dict(hour=6, minute=0, id="ingest_drift_detector",
            max_instances=1, coalesce=True, misfire_grace_time=3600)),
        (_ingest_status_rollup_tick, "interval", dict(minutes=5, id="ingest_status_rollup",
            max_instances=1, coalesce=True, executor="fast")),
        (_write_ingest_heartbeat, "interval", dict(seconds=60, id="ingest_heartbeat",
            max_instances=1, coalesce=True, executor="fast")),
        (_uma_resolution_listener_tick, "interval", dict(minutes=5, id="ingest_uma_resolution_listener",
            max_instances=1, coalesce=True, executor="fast")),
        (_etl_forecast_skill_tick, "cron", dict(hour=3, minute=0, id="ingest_etl_forecast_skill",
            max_instances=1, coalesce=True, misfire_grace_time=3600)),
        (_market_scan_tick, "interval", dict(minutes=30, id="ingest_market_scan",
            max_instances=1, coalesce=True)),
        (_oracle_snapshot_tick, "cron", dict(hour=10, minute=0, id="ingest_oracle_snapshot",
            max_instances=1, coalesce=True, misfire_grace_time=600, executor="fast")),
        (_bridge_oracle_tick, "cron", dict(hour=10, minute=5, id="ingest_oracle_bridge",
            max_instances=1, coalesce=True, misfire_grace_time=600, executor="fast")),
        (_bridge_oracle_startup_catch_up, "date", dict(run_date=now,
            id="ingest_oracle_bridge_startup_catch_up", max_instances=1, coalesce=True,
            misfire_grace_time=None, executor="fast")),
        (_calibration_auto_promote_tick, "cron", dict(day_of_week="sun", hour=4, minute=30,
            id="ingest_calibration_auto_promote", max_instances=1, coalesce=True, misfire_grace_time=3600)),
    ]
    return specs


def main() -> None:
    global _scheduler
    from apscheduler.schedulers.blocking import BlockingScheduler

    # F85: route INFO/DEBUG to stdout (.log) and WARNING+ to stderr (.err).
    _fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    _stdout_h = logging.StreamHandler(sys.stdout)
    _stdout_h.setLevel(logging.INFO)
    _stdout_h.setFormatter(_fmt)
    _stdout_h.addFilter(lambda r: r.levelno < logging.WARNING)
    _stderr_h = logging.StreamHandler(sys.stderr)
    _stderr_h.setLevel(logging.WARNING)
    _stderr_h.setFormatter(_fmt)
    _root = logging.getLogger()
    _root.handlers.clear()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stdout_h)
    _root.addHandler(_stderr_h)
    logger.info("Zeus data-ingest daemon starting (pid=%d)", os.getpid())

    # §4.5(a): control_plane.json dual consumer — boot-time read of ingest directives.
    # Reads paused_sources from state/control_plane.json. Per-tick enforcement is
    # done in _is_source_paused() called from each K2 tick wrapper below.
    # PHASE-3-STUB §4.5(a): stub marker preserved for grep-based antibody compatibility.
    # PHASE-3-STUB-END
    from src.control.control_plane import read_ingest_control_state
    _ingest_ctrl = read_ingest_control_state()
    if _ingest_ctrl.get("paused_sources"):
        logger.info(
            "Ingest daemon boot: control_plane paused_sources=%s",
            sorted(_ingest_ctrl["paused_sources"]),
        )

    # Proxy health gate — must precede any HTTP call.
    from src.data.proxy_health import bypass_dead_proxy_env_vars
    bypass_dead_proxy_env_vars()

    # Schema init on world DB.  A current sentinel means a prior init_schema
    # already returned OK for the pinned DDL; skip the repeat write path on
    # restarts so source-clock polling is not delayed behind a world DB lock.
    from src.state.db import init_schema, get_world_connection
    if _world_schema_boot_requires_init():
        conn = get_world_connection(write_class="bulk")
        init_schema(conn)
        conn.close()
        logger.info("init_schema complete")
    _assert_forecasts_schema_ready_for_ingest()
    logger.info("init_schema_forecasts + assert_schema_current_forecasts complete")

    # v1.F1 (2026-05-18): assert_db_matches_registry boot wiring — ingest daemon.
    # Fail-closed per INV-05: RegistryAssertionError propagates and aborts daemon start.
    # No advisory mode — a live DB whose table-set diverges from
    # architecture/db_table_ownership.yaml must not enter the ingest loop.
    # Guard: ZEUS_BOOT_REGISTRY_ASSERT_ENABLED defaults "1" (enabled).
    # Set to "0" ONLY during intentional schema migrations; document the migration window.
    if os.environ.get("ZEUS_BOOT_REGISTRY_ASSERT_ENABLED", "1") != "0":
        from src.state.table_registry import (
            DBIdentity,
            assert_db_matches_registry,
            assert_writer_jobs_registered,
        )
        _world_conn_reg = get_world_connection()
        try:
            assert_db_matches_registry(_world_conn_reg, DBIdentity.WORLD)
            logger.info("assert_db_matches_registry: world DB table-set matches registry")
        finally:
            _world_conn_reg.close()

        # v1.F44 (2026-05-18): A5 — daemon_writer registry cross-check.
        # Every YAML entry with daemon_writer != "none" must have a live
        # @_scheduler_job(...) in this file.  Prevents silent writer death.
        assert_writer_jobs_registered()
        logger.info("assert_writer_jobs_registered: all declared daemon writers are wired")

        # F2 (fix/persistence-bypass 2026-06-03): assert no daemon caller uses bare
        # find_weather_markets() — all must go through find_weather_markets_or_raise.
        from src.state.table_registry import (
            assert_no_raw_find_weather_markets_in_daemon_callers,
        )
        assert_no_raw_find_weather_markets_in_daemon_callers()
        logger.info(
            "assert_no_raw_find_weather_markets_in_daemon_callers: "
            "all daemon callers use find_weather_markets_or_raise"
        )

    # Write sentinel BEFORE scheduler.start() (design §4.2).
    _write_world_schema_ready_sentinel()

    # SIGTERM → graceful shutdown.
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    # Two-executor topology (Fix #4 2026-05-06; refined post-deployment):
    #
    # Problem: the ingest daemon writes to a single SQLite DB (state/zeus-world.db)
    # under WAL. WAL allows concurrent readers + ONE writer; the default
    # APScheduler ThreadPoolExecutor(max_workers=10) let multiple jobs (hourly
    # insert, OpenData cycle write, harvester truth writer, etc.) hit the
    # writer lock simultaneously, producing the `OperationalError: database is
    # locked` storm observed 2026-05-06 (Chongqing hourly_instants_append
    # failing every ~30s).
    #
    # Naive fix (max_workers=1 single executor) serialised everything but
    # starved the heartbeat + status_rollup + source_health_probe ticks
    # behind the long-running startup catch-up — daemon-heartbeat-ingest.json
    # went 60+ minutes stale, breaking the heartbeat-sensor liveness contract.
    #
    # Refined topology:
    #   - "default" executor (max_workers=1): all DB-writing jobs queue here
    #     and serialise. Per-job max_instances=1 still prevents same-job
    #     overlap; max_workers=1 prevents cross-job overlap.
    #   - "fast" executor (max_workers=4): file-only / observability ticks
    #     (heartbeat, status rollup, source health probe) run in parallel
    #     so they don't starve behind a long DB writer. These jobs do NOT
    #     write to the world DB, so they can't contend on the writer lock.
    #
    # Each add_job() below is annotated with the executor it should run on.
    # New jobs default to "default" (safe for DB writers); only add a job to
    # "fast" if it provably does not write to state/zeus-world.db.
    #
    # See memory: feedback_sqlite_wal_multi_writer_starvation.md.
    specs = _ingest_main_job_specs()

    from src.data.scheduler_adapter import (
        build_registry_scheduler,
        job_defs_from_specs,
        registry_executor_pools,
    )

    # R3 (2026-07-08): registry-built scheduling with executor-lane routing + a fail-fast boot
    # assert (a registry/daemon job-set mismatch halts boot rather than booting a divergent
    # schedule) is now the ONLY path — the legacy hand-coded 2-pool add_job() loop was deleted
    # (zero-caller-verified; no plist ever set the mode-selection env vars, see scheduler_adapter.py).
    _scheduler = BlockingScheduler(executors=registry_executor_pools())
    build_registry_scheduler(
        _scheduler, "ingest_main", job_defs_from_specs(specs),
        forecast_live_owner_env=_forecast_live_owner(), logger=logger,
    )

    jobs = [j.id for j in _scheduler.get_jobs()]
    logger.info("Ingest scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus data-ingest daemon shutting down")
        _shutdown_scheduler_if_running(_scheduler, wait=True)


if __name__ == "__main__":
    main()
