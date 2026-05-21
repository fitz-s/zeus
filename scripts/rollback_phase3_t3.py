# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §5 EH-3 + §2 T3 G6
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: EH-3 escape hatch — drops shoulder_exposure_ledger and reverts user_version to 22 (pre-T3)
# Reuse: SCAFFOLD — run() raises NotImplementedError; remove guard and review _ROLLBACK_SQL before executing

"""Phase 3 T3 rollback — drop shoulder_exposure_ledger, restore user_version to 22.

Escape hatch EH-3 (plan §5): per-track revert. Drops the additive T3 table
(shoulder_exposure_ledger) and reverts PRAGMA user_version from 23 to 22.

The no_trade_events table is NOT rolled back — its schema_version CHECK (14..23)
expansion is additive and backward-compatible; removing v23 from the CHECK would
break existing rows written under v23.

Usage
-----
    python scripts/rollback_phase3_t3.py [--dry-run] [--db PATH]

--dry-run: print the SQL without touching the DB.
--db PATH: override world DB path (default: from src.config STATE_DIR).

SCAFFOLD — run() raises NotImplementedError. Remove the guard and review _ROLLBACK_SQL
before executing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

_ROLLBACK_SQL = """
-- Phase 3 T3 rollback: drop shoulder_exposure_ledger, restore user_version=22.
-- Run inside a single transaction.
DROP INDEX IF EXISTS idx_shoulder_exposure_ledger_cluster_side;
DROP INDEX IF EXISTS idx_shoulder_exposure_ledger_city;
DROP TABLE IF EXISTS shoulder_exposure_ledger;
PRAGMA user_version = 22;
"""


def run(db_path: Path, dry_run: bool = False) -> None:
    """Execute the T3 rollback under a single transaction.

    SCAFFOLD — raises NotImplementedError. T3 production pass owns execution.
    """
    raise NotImplementedError(
        "T3 production pass owns run() execution. "
        "Review the SQL block in _ROLLBACK_SQL before removing this guard."
    )
    # import sqlite3
    # conn = sqlite3.connect(str(db_path))
    # try:
    #     conn.executescript(_ROLLBACK_SQL)
    #     conn.commit()
    # finally:
    #     conn.close()


def main() -> None:
    """CLI entry point.

    SCAFFOLD — raises NotImplementedError until T3 production pass.
    """
    raise NotImplementedError("T3 production pass owns main() execution.")
    # parser = argparse.ArgumentParser(description=__doc__)
    # parser.add_argument("--dry-run", action="store_true")
    # parser.add_argument("--db", type=Path, default=None)
    # args = parser.parse_args()
    # if args.db is None:
    #     from src.config import STATE_DIR
    #     args.db = STATE_DIR / "zeus-world.db"
    # run(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    raise NotImplementedError("T3 production pass owns execution")
