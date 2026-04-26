"""Zeus main entry point. Blueprint v2 §9.1.

All discovery modes go through the same CycleRunner with different DiscoveryMode values.
The lifecycle is identical for all modes — only scanner parameters differ.
"""

# Created: pre-Phase-0 (K2 scheduler wiring via 27bedbd; P9A run_mode observability via 7081634)
# Last reused/audited: 2026-04-21
# Authority basis: Phase 10 DT-close B047 — docs/operations/task_2026-04-16_dual_track_metric_spine/phase10_evidence/SCAFFOLD_B047_scheduler_observability.md

import functools
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from src.config import cities_by_name, get_mode, settings
from src.engine.cycle_runner import run_cycle
from src.engine.discovery_mode import DiscoveryMode
from src.observability.scheduler_health import _write_scheduler_health
from src.state.db import init_schema, get_world_connection, get_trade_connection

logger = logging.getLogger("zeus")

# Cross-mode lock: prevents two discovery modes from reading/writing portfolio concurrently
_cycle_lock = threading.Lock()


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


def _etl_subprocess_python() -> str:
    candidate = Path(__file__).parent.parent / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


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
    from src.execution.harvester import run_harvester
    result = run_harvester()
    logger.info("Harvester: %s", result)


@_scheduler_job("k2_daily_obs")
def _k2_daily_obs_tick():
    """K2 daily-observations tick — replaces legacy wu_daily_collector.

    Fires every hour. Inside:
    - WU cities whose local peak+4h window is active get their last
      completed local day fetched via the WU ICAO historical endpoint
    - HKO current+prior month refreshed once per day (gated to UTC 02:00)
    - All writes flow through ObservationAtom + IngestionGuard (no Layer 3)
    - data_coverage is updated in the same transaction as the physical row
    """
    from src.data.daily_obs_append import daily_tick
    from src.state.db import get_world_connection
    conn = get_world_connection()
    try:
        result = daily_tick(conn)
    finally:
        conn.close()
    logger.info("K2 daily_obs_tick: %s", result)


@_scheduler_job("k2_hourly_instants")
def _k2_hourly_instants_tick():
    """K2 hourly Open-Meteo archive tick for observation_instants.

    Sweeps all 46 cities with a per-city dynamic end_date (each city's
    most recently completed local day). 3-day rolling window allows
    Open-Meteo archive ~2-3 day delay + catches promotions.
    """
    from src.data.hourly_instants_append import hourly_tick
    from src.state.db import get_world_connection
    conn = get_world_connection()
    try:
        result = hourly_tick(conn)
    finally:
        conn.close()
    logger.info("K2 hourly_instants_tick: %s", result)


@_scheduler_job("k2_solar_daily")
def _k2_solar_daily_tick():
    """K2 daily sunrise/sunset refresh — once per day (UTC 00:30).

    Fetches [today, today+14] per city. Deterministic astronomical data
    so no backoff / retry semantics are needed beyond network errors.
    """
    from src.data.solar_append import daily_tick
    from src.state.db import get_world_connection
    conn = get_world_connection()
    try:
        result = daily_tick(conn)
    finally:
        conn.close()
    logger.info("K2 solar_daily_tick: %s", result)


@_scheduler_job("k2_forecasts_daily")
def _k2_forecasts_daily_tick():
    """K2 daily NWP forecasts refresh — once per day (UTC 07:30).

    Fires after ECMWF 00Z and GFS 06Z runs are populated in the
    Previous Runs API (empirically ~UTC 07:00). Fetches [today-3,
    today+7] × 5 models × 7 leads per city.
    """
    from src.data.forecasts_append import daily_tick
    from src.state.db import get_world_connection
    conn = get_world_connection()
    try:
        result = daily_tick(conn)
    finally:
        conn.close()
    logger.info("K2 forecasts_daily_tick: %s", result)


@_scheduler_job("k2_hole_scanner")
def _k2_hole_scanner_tick():
    """K2 hole scanner daily patrol — runs hole_scanner.scan_all().

    Finds physical-table rows not yet in data_coverage (self-seed from
    critic S2#2) and writes MISSING rows for expected-but-absent ones.
    The appenders pick up the new MISSING rows on their next tick via
    find_pending_fills. Logs a compact summary per data_table.
    """
    from src.data.hole_scanner import HoleScanner
    from src.state.db import get_world_connection

    conn = get_world_connection()
    try:
        scanner = HoleScanner(conn)
        results = scanner.scan_all()
        for r in results:
            logger.info("K2 hole_scanner %s: %s", r.data_table.value, r.as_dict())
    finally:
        conn.close()


