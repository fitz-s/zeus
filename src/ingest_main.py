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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
DAY0_HKO_POLL_SECONDS_ENV = "ZEUS_DAY0_HKO_POLL_SECONDS"
DAY0_METAR_COMMIT_RETRY_SECONDS = 0.25
DAY0_METAR_COMMIT_RETRY_MAX_SECONDS = 5.0
DAY0_METAR_COMMIT_RETRY_MAX_FAILURES = 6
# Bounded local retry for the Day0 family-admission resolver (review blocker
# C6). A failed read gets one more shot within this call before the caller
# gives up for this tick; the next scheduled poll re-derives admission fresh
# regardless. Never unbounded — this must not stall the source-clock tick.
DAY0_FAMILY_ADMISSION_RETRY_BUDGET_SECONDS = 1.0
DAY0_FAMILY_ADMISSION_RETRY_INTERVAL_SECONDS = 0.1
_ORACLE_BRIDGE_LOCK = threading.Lock()
_ORACLE_SNAPSHOT_LOCK = threading.Lock()
_DAY0_METAR_EMITTER: Any | None = None
_DAY0_HKO_POLLER: Any | None = None
_DAY0_METAR_COMMIT_LOCK = threading.Lock()
_DAY0_METAR_PENDING_COMMITS: list[tuple[Any, str, bool, Any | None]] = []
_DAY0_METAR_RETRY_LOCK = threading.Lock()
_DAY0_METAR_RETRY_FAILURES = 0
_DAY0_METAR_RETRY_NOT_BEFORE_MONOTONIC = 0.0
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


def _day0_hko_poll_seconds() -> float:
    raw = os.environ.get(DAY0_HKO_POLL_SECONDS_ENV, "").strip()
    if not raw:
        return 2.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning(
            "invalid %s=%r; using 2s HKO extrema source-clock cadence",
            DAY0_HKO_POLL_SECONDS_ENV,
            raw,
        )
        return 2.0


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


def _close_day0_metar_emitter() -> None:
    """Release the ingest-owned Day0 worker pools exactly once at shutdown."""

    global _DAY0_METAR_EMITTER
    with _DAY0_METAR_COMMIT_LOCK:
        emitter = _DAY0_METAR_EMITTER
        _DAY0_METAR_EMITTER = None
    if emitter is None:
        return
    try:
        emitter.close()
    except Exception as exc:  # noqa: BLE001 - teardown must retain clean exit
        logger.warning(
            "DAY0_METAR_EMITTER_CLOSE_FAILED exc=%s: %s",
            type(exc).__name__,
            exc,
        )


def _day0_hko_poller():
    global _DAY0_HKO_POLLER
    if _DAY0_HKO_POLLER is None:
        from scripts.hko_ingest_tick import HkoExtremaPoller

        _DAY0_HKO_POLLER = HkoExtremaPoller()
    return _DAY0_HKO_POLLER


def _close_day0_hko_poller() -> None:
    global _DAY0_HKO_POLLER
    poller = _DAY0_HKO_POLLER
    _DAY0_HKO_POLLER = None
    if poller is None:
        return
    try:
        poller.close()
    except Exception as exc:  # noqa: BLE001 - teardown must retain clean exit
        logger.warning(
            "DAY0_HKO_POLLER_CLOSE_FAILED exc=%s: %s",
            type(exc).__name__,
            exc,
        )


def _day0_priority_scopes() -> frozenset[tuple[str, str]]:
    """Current exposure scopes whose station files deserve the fastest lane."""

    try:
        from src.data.replacement_forecast_seed_discovery import (
            held_position_family_priorities,
        )

        priorities = held_position_family_priorities()
    except Exception as exc:  # noqa: BLE001 - global METAR feed remains available
        logger.warning(
            "DAY0_PRIORITY_SCOPE_READ_FAILED exc=%s: %s",
            type(exc).__name__,
            exc,
        )
        return frozenset()
    return frozenset(
        (city, target_date)
        for (city, target_date, _metric), priority in priorities.items()
        if priority == 0
    )


