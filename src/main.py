"""Zeus main entry point. Blueprint v2 §9.1.

All discovery modes go through the same CycleRunner with different DiscoveryMode values.
The lifecycle is identical for all modes — only scanner parameters differ.
"""

import logging
import os
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from src.config import settings
from src.engine.cycle_runner import run_cycle
from src.engine.discovery_mode import DiscoveryMode
from src.state.db import init_schema, get_connection

logger = logging.getLogger("zeus")


def _run_mode(mode: DiscoveryMode):
    """Wrapper with error handling for scheduler."""
    try:
        summary = run_cycle(mode)
        logger.info("%s: %s", mode.value, summary)
    except Exception as e:
        logger.error("%s failed: %s", mode.value, e, exc_info=True)


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


def run_single_cycle():
    """Run one complete cycle of all modes. For testing, not production."""
    logger.info("=== SINGLE CYCLE TEST ===")
    for mode in DiscoveryMode:
        logger.info("[%s]...", mode.value)
        _run_mode(mode)
    _harvester_cycle()
    logger.info("=== SINGLE CYCLE COMPLETE ===")


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

    conn = get_connection()
    init_schema(conn)
    conn.close()

    if once:
        run_single_cycle()
        return

    # APScheduler loop mode
    scheduler = BlockingScheduler()
    discovery = settings["discovery"]

    # All modes use the SAME CycleRunner with different DiscoveryMode values
    scheduler.add_job(
        lambda: _run_mode(DiscoveryMode.OPENING_HUNT), "interval",
        minutes=discovery["opening_hunt_interval_min"], id="opening_hunt",
    )
    for time_str in discovery["update_reaction_times_utc"]:
        h, m = time_str.split(":")
        scheduler.add_job(
            lambda: _run_mode(DiscoveryMode.UPDATE_REACTION), "cron",
            hour=int(h), minute=int(m), id=f"update_reaction_{time_str}",
        )
    scheduler.add_job(
        lambda: _run_mode(DiscoveryMode.DAY0_CAPTURE), "interval",
        minutes=discovery["day0_interval_min"], id="day0_capture",
    )
    scheduler.add_job(_harvester_cycle, "interval", hours=1, id="harvester")
    for time_str in discovery["ecmwf_open_data_times_utc"]:
        h, m = time_str.split(":")
        scheduler.add_job(
            _ecmwf_open_data_cycle, "cron",
            hour=int(h), minute=int(m), id=f"ecmwf_open_data_{time_str}",
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
