# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md section 6.1, section 6.2, and section 8 Phase 4; Phase 6 durable work journaling.
"""Dedicated OpenData live forecast producer daemon.

This module owns only the ECMWF OpenData live forecast scheduler. It does not
schedule TIGGE archive backfill, calibration refit, settlement truth, market
scan, risk, execution, evaluator, or venue work.
"""

from __future__ import annotations

import functools
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("zeus.forecast_live")

_scheduler: Any | None = None

FORECAST_LIVE_DAILY_HIGH_JOB_ID = "forecast_live_opendata_daily_mx2t6"
FORECAST_LIVE_DAILY_LOW_JOB_ID = "forecast_live_opendata_daily_mn2t6"
FORECAST_LIVE_STARTUP_JOB_ID = "forecast_live_opendata_startup_catch_up"

FORECAST_LIVE_JOB_IDS = frozenset(
    {
        FORECAST_LIVE_DAILY_HIGH_JOB_ID,
        FORECAST_LIVE_DAILY_LOW_JOB_ID,
        FORECAST_LIVE_STARTUP_JOB_ID,
    }
)

FORECAST_LIVE_WORK_JOB_NAME_BY_TRACK = {
    "mx2t6_high": "forecast_live_opendata_mx2t6_high",
    "mn2t6_low": "forecast_live_opendata_mn2t6_low",
}