@_scheduler_job("k2_startup_catch_up")
def _k2_startup_catch_up():
    """K2 boot-time hole filler — runs once at daemon start.

    For each of the 4 data tables, calls the module's catch_up_missing()
    which queries data_coverage for MISSING/retry-ready FAILED rows in
    the last 30 days and fills them. This handles daemon downtime gaps
    without human intervention.
    """
    from src.data.daily_obs_append import catch_up_missing as catch_up_obs
    from src.data.hourly_instants_append import catch_up_missing as catch_up_hourly
    from src.data.solar_append import catch_up_missing as catch_up_solar
    from src.data.forecasts_append import catch_up_missing as catch_up_forecasts
    from src.state.db import get_world_connection

    conn = get_world_connection()
    try:
        logger.info("K2 startup catch-up: observations")
        logger.info("  %s", catch_up_obs(conn, days_back=30))
        logger.info("K2 startup catch-up: observation_instants")
        logger.info("  %s", catch_up_hourly(conn, days_back=30))
        logger.info("K2 startup catch-up: solar_daily")
        logger.info("  %s", catch_up_solar(conn, days_back=30))
        logger.info("K2 startup catch-up: forecasts")
        logger.info("  %s", catch_up_forecasts(conn, days_back=30))
    finally:
        conn.close()


@_scheduler_job("ecmwf_open_data")
def _ecmwf_open_data_cycle():
    from src.data.ecmwf_open_data import collect_open_ens_cycle

    result = collect_open_ens_cycle()
    logger.info("ECMWF Open Data: %s", result)


@_scheduler_job("etl_recalibrate")
def _etl_recalibrate():
    """Weekly recalibration: refresh ETL tables + refit Platt + replay audit.

    Cross-module sync mechanism. Keeps data tables fresh and downstream
    consumers (Platt calibration) aligned with current data.

    History:
    - Originally included per-city α validation (validate_dynamic_alpha.py).
      Removed after D1 analysis (2026-03-31) showed MAE→α mapping is
      fundamentally wrong (r=+0.032). α improvements are now per-decision
      (spread bonus D4, tail scaling D3) not per-city.
    - Platt refit is critical (D5: overconfidence Brier 0.31→0.02, -92%).
    """
    import subprocess

    venv_python = _etl_subprocess_python()
    scripts_dir = Path(__file__).parent.parent / "scripts"

    results = {}

    # 1. Refresh ETL tables (diurnal curves, persistence, observations)
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

    # 3. Calibration pairs are produced by the post-fillback canonical cascade.
    # Do not run legacy/direct TIGGE pair generators here; refit consumes only
    # already-certified canonical pairs.
    results["calibration_pairs"] = "SKIP: run rebuild_calibration_pairs_canonical post-fillback"

    # 4. Platt refit is explicit-only after the canonical post-fillback cascade.
    results["platt_refit"] = "SKIP: run explicit post-fillback canonical refit"

    # 5. Replay audit snapshot — track system performance trend
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


@_scheduler_job("automation_analysis")
def _automation_analysis_cycle():
    """Daily diagnostic: check calibration layer tables and bias correction readiness.

    Designed to run every 6 hours so Zeus operator always knows the state
    of the automation layer without manual DB queries.
    """
    import sys
    import subprocess
    venv_python = sys.executable
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


def _startup_wallet_check(clob=None):
    """P7: Fail-closed wallet gate. Live daemon refuses to start if wallet query fails.

    Accepts an optional clob for testing. In production, creates a live
    PolymarketClient.
    """
    if clob is None:
        from src.data.polymarket_client import PolymarketClient
        clob = PolymarketClient()
    try:
        balance = float(clob.get_balance())
        logger.info("Startup wallet check: $%.2f USDC available", balance)
    except Exception as exc:
        logger.critical("FAIL-CLOSED: wallet query failed at daemon start: %s", exc)
        sys.exit("FATAL: Cannot start \u2014 wallet unreachable. Fix credentials or network and restart.")


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


