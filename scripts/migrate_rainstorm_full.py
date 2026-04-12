#!/usr/bin/env python3
"""Rainstorm → zeus-world.db migration (COMPLETE).

All rainstorm.db data has been fully migrated to zeus-world.db.
This script is retained as a no-op because src/main.py calls it
from _etl_recalibrate().
"""
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


def run_migration(dry_run: bool = False) -> dict:
    """No-op: rainstorm.db migration is complete — all data in zeus-world.db."""
    logger.info("Rainstorm migration complete — all data in zeus-world.db. Skipping.")
    print("Rainstorm migration complete — all data in zeus-world.db. Skipping.")
    return {"status": "noop", "reason": "migration_complete"}


if __name__ == "__main__":
    run_migration(dry_run="--dry-run" in sys.argv)
