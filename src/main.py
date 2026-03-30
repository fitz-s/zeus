"""Zeus main entry point. Spec §9.1.

ZEUS_MODE=paper|live controls execution mode.
Uses APScheduler for job management:
- Opening Hunt: every 30 min (Mode A)
- Update Reaction: 4× daily aligned with ENS updates (Mode B)
- Day0 Capture: every 15 min for markets within 6h (Mode C)
- Harvester: hourly
- Monitor: every 5 min
"""

import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from src.config import settings
from src.execution.day0_capture import run_day0_capture
from src.execution.harvester import run_harvester
from src.execution.monitor import run_monitor
from src.execution.opening_hunt import run_opening_hunt
from src.execution.update_reaction import run_update_reaction
from src.state.db import init_schema, get_connection

logger = logging.getLogger("zeus")


def _opening_hunt_cycle():
    """Wrapper with error handling for scheduler."""
    try:
        n = run_opening_hunt()
        logger.info("Opening Hunt: %d trades", n)
    except Exception as e:
        logger.error("Opening Hunt failed: %s", e, exc_info=True)


def _update_reaction_cycle():
    try:
        result = run_update_reaction()
        logger.info("Update Reaction: %s", result)
    except Exception as e:
        logger.error("Update Reaction failed: %s", e, exc_info=True)


def _day0_capture_cycle():
    try:
        n = run_day0_capture()
        logger.info("Day0 Capture: %d trades", n)
    except Exception as e:
        logger.error("Day0 Capture failed: %s", e, exc_info=True)


def _harvester_cycle():
    """Detect settlements, generate calibration pairs. Spec §8.1."""
    try:
        result = run_harvester()
        logger.info("Harvester: %s", result)
    except Exception as e:
        logger.error("Harvester failed: %s", e, exc_info=True)


def _monitor_cycle():
    try:
        n = run_monitor()
        if n > 0:
            logger.info("Monitor: %d exits", n)
    except Exception as e:
        logger.error("Monitor failed: %s", e, exc_info=True)


def main():
    mode = os.environ.get("ZEUS_MODE", settings.mode)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Zeus starting in %s mode", mode)
    logger.info("Capital: $%.2f | Kelly: %.0f%%",
                settings.capital_base_usd,
                settings["sizing"]["kelly_multiplier"] * 100)

    # Initialize database
    conn = get_connection()
    init_schema(conn)
    conn.close()

    # Set up scheduler
    scheduler = BlockingScheduler()
    discovery = settings["discovery"]

    # Mode A: Opening Hunt — every 30 min
    scheduler.add_job(
        _opening_hunt_cycle, "interval",
        minutes=discovery["opening_hunt_interval_min"],
        id="opening_hunt",
    )

    # Mode B: Update Reaction — 4× daily at fixed UTC times
    for time_str in discovery["update_reaction_times_utc"]:
        hour, minute = time_str.split(":")
        scheduler.add_job(
            _update_reaction_cycle, "cron",
            hour=int(hour), minute=int(minute),
            id=f"update_reaction_{time_str}",
        )

    # Mode C: Day0 Capture — every 15 min
    scheduler.add_job(
        _day0_capture_cycle, "interval",
        minutes=discovery["day0_interval_min"],
        id="day0_capture",
    )

    # Harvester: hourly
    scheduler.add_job(_harvester_cycle, "interval", hours=1, id="harvester")

    # Monitor: every 5 min
    scheduler.add_job(_monitor_cycle, "interval", minutes=5, id="monitor")

    jobs = [j.id for j in scheduler.get_jobs()]
    logger.info("Scheduler ready. %d jobs: %s", len(jobs), jobs)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
