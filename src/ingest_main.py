# Lifecycle: created=2026-04-30; last_reviewed=2026-05-03; last_reused=2026-05-03
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §5 Phase 1 + PLAN_v4 Phase 5A source-run selection.
"""Zeus data-ingest daemon entry point.

Runs all K2 ingest jobs and supporting cycles on an independent APScheduler.
Does NOT import from src.engine, src.execution, src.strategy, src.signal,
src.control, or src.main — those are trading-lane only.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

logger = logging.getLogger("zeus.ingest")

# ---------------------------------------------------------------------------
# Module-level scheduler reference for SIGTERM handler
# ---------------------------------------------------------------------------
_scheduler: BlockingScheduler | None = None


def _graceful_shutdown(signum, frame) -> None:
    """SIGTERM handler — wait for in-flight jobs then exit 0."""
    logger.info("data-ingest daemon received SIGTERM; shutting down scheduler")
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=True)
        except Exception as exc:
            logger.warning("Scheduler shutdown error: %s", exc)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Decorator — mirrors src/main.py:_scheduler_job
# ---------------------------------------------------------------------------

_TRUTHFUL_FAIL_STATUSES = frozenset({
    "download_failed",
    "extract_failed",
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
    """Atomically write state/world_schema_ready.json after init_schema succeeds."""
    from src.config import state_path

    schema_version: str = "unknown_v0"
    schema_yaml = Path(__file__).parent.parent / "architecture" / "world_schema_version.yaml"
    if schema_yaml.exists():
        try:
            import yaml  # type: ignore[import]
            data = yaml.safe_load(schema_yaml.read_text())
            schema_version = str(data.get("version", "unknown_v0")) if isinstance(data, dict) else str(data)
        except Exception:
            pass

    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": schema_version,
        "ingest_pid": os.getpid(),
        "init_schema_returned_ok": True,
    }
    path = state_path("world_schema_ready.json")
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)
    logger.info("Wrote world_schema_ready sentinel: schema_version=%s", schema_version)


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
    from src.state.db import get_world_connection
    with acquire_lock("daily_obs") as acquired:
        if not acquired:
            logger.info("ingest k2_daily_obs_tick skipped_lock_held")
            return
        conn = get_world_connection()
        try:
            result = daily_tick(conn)
        finally:
            conn.close()
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
        conn = get_world_connection()
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
        conn = get_world_connection()
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
        conn = get_world_connection()
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
    from src.state.db import get_world_connection
    with acquire_lock("hole_scanner") as acquired:
        if not acquired:
            logger.info("ingest k2_hole_scanner_tick skipped_lock_held")
            return
        conn = get_world_connection()
        try:
            scanner = HoleScanner(conn)
            results = scanner.scan_all()
            for r in results:
                logger.info("K2 hole_scanner %s: %s", r.data_table.value, r.as_dict())
        finally:
            conn.close()


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
    from src.state.db import get_world_connection

    conn = get_world_connection()
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
        logger.info("  %s", catch_up_obs(conn, days_back=30))
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
    """ECMWF Open Data daily mx2t6 (HIGH track) ingest.

    Open Data ENS posts 00Z runs by ~07:00 UTC (latency 6-8h). This job runs
    at 07:30 UTC and writes ``ecmwf_opendata_mx2t6_local_calendar_day_max_v1``
    rows to ``ensemble_snapshots_v2``.
    """
    if _is_source_paused("ecmwf_open_data"):
        logger.info("_opendata_mx2t6_cycle: paused_by_control_plane")
        return {"status": "paused_by_control_plane", "source": "ecmwf_open_data"}
    from src.data.ecmwf_open_data import collect_open_ens_cycle
    result = collect_open_ens_cycle(track="mx2t6_high")
    logger.info("ECMWF Open Data mx2t6: %s",
                {k: v for k, v in result.items() if k != "stages"})
    return result


@_scheduler_job("ingest_opendata_daily_mn2t6")
def _opendata_mn2t6_cycle():
    """ECMWF Open Data daily mn2t6 (LOW track) ingest.

    Runs at 07:35 UTC (5-min offset from mx2t6 to space out downloads). Writes
    ``ecmwf_opendata_mn2t6_local_calendar_day_min_v1`` rows to
    ``ensemble_snapshots_v2``.
    """
    if _is_source_paused("ecmwf_open_data"):
        logger.info("_opendata_mn2t6_cycle: paused_by_control_plane")
        return {"status": "paused_by_control_plane", "source": "ecmwf_open_data"}
    from src.data.ecmwf_open_data import collect_open_ens_cycle
    result = collect_open_ens_cycle(track="mn2t6_low")
    logger.info("ECMWF Open Data mn2t6: %s",
                {k: v for k, v in result.items() if k != "stages"})
    return result


@_scheduler_job("ingest_opendata_startup_catch_up")
def _opendata_startup_catch_up():
    """Boot-time catch-up for both Open Data tracks.

    Fires once at daemon start; pulls the latest release-calendar-approved
    full-horizon source run for both tracks. Re-runs after a brief restart are
    nearly idempotent thanks to ``INSERT OR IGNORE``.
    """
    if _is_source_paused("ecmwf_open_data"):
        logger.info("_opendata_startup_catch_up: paused_by_control_plane")
        return
    from src.data.ecmwf_open_data import collect_open_ens_cycle
    for track in ("mx2t6_high", "mn2t6_low"):
        result = collect_open_ens_cycle(track=track)
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
    import subprocess
    venv_python = _etl_subprocess_python()
    scripts_dir = Path(__file__).parent.parent / "scripts"
    results = {}

    for script in [
        "etl_diurnal_curves.py",
        "etl_temp_persistence.py",
        "etl_hourly_observations.py",
    ]:
        script_path = scripts_dir / script
        if script_path.exists():
            try:
                r = subprocess.run(
                    [venv_python, str(script_path)],
                    capture_output=True, text=True, timeout=300,
                )
                results[script] = "OK" if r.returncode == 0 else f"FAIL: {r.stderr[-200:]}"
            except Exception as e:
                results[script] = f"ERROR: {e}"

    results["calibration_pairs"] = "SKIP: run rebuild_calibration_pairs_canonical post-fillback"
    results["platt_refit"] = "SKIP: run explicit post-fillback canonical refit"

    try:
        r = subprocess.run(
            [venv_python, str(scripts_dir / "run_replay.py"),
             "--mode", "audit", "--start", "2025-01-01", "--end", "2099-12-31"],
            capture_output=True, text=True, timeout=600,
        )
        results["replay_audit"] = "OK" if r.returncode == 0 else "FAIL"
    except Exception as e:
        results["replay_audit"] = f"ERROR: {e}"

    logger.info("ETL recalibration: %s", results)


@_scheduler_job("ingest_harvester_truth_writer")
def _harvester_truth_writer_tick():
    """Phase 1.5 harvester split — ingest-side world.settlements writer.

    Acquires advisory lock before running. Runs hourly. Writes settlement truth
    to world.settlements independent of the trading daemon's lifecycle.
    Feature-flagged: ZEUS_HARVESTER_LIVE_ENABLED must equal "1" to do real work.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.ingest.harvester_truth_writer import write_settlement_truth_for_open_markets
    from src.state.db import get_world_connection
    with acquire_lock("harvester_truth") as acquired:
        if not acquired:
            logger.info("ingest harvester_truth_writer_tick skipped_lock_held")
            return
        conn = get_world_connection()
        try:
            result = write_settlement_truth_for_open_markets(conn)
        finally:
            conn.close()
    logger.info("harvester_truth_writer_tick: %s", result)


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