_TRUTHFUL_FAIL_STATUSES = frozenset(
    {
        "download_failed",
        "empty_ingest",
        "extract_failed",
        "skipped_lock_held",
        "bad_target_date",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _graceful_shutdown(signum, frame) -> None:
    logger.info("forecast-live daemon received SIGTERM; shutting down scheduler")
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=True)
        except Exception as exc:
            logger.warning("forecast-live scheduler shutdown error: %s", exc)
    sys.exit(0)


def _classify_result(result) -> tuple[bool, str | None]:
    if not isinstance(result, dict):
        return False, None
    status = str(result.get("status", "")).lower()
    if status in _TRUTHFUL_FAIL_STATUSES:
        return True, status + (": " + str(result.get("error")) if result.get("error") else "")
    if status in {"paused_by_control_plane", "noop_no_dates"}:
        return False, None
    stages = result.get("stages") or []
    for stage in stages:
        if isinstance(stage, dict) and stage.get("ok") is False:
            return True, f"stage_failed:{stage.get('label', '?')}:{stage.get('error', '?')}"
    tracks = result.get("tracks") or {}
    if isinstance(tracks, dict):
        for track_result in tracks.values():
            failed, reason = _classify_result(track_result)
            if failed:
                return True, reason
    return False, None


def _scheduler_job(job_name: str):
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                from src.observability.scheduler_health import _write_scheduler_health

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


def _is_source_paused(source_id: str) -> bool:
    try:
        from src.control.control_plane import read_ingest_control_state

        state = read_ingest_control_state()
        return source_id in state.get("paused_sources", set())
    except Exception as exc:
        logger.warning("forecast-live pause check failed for %s: %s", source_id, exc)
        return False


def _forecast_work_identity(track: str, *, now_utc: datetime) -> dict[str, object]:
    from src.data.ecmwf_open_data import SOURCE_ID, STEP_HOURS, TRACKS
    from src.data.release_calendar import (
        cycle_profile_for_hour,
        get_entry,
        select_source_run_for_target_horizon,
    )

    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    decision, metadata = select_source_run_for_target_horizon(
        now_utc=now_utc,
        source_id=SOURCE_ID,
        track=track,
        required_max_step_hours=max(STEP_HOURS),
    )
    selected_cycle = metadata.get("selected_cycle_time")
    if not isinstance(selected_cycle, datetime):
        selected_cycle = now_utc
    horizon_profile = metadata.get("horizon_profile")
    if not horizon_profile:
        entry = get_entry(SOURCE_ID, track)
        profile = cycle_profile_for_hour(entry, selected_cycle.hour) if entry is not None else None
        horizon_profile = profile.horizon_profile if profile is not None else None
    horizon_profile = str(horizon_profile or "unknown")
    return {
        "decision": decision,
        "metadata": metadata,
        "job_name": FORECAST_LIVE_WORK_JOB_NAME_BY_TRACK[track],
        "source_id": SOURCE_ID,
        "track": track,
        "scheduled_for": selected_cycle.astimezone(timezone.utc),
        "release_calendar_key": f"{SOURCE_ID}:{track}:{horizon_profile}",
        "safe_fetch_not_before": metadata.get("next_safe_fetch_at"),
    }


def _job_run_id(identity: dict[str, object]) -> str:
    scheduled_for = identity["scheduled_for"]
    if isinstance(scheduled_for, datetime):
        scheduled = scheduled_for.isoformat()
    else:
        scheduled = str(scheduled_for)
    return (
        f"{identity['job_name']}:{identity['source_id']}:{identity['track']}:"
        f"{scheduled}:{identity['release_calendar_key']}"
    )


def _job_status_from_result(result: dict | None) -> tuple[str, str | None, int, int]:
    if not isinstance(result, dict):
        return "FAILED", "RESULT_MISSING", 0, 1
    status = str(result.get("status", "")).lower()
    source_run_status = str(result.get("source_run_status", "")).upper()
    source_run_completeness = str(result.get("source_run_completeness", "")).upper()
    rows_written = int(result.get("snapshots_inserted") or result.get("coverage_written") or 0)
    if source_run_status == "PARTIAL" or source_run_completeness == "PARTIAL":
        return "PARTIAL", str(result.get("reason") or result.get("reason_code") or "PARTIAL"), rows_written, 0
    if source_run_status == "FAILED" or source_run_completeness == "MISSING":
        return "FAILED", str(result.get("error") or result.get("reason_code") or "FAILED"), rows_written, 1
    if status == "ok":
        return "SUCCESS", None, rows_written, 0
    if status == "partial":
        return "PARTIAL", str(result.get("reason") or "PARTIAL"), rows_written, 0
    if status == "skipped_not_released":
        return "SKIPPED_NOT_RELEASED", str(result.get("reason") or "SKIPPED_NOT_RELEASED"), 0, 0
    if status == "skipped_lock_held":
        return "SKIPPED_LOCK_HELD", "SKIPPED_LOCK_HELD", 0, 0
    if status in _TRUTHFUL_FAIL_STATUSES:
        return "FAILED", str(result.get("error") or result.get("reason") or status), rows_written, 1
    failed, reason = _classify_result(result)
    if failed:
        return "FAILED", reason or status or "FAILED", rows_written, 1
    return "SUCCESS", None, rows_written, 0


def _write_job_run(
    conn,
    *,
    identity: dict[str, object],
    status: str,
    now_utc: datetime,
    result: dict | None = None,
    reason_code: str | None = None,
    started_at: datetime | None = None,
    lock_acquired_at: datetime | None = None,
    lock_key: str = "opendata_live_forecast",
) -> None:
    from src.state.job_run_repo import write_job_run

    if result is None:
        result = {}
    rows_written = int(result.get("snapshots_inserted") or result.get("coverage_written") or 0)
    rows_failed_raw = result.get("rows_failed")
    rows_failed = int(rows_failed_raw) if rows_failed_raw is not None else (1 if status == "FAILED" else 0)
    write_job_run(
        conn,
        job_run_id=_job_run_id(identity),
        job_name=str(identity["job_name"]),
        plane="forecast",
        scheduled_for=identity["scheduled_for"],
        started_at=started_at,
        finished_at=now_utc if status != "RUNNING" else None,
        lock_key=lock_key,
        lock_acquired_at=lock_acquired_at,
        status=status,
        reason_code=reason_code,
        rows_written=rows_written,
        rows_failed=rows_failed,
        source_run_id=result.get("source_run_id") if isinstance(result.get("source_run_id"), str) else None,
        source_id=str(identity["source_id"]),
        track=str(identity["track"]),
        release_calendar_key=(
            result.get("release_calendar_key")
            if isinstance(result.get("release_calendar_key"), str)
            else str(identity["release_calendar_key"])
        ),
        safe_fetch_not_before=identity.get("safe_fetch_not_before"),
        expected_scope_json={
            "selection": identity.get("metadata"),
            "release_calendar_key": identity.get("release_calendar_key"),
        },
        affected_scope_json={
            "track": identity.get("track"),
            "forecast_track": result.get("forecast_track"),
            "status": result.get("status"),
        },
        readiness_impacts_json={
            "coverage_written": result.get("coverage_written"),
            "producer_readiness_written": result.get("producer_readiness_written"),
        },
        readiness_recomputed_at=now_utc if result.get("producer_readiness_written") else None,
        meta_json={"daemon": "forecast-live", "collector_status": result.get("status")},
    )


def run_opendata_track(
    track: str,
    *,
    _locks_dir_override: Path | None = None,
    _collector: Callable[..., dict] | None = None,
    _source_paused: Callable[[str], bool] | None = None,
    _job_conn=None,
    _now_utc: datetime | None = None,
) -> dict:
    from src.data.dual_run_lock import OPENDATA_DAEMON_LOCK_KEY, acquire_lock
    from src.data.ecmwf_open_data import SOURCE_ID, collect_open_ens_cycle

    source_paused = _source_paused or _is_source_paused
    if source_paused(SOURCE_ID):
        logger.info("forecast-live OpenData %s paused_by_control_plane", track)
        return {"status": "paused_by_control_plane", "source": SOURCE_ID, "track": track}

    from src.data.release_calendar import FetchDecision

    now = (_now_utc or _utcnow()).astimezone(timezone.utc)
    identity = _forecast_work_identity(track, now_utc=now)
    decision = identity["decision"]
    if _job_conn is not None and decision is not FetchDecision.FETCH_ALLOWED:
        status = "SKIPPED_NOT_RELEASED" if decision is FetchDecision.SKIPPED_NOT_RELEASED else "FAILED"
        result = {"status": decision.value.lower(), "selection": identity.get("metadata")}
        _write_job_run(
            _job_conn,
            identity=identity,
            status=status,
            now_utc=now,
            result=result,
            reason_code=getattr(decision, "value", str(decision)),
            lock_key=OPENDATA_DAEMON_LOCK_KEY,
        )
        return {
            "status": decision.value.lower(),
            "source": SOURCE_ID,
            "track": track,
            "selection": identity.get("metadata"),
        }

    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=_locks_dir_override) as acquired:
        if not acquired:
            logger.info("forecast-live OpenData %s skipped_lock_held", track)
            result = {"status": "skipped_lock_held", "source": SOURCE_ID, "track": track}
            if _job_conn is not None:
                _write_job_run(
                    _job_conn,
                    identity=identity,
                    status="SKIPPED_LOCK_HELD",
                    now_utc=now,
                    result=result,
                    reason_code="SKIPPED_LOCK_HELD",
                    lock_key=OPENDATA_DAEMON_LOCK_KEY,
                )
            return result
        collector = _collector or collect_open_ens_cycle
        lock_acquired_at = _utcnow()
        if _job_conn is not None:
            _write_job_run(
                _job_conn,
                identity=identity,
                status="RUNNING",
                now_utc=now,
                started_at=now,
                lock_acquired_at=lock_acquired_at,
                lock_key=OPENDATA_DAEMON_LOCK_KEY,
            )
        try:
            result = collector(track=track)
        except Exception as exc:
            if _job_conn is not None:
                _write_job_run(
                    _job_conn,
                    identity=identity,
                    status="FAILED",
                    now_utc=_utcnow(),
                    result={"status": "exception"},
                    reason_code=f"EXCEPTION:{type(exc).__name__}:{exc}",
                    started_at=now,
                    lock_acquired_at=lock_acquired_at,
                    lock_key=OPENDATA_DAEMON_LOCK_KEY,
                )
            raise
        if _job_conn is not None:
            status, reason_code, rows_written, rows_failed = _job_status_from_result(result)
            enriched = {
                **result,
                "snapshots_inserted": result.get("snapshots_inserted", rows_written),
                "rows_failed": rows_failed,
            }
            _write_job_run(
                _job_conn,
                identity=identity,
                status=status,
                now_utc=_utcnow(),
                result=enriched,
                reason_code=reason_code,
                started_at=now,
                lock_acquired_at=lock_acquired_at,
                lock_key=OPENDATA_DAEMON_LOCK_KEY,
            )
        return result