def main():
    if "ZEUS_MODE" not in os.environ:
        sys.exit("FATAL: ZEUS_MODE not set. Launch with ZEUS_MODE=live")
    if os.environ["ZEUS_MODE"] != "live":
        sys.exit(
            f"FATAL: ZEUS_MODE={os.environ['ZEUS_MODE']!r} is not valid. "
            "Must be exactly 'live'."
        )

    # G6 antibody (2026-04-26): refuse boot if any non-allowlisted strategy is
    # currently enabled. KNOWN_STRATEGIES is the engine's universe of buildable
    # strategies (4); LIVE_SAFE_STRATEGIES is the operator-approved subset for
    # live execution (1: opening_inertia). is_strategy_enabled() returns True
    # by default, so non-safe strategies must be explicitly disabled via
    # control_plane set_strategy_gate before launch.
    from src.control.control_plane import (
        assert_live_safe_strategies_under_live_mode,
        is_strategy_enabled,
    )
    from src.engine.cycle_runner import KNOWN_STRATEGIES
    enabled_strategies = {s for s in KNOWN_STRATEGIES if is_strategy_enabled(s)}
    assert_live_safe_strategies_under_live_mode(enabled_strategies)

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
    logger.info("Capital: $%.2f | Kelly: %.0f%%",
                settings.capital_base_usd,
                settings["sizing"]["kelly_multiplier"] * 100)

    conn = get_world_connection()
    init_schema(conn)

    # Ensure trade DB has all tables (prevents lazy-creation gaps)
    trade_conn = get_trade_connection()
    init_schema(trade_conn)
    trade_conn.close()

    # Startup health check: warn about deferred data actions
    _startup_data_health_check(conn)

    conn.close()

    # P7: Fail-closed wallet gate u2014 must run before first cycle.
    _startup_wallet_check()

    if once:
        run_single_cycle()
        return

    # APScheduler loop mode
    scheduler = BlockingScheduler()
    discovery = settings["discovery"]

    # All modes use the SAME CycleRunner with different DiscoveryMode values
    # max_instances=1: prevent concurrent execution if previous cycle still running
    scheduler.add_job(
        lambda: _run_mode(DiscoveryMode.OPENING_HUNT), "interval",
        minutes=discovery["opening_hunt_interval_min"], id="opening_hunt",
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
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(_harvester_cycle, "interval", hours=1, id="harvester")
    scheduler.add_job(_write_heartbeat, "interval", seconds=60, id="heartbeat",
                      max_instances=1, coalesce=True)
    for time_str in discovery["ecmwf_open_data_times_utc"]:
        h, m = time_str.split(":")
        scheduler.add_job(
            _ecmwf_open_data_cycle, "cron",
            hour=int(h), minute=int(m), id=f"ecmwf_open_data_{time_str}",
        )

    # K2 live-ingestion packet (task #59) — 4 appenders that replace the
    # legacy wu_daily_collector. Each writes to its own physical table AND
    # to the shared `data_coverage` ledger in the same transaction, so the
    # hole scanner always has a truthful view of what's been fetched.
    #
    # Job cadence rationale:
    #   daily_obs_tick          hourly — WuDailyScheduler fires per city at
    #                           local peak+4h, HKO refresh gated to UTC 02:00
    #   hourly_instants_tick    hourly — 3-day rolling window per city with
    #                           per-city dynamic end_date (most-recently
    #                           completed local day)
    #   solar_daily_tick        daily UTC 00:30 — deterministic astronomical
    #                           data, [today, today+14] per city
    #   forecasts_daily_tick    daily UTC 07:30 — after ECMWF 00Z + GFS 06Z
    #                           runs populate in Previous Runs API
    scheduler.add_job(
        _k2_daily_obs_tick, "cron",
        minute=0, id="k2_daily_obs",
        max_instances=1, coalesce=True, misfire_grace_time=1800,
    )
    scheduler.add_job(
        _k2_hourly_instants_tick, "cron",
        minute=7, id="k2_hourly_instants",   # :07 to stagger from daily_obs :00
        max_instances=1, coalesce=True, misfire_grace_time=1800,
    )
    scheduler.add_job(
        _k2_solar_daily_tick, "cron",
        hour=0, minute=30, id="k2_solar_daily",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        _k2_forecasts_daily_tick, "cron",
        hour=7, minute=30, id="k2_forecasts_daily",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )

    # Daily recalibration: ETL refresh + TIGGE direct cal + Platt refit
    scheduler.add_job(
        _etl_recalibrate, "cron",
        hour=6, minute=0,
        id="etl_recalibrate",
    )

    # Daily automation analysis: calibration layer diagnostics once a day
    scheduler.add_job(
        _automation_analysis_cycle, "cron",
        hour=9, minute=0, id="automation_analysis",
        max_instances=1, coalesce=True,
    )

    # K2 boot-time catch-up: register as the FIRST scheduled job with
    # next_run_time=now so the scheduler starts immediately and the
    # catch-up runs on the scheduler thread rather than blocking boot.
    # Rationale (critic S2#3): running catch-up inline before
    # scheduler.start() blocks the heartbeat writer, which lets the
    # OpenClaw process supervisor mark the daemon stale and kill it
    # mid-catch-up, creating a livelock: restart → re-run 30 min of
    # catch-up → get killed → restart. Registering as a normal job with
    # max_instances=1 avoids the stall.
    from datetime import datetime as _datetime_now
    scheduler.add_job(
        _k2_startup_catch_up, "date",
        run_date=_datetime_now.now(),  # fire once, immediately
        id="k2_startup_catch_up",
        max_instances=1, coalesce=True, misfire_grace_time=None,
    )

    # K2 hole scanner: scheduled pass once per day (UTC 04:00) so the
    # coverage ledger stays healthy even for cities onboarded after
    # daemon boot. Previously the scanner only ran via the post-fillback
    # shell script; K2 now has a permanent patrol schedule (critic S3).
    scheduler.add_job(
        _k2_hole_scanner_tick, "cron",
        hour=4, minute=0, id="k2_hole_scanner",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )

    jobs = [j.id for j in scheduler.get_jobs()]
    logger.info("Scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