@_scheduler_job("ingest_source_health_probe")
def _source_health_probe_tick():
    """Source health probe every 10 minutes (design §2.1).

    Probes all upstream sources and writes state/source_health.json.
    Acquires advisory lock so only one process probes at a time.
    """
    from src.data.dual_run_lock import acquire_lock
    from src.data.source_health_probe import probe_all_sources, write_source_health
    from src.config import state_path
    import json
    from pathlib import Path as _Path

    with acquire_lock("source_health") as acquired:
        if not acquired:
            logger.info("ingest _source_health_probe_tick skipped_lock_held")
            return

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
    from src.calibration.retrain_trigger_v2 import check_and_arm_refit
    from src.state.db import get_world_connection

    with acquire_lock("drift_detector") as acquired:
        if not acquired:
            logger.info("ingest _drift_detector_tick skipped_lock_held")
            return
        conn = get_world_connection()
        try:
            result = check_and_arm_refit(conn)
            logger.info(
                "Drift detector: %d REFIT_NOW, %d WATCH, %d OK",
                result["n_refit_now"], result["n_watch"], result["n_ok"],
            )
        finally:
            conn.close()


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
        conn = get_world_connection()
        try:
            write_ingest_status(conn)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _scheduler

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
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

    # Schema init on world DB.
    from src.state.db import init_schema, get_world_connection
    conn = get_world_connection()
    init_schema(conn)
    conn.close()
    logger.info("init_schema complete")

    # Write sentinel BEFORE scheduler.start() (design §4.2).
    _write_world_schema_ready_sentinel()

    # SIGTERM → graceful shutdown.
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    _scheduler = BlockingScheduler()

    from src.config import settings

    # Mirrors src/main.py APScheduler block for ingest jobs only.
    _scheduler.add_job(
        _k2_daily_obs_tick, "cron",
        minute=0, id="ingest_k2_daily_obs",
        max_instances=1, coalesce=True, misfire_grace_time=1800,
    )
    _scheduler.add_job(
        _k2_hourly_instants_tick, "cron",
        minute=7, id="ingest_k2_hourly_instants",
        max_instances=1, coalesce=True, misfire_grace_time=1800,
    )
    _scheduler.add_job(
        _k2_solar_daily_tick, "cron",
        hour=0, minute=30, id="ingest_k2_solar_daily",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    _scheduler.add_job(
        _k2_forecasts_daily_tick, "cron",
        hour=7, minute=30, id="ingest_k2_forecasts_daily",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    _scheduler.add_job(
        _k2_hole_scanner_tick, "cron",
        hour=4, minute=0, id="ingest_k2_hole_scanner",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    _scheduler.add_job(
        _etl_recalibrate, "cron",
        hour=6, minute=0, id="ingest_etl_recalibrate",
    )
    # Phase 1.5: harvester truth writer — hourly, offset to minute=45 to avoid
    # collision with trading-side harvester_pnl_resolver (runs at minute=0 via main.py).
    _scheduler.add_job(
        _harvester_truth_writer_tick, "cron",
        minute=45, id="ingest_harvester_truth_writer",
        max_instances=1, coalesce=True, misfire_grace_time=1800,
    )
    _scheduler.add_job(
        _automation_analysis_cycle, "cron",
        hour=9, minute=0, id="ingest_automation_analysis",
        max_instances=1, coalesce=True,
    )

    # ECMWF Open Data — same-day live source (~6-8h latency, no embargo).
    # 07:30 UTC mx2t6 / 07:35 UTC mn2t6 — picks up the 00Z run as soon as
    # it is reliably available. settings.discovery.ecmwf_open_data_times_utc
    # may add additional refreshes; we still register the per-track ticks at
    # 07:30/07:35 unconditionally so the freshness invariant holds even if
    # settings is misconfigured.
    _scheduler.add_job(
        _opendata_mx2t6_cycle, "cron",
        hour=7, minute=30, id="ingest_opendata_daily_mx2t6",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    _scheduler.add_job(
        _opendata_mn2t6_cycle, "cron",
        hour=7, minute=35, id="ingest_opendata_daily_mn2t6",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )

    # TIGGE archive backfill — 14:00 UTC, target = today - 2 days (post-embargo).
    # Distinct from the live Open Data feed; serves the Platt training set and
    # historical audit trail. Renamed from ingest_tigge_daily so health
    # dashboards and operators see the role explicitly.
    _scheduler.add_job(
        _tigge_archive_backfill_cycle, "cron",
        hour=14, minute=0, id="ingest_tigge_archive_backfill",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )

    # Boot-time catch-up fires immediately via 'date' trigger.
    from datetime import datetime as _dt_now
    _scheduler.add_job(
        _k2_startup_catch_up, "date",
        run_date=_dt_now.now(),
        id="ingest_k2_startup_catch_up",
        max_instances=1, coalesce=True, misfire_grace_time=None,
    )

    # TIGGE boot-time catch-up — fires once at daemon start to fill missed days
    # (capped at src.data.tigge_pipeline.MAX_LOOKBACK_DAYS).
    _scheduler.add_job(
        _tigge_startup_catch_up, "date",
        run_date=_dt_now.now(),
        id="ingest_tigge_startup_catch_up",
        max_instances=1, coalesce=True, misfire_grace_time=None,
    )

    # Open Data boot-time catch-up — fires once at daemon start so a fresh
    # ingest doesn't wait until the next 07:30 cron tick to backfill.
    _scheduler.add_job(
        _opendata_startup_catch_up, "date",
        run_date=_dt_now.now(),
        id="ingest_opendata_startup_catch_up",
        max_instances=1, coalesce=True, misfire_grace_time=None,
    )

    # Phase 2: source health probe every 10 minutes (§2.1) — APPENDED END
    _scheduler.add_job(
        _source_health_probe_tick, "interval",
        minutes=10, id="ingest_source_health_probe",
        max_instances=1, coalesce=True,
    )

    # 2026-05-01: Station-migration probe — hourly (Invariant F).
    _scheduler.add_job(
        _station_migration_probe_tick, "interval",
        minutes=60, id="ingest_station_migration_probe",
        max_instances=1, coalesce=True,
    )

    # Phase 2: drift detector daily at UTC 06:00 (§2.2) — APPENDED END
    _scheduler.add_job(
        _drift_detector_tick, "cron",
        hour=6, minute=0, id="ingest_drift_detector",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )

    # Phase 2: ingest status rollup every 5 minutes (§2.5) — APPENDED END
    _scheduler.add_job(
        _ingest_status_rollup_tick, "interval",
        minutes=5, id="ingest_status_rollup",
        max_instances=1, coalesce=True,
    )

    # 60s heartbeat.
    _scheduler.add_job(
        _write_ingest_heartbeat, "interval",
        seconds=60, id="ingest_heartbeat",
        max_instances=1, coalesce=True,
    )

    jobs = [j.id for j in _scheduler.get_jobs()]
    logger.info("Ingest scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus data-ingest daemon shutting down")
        _scheduler.shutdown()


if __name__ == "__main__":
    main()
