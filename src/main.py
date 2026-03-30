"""Zeus main entry point. Spec §9.1.

ZEUS_MODE=paper|live controls execution mode.
Uses APScheduler for job management:
- Opening Hunt: every 30 min (Mode A)
- Update Reaction: 4× daily aligned with ENS updates (Mode B)
- Day0 Capture: every 15 min for markets within 6h (Mode C)
- Harvester: hourly
- Monitor: variable frequency
"""

import logging
import os
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from src.config import settings
from src.riskguard.risk_level import RiskLevel
from src.riskguard.riskguard import get_current_level
from src.state.db import init_schema, get_connection

logger = logging.getLogger("zeus")


def opening_hunt_cycle():
    """Mode A: scan for newly opened markets. Spec §6.2."""
    level = get_current_level()
    if level in (RiskLevel.YELLOW, RiskLevel.ORANGE, RiskLevel.RED):
        logger.info("Opening Hunt skipped: RiskGuard=%s", level.value)
        return

    logger.info("Opening Hunt cycle starting...")
    # TODO(Phase C2): Implement full pipeline:
    # 1. Scan for markets opened < 24h ago
    # 2. For each: fetch ENS → P_raw → calibrate → find edges → FDR → Kelly → execute
    logger.info("Opening Hunt cycle complete (stub)")


def update_reaction_cycle():
    """Mode B: post-ENS update scan + exit check. Spec §6.2."""
    level = get_current_level()
    if level in (RiskLevel.ORANGE, RiskLevel.RED):
        logger.info("Update Reaction skipped: RiskGuard=%s", level.value)
        return

    logger.info("Update Reaction cycle starting...")
    # TODO(Phase C2): Implement full pipeline:
    # 1. Check exit triggers on held positions
    # 2. Scan non-held markets for new edges
    logger.info("Update Reaction cycle complete (stub)")


def day0_capture_cycle():
    """Mode C: observation-based settlement capture. Spec §6.2."""
    level = get_current_level()
    if level == RiskLevel.RED:
        logger.info("Day0 Capture skipped: RiskGuard=RED")
        return

    logger.info("Day0 Capture cycle starting...")
    # TODO(Phase C2): Implement with ASOS observation integration
    logger.info("Day0 Capture cycle complete (stub)")


def harvester_cycle():
    """Detect settlements, generate calibration pairs. Spec §8.1."""
    logger.info("Harvester cycle starting...")
    # TODO(Phase C3): Check Gamma API for settled markets
    logger.info("Harvester cycle complete (stub)")


def monitor_cycle():
    """Check exit triggers on held positions. Spec §6.3."""
    logger.info("Monitor cycle starting...")
    # TODO(Phase C2): Iterate portfolio, evaluate exit triggers
    logger.info("Monitor cycle complete (stub)")


def main():
    mode = os.environ.get("ZEUS_MODE", settings.mode)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("Zeus starting in %s mode", mode)

    # Initialize database
    conn = get_connection()
    init_schema(conn)
    conn.close()

    # Set up scheduler
    scheduler = BlockingScheduler()

    discovery = settings["discovery"]

    # Mode A: Opening Hunt
    scheduler.add_job(
        opening_hunt_cycle,
        "interval",
        minutes=discovery["opening_hunt_interval_min"],
        id="opening_hunt",
    )

    # Mode B: Update Reaction at fixed UTC times
    for time_str in discovery["update_reaction_times_utc"]:
        hour, minute = time_str.split(":")
        scheduler.add_job(
            update_reaction_cycle,
            "cron",
            hour=int(hour),
            minute=int(minute),
            id=f"update_reaction_{time_str}",
        )

    # Mode C: Day0 Capture
    scheduler.add_job(
        day0_capture_cycle,
        "interval",
        minutes=discovery["day0_interval_min"],
        id="day0_capture",
    )

    # Harvester: hourly
    scheduler.add_job(
        harvester_cycle,
        "interval",
        hours=1,
        id="harvester",
    )

    # Monitor: every 5 minutes
    scheduler.add_job(
        monitor_cycle,
        "interval",
        minutes=5,
        id="monitor",
    )

    logger.info("Scheduler configured. Jobs: %s",
                [j.id for j in scheduler.get_jobs()])

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Zeus shutting down")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