def _run_journaled_opendata_track(track: str) -> dict:
    from src.state.db import get_forecasts_connection

    conn = get_forecasts_connection(write_class="bulk")
    try:
        result = run_opendata_track(track, _job_conn=conn)
        conn.commit()
        return result
    except Exception:
        conn.commit()
        raise
    finally:
        conn.close()


@_scheduler_job(FORECAST_LIVE_DAILY_HIGH_JOB_ID)
def _opendata_mx2t6_cycle() -> dict:
    return _run_journaled_opendata_track("mx2t6_high")


@_scheduler_job(FORECAST_LIVE_DAILY_LOW_JOB_ID)
def _opendata_mn2t6_cycle() -> dict:
    return _run_journaled_opendata_track("mn2t6_low")


@_scheduler_job(FORECAST_LIVE_STARTUP_JOB_ID)
def _opendata_startup_catch_up() -> dict:
    results = {
        "mx2t6_high": _run_journaled_opendata_track("mx2t6_high"),
        "mn2t6_low": _run_journaled_opendata_track("mn2t6_low"),
    }
    failed, reason = _classify_result({"tracks": results})
    return {"status": "partial" if failed else "ok", "reason": reason, "tracks": results}


def forecast_live_job_specs(
    *,
    startup_run_date: datetime | None = None,
) -> tuple[tuple[Callable[..., object], str, dict[str, object]], ...]:
    startup_at = startup_run_date or datetime.now(timezone.utc)
    return (
        (
            _opendata_mx2t6_cycle,
            "cron",
            {
                "hour": 7,
                "minute": 30,
                "id": FORECAST_LIVE_DAILY_HIGH_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 3600,
            },
        ),
        (
            _opendata_mn2t6_cycle,
            "cron",
            {
                "hour": 7,
                "minute": 35,
                "id": FORECAST_LIVE_DAILY_LOW_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 3600,
            },
        ),
        (
            _opendata_startup_catch_up,
            "date",
            {
                "run_date": startup_at,
                "id": FORECAST_LIVE_STARTUP_JOB_ID,
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": None,
                "executor": "fast",
            },
        ),
    )