def _day0_family_admission_for_scopes(
    scopes: tuple[tuple[str, str], ...],
):
    """Bind source-clock events to a listed market or current exposure.

    Fail-CLOSED (review blocker C6): a caller reads the returned predicate as
    "admit only what it accepts" for any non-None return, and treats a bare
    ``None`` as "no filter configured" (admit everything). So on admission-
    read failure this must NEVER return ``None`` — that would silently widen
    every eligible high/low family into an executable event on a plain DB
    fault. Absence of admission truth is not the same as "all families".
    Instead: retry the resolver a bounded number of times within this call,
    and on exhaustion return a deny-all predicate (identical to the "no
    scopes requested" branch below) so the caller emits nothing this tick.
    Raw source facts are written upstream of this gate regardless (this
    function only ever influences the trade-decision/reactor-wake event, not
    the underlying observation persistence) and the next scheduled poll
    re-resolves admission from scratch, so a transient outage delays
    emission rather than losing or misrouting it.
    """

    scopes = tuple(
        sorted(
            {
                (str(city or "").strip(), str(target_date or "").strip())
                for city, target_date in scopes
                if str(city or "").strip() and str(target_date or "").strip()
            }
        )
    )
    if not scopes:
        return lambda _observation: False

    from src.state.db import (
        get_forecasts_connection_read_only,
        get_trade_connection_read_only,
    )

    values = ",".join("(?,?)" for _ in scopes)
    params = tuple(value for scope in scopes for value in scope)
    deadline = time.monotonic() + DAY0_FAMILY_ADMISSION_RETRY_BUDGET_SECONDS
    last_exc: Exception | None = None
    while True:
        forecasts_conn = None
        trade_conn = None
        try:
            forecasts_conn = get_forecasts_connection_read_only()
            market_rows = forecasts_conn.execute(
                f"""
                WITH requested(city, target_date) AS (VALUES {values})
                SELECT DISTINCT m.city, m.target_date, m.temperature_metric
                  FROM requested AS r
                  JOIN market_events AS m
                    ON m.city = r.city
                   AND m.target_date = r.target_date
                 WHERE m.temperature_metric IN ('high', 'low')
                   AND COALESCE(m.condition_id, '') != ''
                """,
                params,
            ).fetchall()

            trade_conn = get_trade_connection_read_only()
            exposure_rows = trade_conn.execute(
                f"""
                WITH requested(city, target_date) AS (VALUES {values})
                SELECT DISTINCT p.city, p.target_date, p.temperature_metric
                  FROM requested AS r
                  JOIN position_current AS p
                    ON p.city = r.city
                   AND p.target_date = r.target_date
                 WHERE p.temperature_metric IN ('high', 'low')
                   AND p.phase IN ('pending_entry', 'active', 'day0_window', 'pending_exit')
                """,
                params,
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 - admission loss must not swallow source facts
            last_exc = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(DAY0_FAMILY_ADMISSION_RETRY_INTERVAL_SECONDS)
            continue
        finally:
            if trade_conn is not None:
                trade_conn.close()
            if forecasts_conn is not None:
                forecasts_conn.close()

        families = frozenset(
            (str(city), str(target_date), str(metric).lower())
            for city, target_date, metric in (*market_rows, *exposure_rows)
        )

        def _admit(observation: dict[str, Any]) -> bool:
            return (
                str(observation.get("city") or "").strip(),
                str(observation.get("target_date") or "").strip(),
                str(observation.get("metric") or "").strip().lower(),
            ) in families

        return _admit

    logger.warning(
        "DAY0_METAR_FAMILY_ADMISSION_UNAVAILABLE fail_closed=true "
        "budget_s=%.1f exc=%s: %s",
        DAY0_FAMILY_ADMISSION_RETRY_BUDGET_SECONDS,
        type(last_exc).__name__,
        last_exc,
    )
    return lambda _observation: False


def _day0_source_family_admission(eligible: Any):
    """Derive executable family admission from a METAR prefetch."""

    return _day0_family_admission_for_scopes(
        tuple(
            (
                str(getattr(city, "name", "") or "").strip(),
                str(target_date or "").strip(),
            )
            for city, _source, target_date in tuple(eligible or ())
        )
    )


def _stage_day0_metar_commit(
    prefetch: Any,
    *,
    received_at: str,
    day0_is_tradeable: bool,
    family_admission: Any | None = None,
) -> None:
    with _DAY0_METAR_COMMIT_LOCK:
        staged = (
            prefetch,
            received_at,
            day0_is_tradeable,
            family_admission,
        )
        if len(_DAY0_METAR_PENDING_COMMITS) < 2:
            _DAY0_METAR_PENDING_COMMITS.append(staged)
        else:
            _DAY0_METAR_PENDING_COMMITS[-1] = staged


def _bridge_committed_day0_events(
    *,
    source: str,
    event_ids: tuple[str, ...],
    families: tuple[tuple[str, str, str], ...],
) -> None:
    """After commit, enqueue probability refresh and wake the trading reactor."""

    if not event_ids:
        return
    unique_families = tuple(dict.fromkeys(families))
    try:
        from src.data.replacement_cycle_advance_trigger import (
            enqueue_day0_extreme_updated_materialization_seed,
        )
    except Exception:
        logger.warning(
            "DAY0_SOURCE_MATERIALIZATION_BRIDGE_FAILED source=%s families=%d; "
            "scheduled recompute remains the fallback",
            source,
            len(unique_families),
            exc_info=True,
        )
    else:
        for city, target_date, metric in unique_families:
            # Per-family boundary: one family's seed-enqueue error must not
            # skip the seed pre-warm for later families. The underlying fact
            # is already durably committed before this function runs, so a
            # scheduled recompute remains the fallback for the failed family
            # alone — this is latency isolation, not fact-loss prevention.
            try:
                report = enqueue_day0_extreme_updated_materialization_seed(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                )
                logger.info(
                    "DAY0_SOURCE_MATERIALIZATION_BRIDGE source=%s city=%s "
                    "target_date=%s metric=%s status=%s",
                    source,
                    city,
                    target_date,
                    metric,
                    report.get("status"),
                )
            except Exception:
                logger.warning(
                    "DAY0_SOURCE_MATERIALIZATION_BRIDGE_FAILED source=%s "
                    "city=%s target_date=%s metric=%s; scheduled recompute "
                    "remains the fallback",
                    source,
                    city,
                    target_date,
                    metric,
                    exc_info=True,
                )
    try:
        from src.runtime.reactor_wake import publish_reactor_wake

        publish_reactor_wake(
            source=source,
            reason="day0_extreme_event_committed",
            event_ids=event_ids,
            forecast_families=unique_families,
        )
    except Exception:
        logger.warning(
            "DAY0_SOURCE_REACTOR_WAKE_FAILED source=%s events=%d; "
            "periodic reactor scan remains authoritative",
            source,
            len(event_ids),
            exc_info=True,
        )


def _persist_day0_metar_ledger_after_wake(prefetch: Any) -> bool:
    """Best-effort additive ledger flush outside the Day0 alpha transaction."""

    persist = getattr(_day0_metar_emitter(), "persist_prefetched_ledger", None)
    if not callable(persist):
        return False
    ledger_reports = getattr(prefetch, "ledger_reports", ())
    if ledger_reports is not None and not tuple(ledger_reports):
        return True

    conn = None
    mutex = None
    acquired = False
    try:
        from src.state.db import get_world_connection, world_write_mutex

        conn = get_world_connection(write_class="live")
        conn.execute("PRAGMA busy_timeout = 1")
        mutex = world_write_mutex()
        acquired = mutex.acquire(timeout=0.0)
        if not acquired:
            return False
        conn.execute("BEGIN IMMEDIATE")
        if not persist(world_conn=conn, prefetch=prefetch):
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - additive history never blocks alpha
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        logger.warning(
            "DAY0_METAR_LEDGER_DEFERRED reason=%s:%s",
            type(exc).__name__,
            exc,
        )
        return False
    finally:
        if acquired and mutex is not None:
            mutex.release()
        if conn is not None:
            conn.close()


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
    evaluated_report_keys: list[tuple[str, str, float]] = []
    deferred_memo_updates: dict[
        tuple[str, str, str], tuple[int | None, int | None, str | None]
    ] = {}
    pending_reports = 0
    try:
        if not _DAY0_METAR_PENDING_COMMITS:
            return {"status": "SOURCE_CURRENT"}
        staged = _DAY0_METAR_PENDING_COMMITS[0]
        prefetch, received_at, day0_is_tradeable, family_admission = staged
        emitter = _day0_metar_emitter()
        pending_reports = len(tuple(prefetch.ledger_reports or ()))
        if pending_reports == 0:
            del _DAY0_METAR_PENDING_COMMITS[0]
            return {"status": "SOURCE_CURRENT"}

        import sqlite3

        from src.state.db import get_world_connection, world_write_mutex
        from src.state.write_coordinator import (
            DBIdentity,
            WriteLeaseTimeout,
            default_runtime_write_coordinator,
        )

        write_budget_s = _day0_metar_write_budget_seconds()
        write_deadline = time.monotonic() + write_budget_s
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
            remaining_ms = max(
                0,
                int((write_deadline - time.monotonic()) * 1000.0),
            )
            with default_runtime_write_coordinator().lease(
                (DBIdentity.WORLD,),
                owner="day0_metar_source_clock",
                write_class="live",
                deadline_ms=remaining_ms,
                max_hold_ms=max(1, int(write_budget_s * 1000.0)),
            ) as write_lease:
                conn = get_world_connection(write_class="live")
                conn.execute(f"PRAGMA busy_timeout = {max(1, remaining_ms)}")
                before_changes = int(conn.total_changes)
                conn.execute("BEGIN IMMEDIATE")
                emitted = emitter.emit_prefetched(
                    world_conn=conn,
                    prefetch=prefetch,
                    received_at=received_at,
                    limit=max(50, len(prefetch.eligible) * 2),
                    day0_is_tradeable=day0_is_tradeable,
                    family_admission=family_admission,
                    inserted_event_ids=inserted_event_ids,
                    inserted_families=inserted_families,
                    evaluated_report_keys=evaluated_report_keys,
                    deferred_memo_updates=deferred_memo_updates,
                    persist_ledger=False,
                )
                commit_started = time.monotonic()
                conn.commit()
                write_lease.record_commit(
                    commit_ms=(time.monotonic() - commit_started) * 1000.0,
                    rows_changed=max(0, int(conn.total_changes) - before_changes),
                )
                apply_memo_updates = getattr(emitter, "apply_memo_updates", None)
                if callable(apply_memo_updates):
                    apply_memo_updates(deferred_memo_updates)
                mark_memo_snapshot = getattr(emitter, "mark_event_memo_snapshot", None)
                if callable(mark_memo_snapshot):
                    mark_memo_snapshot(conn)
                mark_evaluated = getattr(
                    emitter,
                    "mark_prefetched_events_evaluated",
                    None,
                )
                if callable(mark_evaluated):
                    mark_evaluated(evaluated_report_keys)
                del _DAY0_METAR_PENDING_COMMITS[0]
        except WriteLeaseTimeout as exc:
            logger.info(
                "DAY0_METAR_COMMIT_DEFERRED origin=%s reason=world_writer_gate_busy "
                "pending_reports=%d budget_ms=%d exc=%r",
                origin,
                pending_reports,
                int(write_budget_s * 1000.0),
                exc,
            )
            return {
                "status": "WRITE_CONTENDED",
                "pending_reports": pending_reports,
            }
        except sqlite3.OperationalError as exc:
            if conn is not None:
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
        _bridge_committed_day0_events(
            source="day0_metar_source_clock",
            event_ids=tuple(inserted_event_ids),
            families=tuple(inserted_families),
        )
    # Never reacquire the world writer after waking the reactor for a new
    # trade fact. The emitter retains unledgered publication identities, and a
    # later non-emitting source pass persists them outside the alpha window.
    ledger_persisted = (
        _persist_day0_metar_ledger_after_wake(prefetch)
        if emitted == 0
        else False
    )
    logger.info(
        "DAY0_METAR_COMMIT_COMPLETED origin=%s pending_reports=%d emitted=%d "
        "ledger_persisted=%s",
        origin,
        pending_reports,
        emitted,
        ledger_persisted,
    )
    return {
        "status": "COMMITTED",
        "pending_reports": pending_reports,
        "events_emitted": emitted,
    }


def _schedule_day0_metar_commit_retry() -> bool:
    """Coalesce a pending Day0 write into one bounded-backoff retry."""

    global _DAY0_METAR_RETRY_FAILURES, _DAY0_METAR_RETRY_NOT_BEFORE_MONOTONIC

    if _scheduler is None:
        logger.error("DAY0_METAR_COMMIT_RETRY_NOT_SCHEDULED reason=scheduler_unavailable")
        return False

    with _DAY0_METAR_RETRY_LOCK:
        if not _DAY0_METAR_PENDING_COMMITS:
            return False
        now_monotonic = time.monotonic()
        if now_monotonic < _DAY0_METAR_RETRY_NOT_BEFORE_MONOTONIC:
            return True
        if _DAY0_METAR_RETRY_FAILURES >= DAY0_METAR_COMMIT_RETRY_MAX_FAILURES:
            logger.warning(
                "DAY0_METAR_COMMIT_RETRY_EXHAUSTED failures=%d; "
                "pending fact retained for the next source-clock tick",
                _DAY0_METAR_RETRY_FAILURES,
            )
            return False
        failures = _DAY0_METAR_RETRY_FAILURES + 1
        delay_seconds = min(
            DAY0_METAR_COMMIT_RETRY_SECONDS * (2 ** (failures - 1)),
            DAY0_METAR_COMMIT_RETRY_MAX_SECONDS,
        )
        _scheduler.add_job(
            _day0_metar_commit_retry_tick,
            "date",
            run_date=datetime.now(timezone.utc)
            + timedelta(seconds=delay_seconds),
            id="ingest_day0_metar_commit_retry",
            executor="source_clock_db",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1,
            replace_existing=True,
        )
        _DAY0_METAR_RETRY_FAILURES = failures
        _DAY0_METAR_RETRY_NOT_BEFORE_MONOTONIC = now_monotonic + delay_seconds
        logger.info(
            "DAY0_METAR_COMMIT_RETRY_SCHEDULED failures=%d delay_seconds=%.3f",
            failures,
            delay_seconds,
        )
    return True


def _reset_day0_metar_commit_retry() -> None:
    """Clear a contention streak once its pending write has closed."""

    global _DAY0_METAR_RETRY_FAILURES, _DAY0_METAR_RETRY_NOT_BEFORE_MONOTONIC
    with _DAY0_METAR_RETRY_LOCK:
        _DAY0_METAR_RETRY_FAILURES = 0
        _DAY0_METAR_RETRY_NOT_BEFORE_MONOTONIC = 0.0


def _commit_or_schedule_day0_metar(*, origin: str) -> dict:
    result = _commit_pending_day0_metar(origin=origin)
    if result.get("status") in {"COMMIT_ACTIVE", "WRITE_CONTENDED"}:
        _schedule_day0_metar_commit_retry()
    else:
        _reset_day0_metar_commit_retry()
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


def _compact_replacement_current_target_report(download_report):
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
        "fusion_upgrade_status": download_report.get("fusion_upgrade_status"),
        "fusion_upgrade_seeds_enqueued": download_report.get(
            "fusion_upgrade_seeds_enqueued"
        ),
        "cycle_advance_status": download_report.get("cycle_advance_status"),
        "cycle_advance_seeds_enqueued": download_report.get(
            "cycle_advance_seeds_enqueued"
        ),
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
    return {key: value for key, value in compact.items() if value is not None}


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
    finally:
        _close_day0_metar_emitter()
        _close_day0_hko_poller()
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
    "replacement_maintenance_partial",
    "download_failed",
    "empty_ingest",
    "extract_failed",
    "market_events_persistence_failed",
    "market_scan_failed",
    "paused_mars_credentials",
    "bad_target_date",
    "source_clock_scoped_bayes_precision_fusion_extra_permanent_failure",
    "source_clock_scoped_bayes_precision_fusion_extra_transport_retryable",
    "source_clock_scoped_bayes_precision_fusion_extra_timeboxed_incomplete",
    "source_clock_bpf_scoped_quota_cooldown_skipped",
    "source_clock_bpf_scoped_cycle_unresolved_skip",
    "source_clock_bpf_scoped_cycle_unresolved_partial",
    "source_clock_bpf_scoped_capture_failsoft_skipped",
    "source_clock_bpf_scoped_run_identity_mismatch",
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
    source_results = result.get("source_results")
    if isinstance(source_results, dict):
        permanent_sources = sorted(
            str(source)
            for source, source_result in source_results.items()
            if isinstance(source_result, dict)
            and str(source_result.get("status") or "").lower()
            == "source_clock_source_permanent_failure"
        )
        if permanent_sources:
            return True, "source_run_permanent_failure:" + ",".join(permanent_sources)
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
    """Capture newly published METAR reports and emit moved Day0 extremes.

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
        priority_scopes=_day0_priority_scopes(),
        anomaly_check=None,
    )
    pending_reports = tuple(prefetch.ledger_reports or ())
    if not pending_reports:
        return {
            "status": "SOURCE_CURRENT",
            "freshness_status": prefetch.freshness_status,
            "reports": len(prefetch.reports),
        }
    events_evaluated = getattr(emitter, "prefetched_events_evaluated", None)
    if callable(events_evaluated) and events_evaluated(prefetch):
        persisted = _persist_day0_metar_ledger_after_wake(prefetch)
        return {
            "status": "LEDGER_FLUSHED" if persisted else "LEDGER_DEFERRED",
            "pending_reports": len(pending_reports),
        }
    family_admission = _day0_source_family_admission(prefetch.eligible)
    hydrate_event_memos = getattr(emitter, "hydrate_event_memos_from_events", None)
    if callable(hydrate_event_memos):
        read_conn = None
        try:
            read_conn = get_world_connection_read_only()
            hydrate_event_memos(
                read_conn,
                prefetch.eligible,
                family_admission=family_admission,
            )
        except Exception as exc:  # noqa: BLE001 - write phase retains fallback
            logger.warning(
                "DAY0_EVENT_MEMO_PREHYDRATE_FAILED exc=%s: %s",
                type(exc).__name__,
                exc,
            )
        finally:
            if read_conn is not None:
                read_conn.close()
    _stage_day0_metar_commit(
        prefetch,
        received_at=decision_time.isoformat(),
        day0_is_tradeable=day0_is_tradeable_for_scope(
            str(edli_cfg.get("edli_live_scope") or "forecast_plus_day0")
        ),
        family_admission=family_admission,
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
    from src.state.db import (
        get_world_connection,
        get_world_connection_read_only,
        world_write_mutex,
    )

    edli_cfg = settings["edli"]
    if not (
        edli_cfg.get("enabled")
        and edli_cfg.get("event_writer_enabled")
        and edli_cfg.get("day0_extreme_trigger_enabled")
        and edli_cfg.get("day0_fast_obs_lane_enabled", True)
    ):
        return {"status": "DISABLED"}

    cities = runtime_cities()
    decision_time = datetime.now(timezone.utc)
    priority_city_names: tuple[str, ...] = ()
    read_conn = None
    try:
        read_conn = get_world_connection_read_only()
        rows = read_conn.execute(
            "SELECT city, target_date, flagged_at, ttl_hours "
            "FROM day0_oracle_anomaly_flags ORDER BY flagged_at"
        ).fetchall()
        city_by_name = {
            str(getattr(city, "name", "") or ""): city for city in cities
        }
        priority = []
        for city_name, target_date, flagged_at, ttl_hours in rows:
            city = city_by_name.get(str(city_name))
            if city is None:
                continue
            try:
                flagged = datetime.fromisoformat(
                    str(flagged_at).replace("Z", "+00:00")
                )
                if flagged.tzinfo is None:
                    flagged = flagged.replace(tzinfo=timezone.utc)
                ttl = float(ttl_hours)
                local_date = decision_time.astimezone(
                    ZoneInfo(str(city.timezone))
                ).date().isoformat()
            except (TypeError, ValueError, ZoneInfoNotFoundError):
                continue
            if (
                ttl > 0.0
                and str(target_date) == local_date
                and decision_time
                <= flagged.astimezone(timezone.utc) + timedelta(hours=ttl)
            ):
                priority.append(str(city_name))
        priority_city_names = tuple(priority)
    except sqlite3.Error:
        logger.warning(
            "DAY0_ORACLE_ANOMALY_PRIORITY_READ_FAILED",
            exc_info=True,
        )
    finally:
        if read_conn is not None:
            read_conn.close()

    actions = _day0_metar_emitter().cached_anomaly_actions(
        cities=cities,
        decision_time=decision_time,
        anomaly_check=wu_metar_anomaly_action,
        max_cities=1 + min(2, len(priority_city_names)),
        priority_city_names=priority_city_names,
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

    logger.info(
        "DAY0_ORACLE_ANOMALY_ACTIONS_COMMITTED count=%d actions=%s",
        len(actions),
        ",".join(
            f"{str(action.action).lower()}:{action.city}:{action.target_date}"
            for action in actions
        ),
    )
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
    """Poll HKO extrema conditionally and publish changed facts after commit."""

    import sqlite3

    from src.data.dual_run_lock import acquire_lock
    from scripts.hko_ingest_tick import DEFAULT_LOG_PATH, project_accumulator_to_v2
    from src.config import runtime_cities_by_name, settings
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.events.event_priority import day0_is_tradeable_for_scope
    from src.events.event_writer import EventWriter
    from src.events.triggers.day0_extreme_updated import Day0ExtremeUpdatedTrigger
    from src.state.db import get_world_connection, world_write_mutex
    from src.state.write_coordinator import (
        DBIdentity,
        WriteLeaseTimeout,
        default_runtime_write_coordinator,
    )

    poller = _day0_hko_poller()
    prefetch = poller.prefetch()
    if prefetch is None:
        return {"status": "SOURCE_CURRENT"}

    snapshot = prefetch.snapshot
    hko_city = runtime_cities_by_name()["Hong Kong"]
    family_admission = _day0_family_admission_for_scopes(
        (("Hong Kong", snapshot.target_date),)
    )
    edli_cfg = settings["edli"]
    event_enabled = bool(
        edli_cfg.get("enabled")
        and edli_cfg.get("event_writer_enabled")
        and edli_cfg.get("day0_extreme_trigger_enabled")
    )
    write_budget_s = _day0_metar_write_budget_seconds()
    write_deadline = time.monotonic() + write_budget_s
    conn = None
    mutex = None
    acquired = False
    inserted_event_ids: tuple[str, ...] = ()
    inserted_families: tuple[tuple[str, str, str], ...] = ()
    try:
        with acquire_lock("hko_tick") as source_acquired:
            if not source_acquired:
                return {"status": "SOURCE_CONTENDED"}
            mutex = world_write_mutex()
            acquired = mutex.acquire(timeout=write_budget_s)
            if not acquired:
                return {"status": "WRITE_CONTENDED"}
            remaining_ms = max(
                1,
                int((write_deadline - time.monotonic()) * 1000.0),
            )
            with default_runtime_write_coordinator().lease(
                (DBIdentity.WORLD,),
                owner="day0_hko_source_clock",
                write_class="live",
                deadline_ms=remaining_ms,
                max_hold_ms=max(1, int(write_budget_s * 1000.0)),
            ) as write_lease:
                conn = get_world_connection(write_class="live")
                conn.execute(f"PRAGMA busy_timeout = {remaining_ms}")
                before_changes = int(conn.total_changes)
                commit_started = time.monotonic()
                project_result = project_accumulator_to_v2(
                    conn,
                    "v1.wu-native",
                    DEFAULT_LOG_PATH,
                    snapshot=snapshot,
                )
                if event_enabled:
                    decision_time = datetime.now(timezone.utc)
                    trigger = Day0ExtremeUpdatedTrigger(
                        EventWriter(conn),
                        day0_is_tradeable=day0_is_tradeable_for_scope(
                            str(
                                edli_cfg.get("edli_live_scope")
                                or "forecast_plus_day0"
                            )
                        ),
                        family_admission=family_admission,
                        scan_cities=("Hong Kong",),
                    )
                    results = trigger.scan_observation_instants_rows(
                        observation_conn=conn,
                        settlement_semantics=SettlementSemantics.for_city(hko_city),
                        decision_time=decision_time,
                        received_at=decision_time.isoformat(),
                        limit=4,
                    )
                    inserted_event_ids = tuple(
                        result.event_id for result in results if result.inserted
                    )
                    if inserted_event_ids:
                        placeholders = ",".join("?" for _ in inserted_event_ids)
                        inserted_families = tuple(
                            (
                                str(city),
                                str(target_date),
                                str(metric).lower(),
                            )
                            for city, target_date, metric in conn.execute(
                                f"""
                                SELECT json_extract(payload_json, '$.city'),
                                       json_extract(payload_json, '$.target_date'),
                                       json_extract(payload_json, '$.metric')
                                  FROM opportunity_events
                                 WHERE event_id IN ({placeholders})
                                """,
                                inserted_event_ids,
                            ).fetchall()
                        )
                    conn.commit()
                write_lease.record_commit(
                    commit_ms=(time.monotonic() - commit_started) * 1000.0,
                    rows_changed=max(
                        0,
                        int(conn.total_changes) - before_changes,
                    ),
                )
                poller.acknowledge(prefetch)
    except WriteLeaseTimeout:
        return {"status": "WRITE_CONTENDED"}
    except sqlite3.OperationalError as exc:
        if conn is not None and conn.in_transaction:
            conn.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            return {"status": "WRITE_CONTENDED"}
        raise
    finally:
        if conn is not None:
            conn.close()
        if acquired and mutex is not None:
            mutex.release()

    _bridge_committed_day0_events(
        source="day0_hko_source_clock",
        event_ids=inserted_event_ids,
        families=inserted_families,
    )
    logger.info(
        "K2 hko_source_clock: observed_at=%s target_date=%s written=%s "
        "events_emitted=%d",
        snapshot.observed_at_utc,
        snapshot.target_date,
        project_result.get("written"),
        len(inserted_event_ids),
    )
    return {
        **project_result,
        "status": "COMMITTED",
        "events_emitted": len(inserted_event_ids),
    }


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


@_scheduler_job("ingest_replacement_maintenance")
def _replacement_maintenance_tick():
    """Repair unchanged replacement targets without blocking the source clock."""
    from src.data.bayes_precision_fusion_download import (  # noqa: PLC0415
        bayes_precision_fusion_quota_cooldown_seconds,
    )
    from src.data.replacement_forecast_production import (  # noqa: PLC0415
        _download_replacement_forecast_current_targets_if_needed,
        _enqueue_cycle_advance_reseeds_if_needed,
        _enqueue_fusion_upgrade_reseeds_if_needed,
        _replacement_forecast_live_materialization_queue_config,
    )

    cfg = _replacement_forecast_live_materialization_queue_config()
    if not bool(cfg.get("download_current_targets_enabled", False)):
        return None
    cooldown_seconds = bayes_precision_fusion_quota_cooldown_seconds()
    if cooldown_seconds > 0:
        _defer_replacement_maintenance(float(cooldown_seconds))
        return {
            "status": "REPLACEMENT_MAINTENANCE_QUOTA_COOLDOWN",
            "cooldown_seconds": cooldown_seconds,
        }
    if not _replacement_maintenance_due():
        return {"status": "REPLACEMENT_MAINTENANCE_NOT_DUE"}

    timeout_s = _replacement_current_target_poll_timeout_seconds(
        _replacement_availability_poll_seconds()
    )
    try:
        download_report = _download_replacement_forecast_current_targets_if_needed(
            cfg,
            max_wall_clock_seconds=timeout_s,
        )
    except TimeoutError as exc:
        download_report = {
            "status": "CURRENT_TARGET_DOWNLOAD_TIMEOUT",
            "timeout_seconds": timeout_s,
            "error": str(exc)[:240],
        }
    except Exception as exc:  # noqa: BLE001 - reseed catch-up remains independent
        logger.warning(
            "replacement maintenance current-target repair failed: %s",
            exc,
            exc_info=True,
        )
        download_report = {
            "status": "CURRENT_TARGET_DOWNLOAD_FAILSOFT",
            "error": f"{type(exc).__name__}: {str(exc)[:220]}",
        }

    report: dict[str, object] = {
        "status": "REPLACEMENT_MAINTENANCE_COMPLETED",
        "current_target_download": _compact_replacement_current_target_report(
            download_report
        ),
    }
    maintenance_errors: list[str] = []
    download_status = str(
        download_report.get("status") or ""
        if isinstance(download_report, dict)
        else ""
    )
    if download_status in {
        "CURRENT_TARGET_DOWNLOAD_TIMEOUT",
        "CURRENT_TARGET_DOWNLOAD_FAILSOFT",
    }:
        maintenance_errors.append(f"current_target:{download_status}")
    for prefix, reseed in (
        ("fusion_upgrade", _enqueue_fusion_upgrade_reseeds_if_needed),
        ("cycle_advance", _enqueue_cycle_advance_reseeds_if_needed),
    ):
        try:
            reseed_report = reseed(cfg)
        except Exception as exc:  # noqa: BLE001 - isolate independent repair lanes
            maintenance_errors.append(
                f"{prefix}:{type(exc).__name__}: {str(exc)[:180]}"
            )
            logger.warning("replacement maintenance %s failed: %s", prefix, exc)
            continue
        if reseed_report is not None:
            report[f"{prefix}_status"] = reseed_report.get("status")
            report[f"{prefix}_seeds_enqueued"] = reseed_report.get("seeds_enqueued")
    if maintenance_errors:
        report["status"] = "REPLACEMENT_MAINTENANCE_PARTIAL"
        report["maintenance_errors"] = tuple(maintenance_errors)
    logger.info("replacement maintenance report: %s", report)
    return report


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
        _ingest_station_forecasts_if_due,
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

    # Station-calibrated official forecasts (CWA township / HKO fnd) ingest on THIS lane — re-homed
    # 2026-07-20 after the 2026-06-11 download-lane migration orphaned the call (it lived only in the
    # descheduled forecast-live _replacement_forecast_download_cycle, so cwa_township/hko_fnd went dark
    # 2026-07-17). Due-gated (~3h) + fail-soft: a provider outage never touches the gridded capture.
    try:
        _station_report = _ingest_station_forecasts_if_due(cfg)
        if _station_report:
            logger.info("station-forecast live ingest wrote rows: %s", _station_report)
    except Exception as exc:  # noqa: BLE001 - station ingest must never break the poll
        logger.warning("station-forecast live ingest skipped (fail-soft): %s", exc)

    def _attach_reseed_reports(
        report: dict[str, object],
        *,
        scopes: tuple[tuple[str, str, str], ...] | None = None,
        changed_sources: tuple[str, ...] | None = None,
        include_cycle_advance: bool = True,
        prepared_manifest_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        manifest_snapshot = None
        if scopes is not None:
            manifest_snapshot = prepared_manifest_snapshot or {}
        upgrade_report = (
            _enqueue_fusion_upgrade_reseeds_if_needed(cfg)
            if scopes is None
            else _enqueue_fusion_upgrade_reseeds_if_needed(
                cfg,
                scopes=scopes,
                changed_sources=changed_sources,
                manifest_snapshot=manifest_snapshot,
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
            else _enqueue_cycle_advance_reseeds_if_needed(
                cfg,
                scopes=scopes,
                manifest_snapshot=manifest_snapshot,
            )
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

    def _download_current_targets(
        *,
        max_wall_clock_seconds: float | None = None,
        required_scopes: tuple[tuple[str, str, str], ...] | None = None,
    ):
        current_target_timeout = (
            _replacement_current_target_poll_timeout_seconds(
                _replacement_availability_poll_seconds()
            )
            if max_wall_clock_seconds is None
            else max(0.0, float(max_wall_clock_seconds))
        )
        try:
            kwargs: dict[str, object] = {
                "max_wall_clock_seconds": current_target_timeout,
            }
            if required_scopes is not None:
                kwargs["required_scopes"] = required_scopes
            return _download_replacement_forecast_current_targets_if_needed(
                cfg,
                **kwargs,
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
        report["maintenance_status"] = "REPLACEMENT_MAINTENANCE_DECOUPLED"
        logger.info("replacement source-clock poll current: %s", report)
        return report
    logger.info("replacement source-clock update detected; running download path: %s", source_clock_payload)
    source_clock_anchor_report = None
    anchor_reseed_published = False
    if "ecmwf_ifs" in source_clock_report.updated_sources:
        # The current provider center is the first q input and already has a
        # run-authoritative live-API ladder. Capture it before waiting for the
        # slower Single Runs archive used by the multimodel BPF inputs.
        source_clock_anchor_report = _download_current_targets(
            max_wall_clock_seconds=min(
                10.0,
                _replacement_current_target_poll_timeout_seconds(
                    _replacement_availability_poll_seconds()
                ),
            )
        )
        if (
            isinstance(source_clock_anchor_report, dict)
            and int(source_clock_anchor_report.get("written_manifest_count") or 0) > 0
        ):
            _attach_reseed_reports(source_clock_anchor_report)
            anchor_reseed_published = True
    notified_source_scopes: set[tuple[str, str, str, str]] = set()
    anchor_scopes_attempted: set[tuple[str, str, str]] = set()
    fallback_reseed_published = False
    scoped_reseed_completed = False
    scoped_reseed_summary: dict[str, object] = {}
    publish_state_lock = threading.Lock()
    publish_scope_locks: dict[tuple[str, str, str], threading.Lock] = {}
    fallback_reseed_lock = threading.Lock()

    def _publish_committed_source(
        source: str,
        task_report: object,
    ) -> None:
        nonlocal fallback_reseed_published, scoped_reseed_completed
        raw_scopes = (
            task_report.get("committed_families", ())
            if isinstance(task_report, dict)
            else ()
        )
        candidate_scopes = tuple(
            dict.fromkeys(
                (str(city), str(target_date), str(metric))
                for city, target_date, metric in raw_scopes
            )
        )
        with publish_state_lock:
            scopes = tuple(
                scope
                for scope in candidate_scopes
                if (source, *scope) not in notified_source_scopes
            )
            if scopes:
                notified_source_scopes.update((source, *scope) for scope in scopes)
                anchor_scopes = tuple(
                    scope for scope in scopes if scope not in anchor_scopes_attempted
                )
                anchor_scopes_attempted.update(anchor_scopes)
                scope_locks = tuple(
                    publish_scope_locks.setdefault(scope, threading.Lock())
                    for scope in sorted(scopes)
                )
            elif fallback_reseed_published:
                return
            else:
                fallback_reseed_published = True
                anchor_scopes = ()
                scope_locks = (fallback_reseed_lock,)

        for scope_lock in scope_locks:
            scope_lock.acquire()
        try:
            _publish_committed_source_locked(
                source=source,
                task_report=task_report,
                scopes=scopes,
                anchor_scopes=anchor_scopes,
            )
        finally:
            for scope_lock in reversed(scope_locks):
                scope_lock.release()

    def _publish_committed_source_locked(
        *,
        source: str,
        task_report: object,
        scopes: tuple[tuple[str, str, str], ...],
        anchor_scopes: tuple[tuple[str, str, str], ...],
    ) -> None:
        nonlocal scoped_reseed_completed
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
            manifest_snapshot = None
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
                    written_manifests = tuple(
                        str(path)
                        for path in (anchor_report.get("written_manifests") or ())
                        if str(path).strip()
                    )
                    if written_manifests:
                        manifest_snapshot = {
                            "manifest_paths": written_manifests,
                        }
            _attach_reseed_reports(
                report,
                scopes=scopes,
                changed_sources=(source,),
                prepared_manifest_snapshot=manifest_snapshot,
            )
        else:
            _attach_reseed_reports(report)
        with publish_state_lock:
            for key in (
                "anchor_scope_status",
                "anchor_scope_manifest_count",
                "fusion_upgrade_status",
                "cycle_advance_status",
                "cycle_advance_detail",
            ):
                if key in report:
                    scoped_reseed_summary[key] = report[key]
            for key in (
                "fusion_upgrade_seeds_enqueued",
                "cycle_advance_seeds_enqueued",
            ):
                if key in report:
                    scoped_reseed_summary[key] = int(
                        scoped_reseed_summary.get(key) or 0
                    ) + int(report.get(key) or 0)
            scoped_reseed_completed = True
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
    report.update(scoped_reseed_summary)
    source_clock_anchor_compact = _compact_replacement_current_target_report(
        source_clock_anchor_report
    )
    if source_clock_anchor_compact is not None:
        report["source_clock_anchor_download"] = source_clock_anchor_compact
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
        notification_errors = tuple(
            report.get("source_commit_notification_errors") or ()
        )
        pending_notifications = int(
            report.get("source_commit_notifications_pending") or 0
        )
        if (
            scoped_reseed_completed
            or anchor_reseed_published
            or pending_notifications > 0
        ) and not notification_errors:
            report["reseed_maintenance_status"] = (
                "SOURCE_COMMIT_RESEEDS_DEFERRED"
                if pending_notifications > 0
                else "SOURCE_COMMIT_RESEEDS_PUBLISHED"
                if scoped_reseed_completed
                else "SOURCE_ANCHOR_RESEEDS_PUBLISHED"
            )
        else:
            # No committed callback completed, or at least one callback failed.
            # Preserve the broad scan as the authoritative catch-up path.
            report = _attach_reseed_reports(report)
    cursor_sources = source_clock_scoped_download_cursor_sources(
        report,
        source_clock_report=source_clock_report,
    )
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
# Weekly fitted-artifact refit — source-clock weights, staleness variance,
# shape-age sigma, ens member dependence
# ---------------------------------------------------------------------------

_ARTIFACT_FIT_SCRIPTS = (
    "fit_source_clock_city_weights.py",
    "fit_model_staleness_variance.py",
    "fit_shape_age_sigma.py",
    "fit_ens_member_dependence.py",
)


@_scheduler_job("ingest_artifact_refit")
def _artifact_refit_tick():
    """Weekly walk-forward refit of the four fitted serving artifacts.

    Each fitter is read-only over zeus-forecasts.db (registered read_only_ro_uri in
    db_writer_lock) and writes only its state/<name>/ artifact + ACTIVE.json pointer.
    Consumers hot-reload on the pointer's mtime (loader wrappers, 2021b8bea), so a
    refit lands in live serving on the next call — no daemon restart. Fail-soft
    per-script: one fitter failing must not block the others; the stale artifact
    simply stays active (fail-open consumers already price that)."""
    import subprocess

    venv_python = _etl_subprocess_python()
    scripts_dir = Path(__file__).parent.parent / "scripts"
    for script in _ARTIFACT_FIT_SCRIPTS:
        script_path = scripts_dir / script
        if not script_path.exists():
            logger.warning("[ARTIFACT_REFIT] missing fitter: %s", script)
            continue
        try:
            r = subprocess.run(
                [venv_python, str(script_path)],
                capture_output=True, text=True, timeout=900,
            )
            if r.returncode == 0:
                logger.info("[ARTIFACT_REFIT] %s OK: %s", script, (r.stdout or "").strip()[-300:])
            else:
                logger.warning(
                    "[ARTIFACT_REFIT] %s FAILED (exit=%d): %s",
                    script, r.returncode, (r.stderr or "")[-500:],
                )
        except Exception as e:
            logger.warning("[ARTIFACT_REFIT] %s ERROR: %s", script, e)


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
    day0_hko_poll_seconds = _day0_hko_poll_seconds()
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
        (_k2_hko_tick, "interval", dict(seconds=day0_hko_poll_seconds,
            id="ingest_k2_hko_tick", max_instances=1, coalesce=True,
            misfire_grace_time=max(5, int(day0_hko_poll_seconds * 2)),
            next_run_time=now)),
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
        (_replacement_maintenance_tick, "interval", dict(seconds=60,
            id="ingest_replacement_maintenance", max_instances=1, coalesce=True,
            misfire_grace_time=120, next_run_time=now + timedelta(seconds=60))),
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
        # Weekly Mon 06:00 UTC (post-weekend settlements graded; mirrors the consult's
        # "activate weekly" cadence for the fitted serving artifacts).
        (_artifact_refit_tick, "cron", dict(day_of_week="mon", hour=6, minute=0,
            id="ingest_artifact_refit", max_instances=1, coalesce=True, misfire_grace_time=3600)),
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
        try:
            _shutdown_scheduler_if_running(_scheduler, wait=True)
        finally:
            _close_day0_metar_emitter()
            _close_day0_hko_poller()


if __name__ == "__main__":
    main()
