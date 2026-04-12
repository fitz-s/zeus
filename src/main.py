"""Zeus main entry point. Blueprint v2 §9.1.

All discovery modes go through the same CycleRunner with different DiscoveryMode values.
The lifecycle is identical for all modes — only scanner parameters differ.
"""

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
from src.state.db import init_schema, get_world_connection, get_trade_connection

logger = logging.getLogger("zeus")

# Cross-mode lock: prevents two discovery modes from reading/writing portfolio concurrently
_cycle_lock = threading.Lock()


def _etl_subprocess_python() -> str:
    candidate = Path(__file__).parent.parent / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def _run_mode(mode: DiscoveryMode):
    """Wrapper with error handling and cycle lock for scheduler."""
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


def _harvester_cycle():
    try:
        from src.execution.harvester import run_harvester
        result = run_harvester()
        logger.info("Harvester: %s", result)
    except Exception as e:
        logger.error("Harvester failed: %s", e, exc_info=True)


def _wu_daily_collection():
    """Daily WU observation collection (all cities). Must run daily — data is ephemeral (~36h)."""
    try:
        from src.data.wu_daily_collector import collect_daily_highs
        result = collect_daily_highs()
        logger.info("WU daily collection: collected=%d, skipped=%d, errors=%d",
                    result["collected"], result["skipped"], result["errors"])
    except Exception as e:
        logger.error("WU daily collection failed: %s", e, exc_info=True)


def _wu_daily_dispatch():
    """Hourly dispatch: check every city's WuDailyScheduler and trigger collection.

    K2: replaces fixed UTC 12:00 cron. Each city fires at local
    peak_hour+4h (DST-aware). On any given hour some subset of cities
    will be in-window; only those are collected.
    """
    try:
        from src.data.wu_scheduler import WuDailyScheduler, dispatch_wu_daily_collection
        from datetime import date as _date
        from src.config import cities_by_name as _cities_by_name
        from src.data.wu_daily_collector import collect_daily_highs
        scheduler = WuDailyScheduler()
        city_names = dispatch_wu_daily_collection(scheduler)
        if not city_names:
            return
        cities = [_cities_by_name[name] for name in city_names if name in _cities_by_name]
        if not cities:
            logger.warning("WU daily dispatch: no known cities in dispatch list %s", city_names)
            return
        result = collect_daily_highs(target_date=_date.today(), cities=cities)
        logger.info(
            "WU daily dispatch: cities=%s collected=%d, skipped=%d, errors=%d",
            [c.name for c in cities],
            result.get("collected", 0),
            result.get("skipped", 0),
            result.get("errors", 0),
        )
    except Exception as e:
        logger.error("WU daily dispatch failed: %s", e, exc_info=True)


def _ecmwf_open_data_cycle():
    try:
        from src.data.ecmwf_open_data import collect_open_ens_cycle

        result = collect_open_ens_cycle()
        logger.info("ECMWF Open Data: %s", result)
    except Exception as e:
        logger.error("ECMWF Open Data collection failed: %s", e, exc_info=True)


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

    # 1. Refresh Rainstorm-derived source tables before downstream ETL consumes them.
    migration_script = scripts_dir / "migrate_rainstorm_full.py"
    if migration_script.exists():
        try:
            r = subprocess.run(
                [venv_python, str(migration_script)],
                capture_output=True, text=True, timeout=300,
            )
            results["migrate_rainstorm_full"] = "OK" if r.returncode == 0 else f"FAIL: {r.stderr[-200:]}"
        except Exception as e:
            results["migrate_rainstorm_full"] = f"ERROR: {e}"

    # 2. Refresh ETL tables (diurnal curves, persistence, observations)
    for script in [
        "etl_observation_instants.py",
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

    # 3. TIGGE direct calibration — pair ENS snapshots with settlement_value
    try:
        r = subprocess.run(
            [venv_python, str(scripts_dir / "etl_tigge_direct_calibration.py")],
            capture_output=True, text=True, timeout=300,
        )
        results["tigge_direct_cal"] = "OK" if r.returncode == 0 else f"FAIL: {r.stderr[-200:]}"
    except Exception as e:
        results["tigge_direct_cal"] = f"ERROR: {e}"

    # 4. Platt refit — critical for calibration accuracy (D5)
    try:
        r = subprocess.run(
            [venv_python, str(scripts_dir / "refit_platt.py")],
            capture_output=True, text=True, timeout=300,
        )
        results["platt_refit"] = "OK" if r.returncode == 0 else f"FAIL: {r.stderr[-200:]}"
    except Exception as e:
        results["platt_refit"] = f"ERROR: {e}"

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


def _automation_analysis_cycle():
    """Daily diagnostic: check calibration layer tables and bias correction readiness.

    Designed to run every 6 hours so Zeus operator always knows the state
    of the automation layer without manual DB queries.
    """
    try:
        import subprocess
        venv_python = str(Path(__file__).parent.parent / ".venv" / "bin" / "python")
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
    except Exception as e:
        logger.error("automation_analysis failed: %s", e, exc_info=True)


def run_single_cycle():
    """Run one complete cycle of all modes. For testing, not production."""
    logger.info("=== SINGLE CYCLE TEST ===")
    for mode in DiscoveryMode:
        logger.info("[%s]...", mode.value)
        _run_mode(mode)
    _harvester_cycle()
    logger.info("=== SINGLE CYCLE COMPLETE ===")


def _write_heartbeat() -> None:
    """Write a heartbeat JSON to state/ every 60s so operators can detect silent crashes."""
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
    except Exception as exc:
        logger.warning("Heartbeat write failed: %s", exc)


def _startup_wallet_check(clob=None):
    """P7: Fail-closed wallet gate. Live daemon refuses to start if wallet query fails.

    Accepts an optional clob for testing. In production, creates a live
    PolymarketClient. Paper mode skips the check (no on-chain wallet).
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
        bias_enabled = settings._data.get("bias_correction_enabled", False)
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
    if os.environ["ZEUS_MODE"] == "paper":
        logger.critical(
            "ZEUS_MODE='paper' is no longer supported. Zeus is designed only for live. "
            "Paper mode was decommissioned in Phase 1. Set ZEUS_MODE=live."
        )
        sys.exit("FATAL: ZEUS_MODE='paper' rejected — Zeus is live-only.")
    if os.environ["ZEUS_MODE"] != "live":
        sys.exit(
            f"FATAL: ZEUS_MODE={os.environ['ZEUS_MODE']!r} is not valid. "
            "Must be exactly 'live'."
        )
    mode = get_mode()
    once = "--once" in sys.argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Zeus starting in %s mode%s", mode, " (single cycle)" if once else "")
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

    # K2: per-city physical-clock WU daily collection (hourly dispatch)
    # Each city fires at local peak_hour+4h (DST-aware via WuDailyScheduler).
    # Hourly tick checks which cities are in their window; only those are collected.
    scheduler.add_job(
        _wu_daily_dispatch, "cron",
        minute=0, id="wu_daily",
        max_instances=1, coalesce=True, misfire_grace_time=1800,
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

    jobs = [j.id for j in scheduler.get_jobs()]
    logger.info("Scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