def build_scheduler(*, startup_run_date: datetime | None = None):
    from apscheduler.executors.pool import ThreadPoolExecutor as _APSchedulerThreadPoolExecutor
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler(
        timezone=timezone.utc,
        executors={
            "default": _APSchedulerThreadPoolExecutor(max_workers=1),
            "fast": _APSchedulerThreadPoolExecutor(max_workers=1),
        },
    )
    for func, trigger, kwargs in forecast_live_job_specs(startup_run_date=startup_run_date):
        scheduler.add_job(func, trigger, **kwargs)
    return scheduler


def main() -> None:
    global _scheduler

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger.info("Zeus forecast-live daemon starting")

    from src.data.proxy_health import bypass_dead_proxy_env_vars
    from src.state.db import (
        assert_schema_current_forecasts,
        get_forecasts_connection,
        init_schema_forecasts,
    )

    bypass_dead_proxy_env_vars()
    conn = get_forecasts_connection(write_class="bulk")
    try:
        init_schema_forecasts(conn)
        assert_schema_current_forecasts(conn)
        conn.commit()
    finally:
        conn.close()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    _scheduler = build_scheduler()
    jobs = [job.id for job in _scheduler.get_jobs()]
    logger.info("Forecast-live scheduler ready. %d jobs: %s", len(jobs), jobs)
    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus forecast-live daemon shutting down")
        _scheduler.shutdown()


if __name__ == "__main__":
    main()
