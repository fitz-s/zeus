# Created: 2026-05-14
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md section 6.1, section 6.2, and section 8 Phase 4.
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

_TRUTHFUL_FAIL_STATUSES = frozenset(
    {
        "download_failed",
        "empty_ingest",
        "extract_failed",
        "skipped_lock_held",
        "bad_target_date",
    }
)


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


def run_opendata_track(
    track: str,
    *,
    _locks_dir_override: Path | None = None,
    _collector: Callable[..., dict] | None = None,
    _source_paused: Callable[[str], bool] | None = None,
) -> dict:
    from src.data.dual_run_lock import OPENDATA_DAEMON_LOCK_KEY, acquire_lock
    from src.data.ecmwf_open_data import SOURCE_ID, collect_open_ens_cycle

    source_paused = _source_paused or _is_source_paused
    if source_paused(SOURCE_ID):
        logger.info("forecast-live OpenData %s paused_by_control_plane", track)
        return {"status": "paused_by_control_plane", "source": SOURCE_ID, "track": track}

    with acquire_lock(OPENDATA_DAEMON_LOCK_KEY, _locks_dir_override=_locks_dir_override) as acquired:
        if not acquired:
            logger.info("forecast-live OpenData %s skipped_lock_held", track)
            return {"status": "skipped_lock_held", "source": SOURCE_ID, "track": track}
        collector = _collector or collect_open_ens_cycle
        return collector(track=track)


@_scheduler_job(FORECAST_LIVE_DAILY_HIGH_JOB_ID)
def _opendata_mx2t6_cycle() -> dict:
    return run_opendata_track("mx2t6_high")


@_scheduler_job(FORECAST_LIVE_DAILY_LOW_JOB_ID)
def _opendata_mn2t6_cycle() -> dict:
    return run_opendata_track("mn2t6_low")


@_scheduler_job(FORECAST_LIVE_STARTUP_JOB_ID)
def _opendata_startup_catch_up() -> dict:
    results = {
        "mx2t6_high": run_opendata_track("mx2t6_high"),
        "mn2t6_low": run_opendata_track("mn2t6_low"),
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
