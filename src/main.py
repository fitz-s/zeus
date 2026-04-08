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

from src.config import settings
from src.engine.cycle_runner import run_cycle
from src.engine.discovery_mode import DiscoveryMode
from src.state.db import init_schema, get_shared_connection

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

    # 1. Refresh ETL tables (diurnal curves, persistence, observations)
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

    # 2. Platt refit — critical for calibration accuracy (D5)
    try:
        r = subprocess.run(
            [venv_python, str(scripts_dir / "refit_platt.py")],
            capture_output=True, text=True, timeout=300,
        )
        results["platt_refit"] = "OK" if r.returncode == 0 else f"FAIL: {r.stderr[-200:]}"
    except Exception as e:
        results["platt_refit"] = f"ERROR: {e}"

    # 3. Replay audit snapshot — track system performance trend
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
    mode = os.environ.get("ZEUS_MODE", settings.mode)
    once = "--once" in sys.argv
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Zeus starting in %s mode%s", mode, " (single cycle)" if once else "")
    logger.info("Capital: $%.2f | Kelly: %.0f%%",
                settings.capital_base_usd,
                settings["sizing"]["kelly_multiplier"] * 100)

    conn = get_shared_connection()
    init_schema(conn)

    # Startup health check: warn about deferred data actions
    _startup_data_health_check(conn)

    conn.close()

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
    for time_str in discovery["ecmwf_open_data_times_utc"]:
        h, m = time_str.split(":")
        scheduler.add_job(
            _ecmwf_open_data_cycle, "cron",
            hour=int(h), minute=int(m), id=f"ecmwf_open_data_{time_str}",
        )

    # Weekly recalibration: ETL refresh + alpha validation + Platt refit
    scheduler.add_job(
        _etl_recalibrate, "cron",
        day_of_week="mon", hour=6, minute=0,
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
