# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase3_shoulder/PHASE_3_SHOULDER_PLAN.md §5 EH-3 + §2 T2 G6
# Lifecycle: created=2026-05-21; last_reviewed=2026-05-21; last_reused=never
# Purpose: EH-3 escape hatch — drops tail_stress_scenarios and reverts user_version to 17 (pre-T2)
# Reuse: SCAFFOLD — run() raises NotImplementedError; T2 production pass owns execution; review SQL before running

"""Phase 3 T2 rollback — drop tail_stress_scenarios, restore user_version to 17.

Escape hatch EH-3 (plan §5): per-track revert. Drops the additive T2 table
(tail_stress_scenarios) and reverts PRAGMA user_version from 18 to 17.
The no_trade_events table is NOT rolled back — its SHOULDER_* CHECK expansion
is additive and backward-compatible; removing members would break existing rows.

Usage
-----
    python scripts/rollback_phase3_t2.py [--dry-run] [--db PATH]

--dry-run: print the SQL without touching the DB.
--db PATH: override world DB path (default: from src.config STATE_DIR).

SCAFFOLD — main() raises NotImplementedError. T2 production pass owns execution.
"""

from __future__ import annotations

import argparse
from pathlib import Path

_ROLLBACK_SQL = """
-- Phase 3 T2 rollback: drop tail_stress_scenarios, restore user_version=17.
-- Run inside a single transaction.
DROP TABLE IF EXISTS tail_stress_scenarios;
DROP INDEX IF EXISTS idx_tail_stress_scenarios_market_date;
PRAGMA user_version = 17;
"""


def run(db_path: Path, dry_run: bool = False) -> None:
    """Execute the T2 rollback under a single transaction.

    SCAFFOLD — raises NotImplementedError. T2 production pass owns execution.
    """
    raise NotImplementedError(
        "T2 production pass owns run() execution. "
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

    SCAFFOLD — raises NotImplementedError until T2 production pass.
    """
    raise NotImplementedError("T2 production pass owns main() execution.")
    # parser = argparse.ArgumentParser(description=__doc__)
    # parser.add_argument("--dry-run", action="store_true")
    # parser.add_argument("--db", type=Path, default=None)
    # args = parser.parse_args()
    # if args.db is None:
    #     from src.config import STATE_DIR
    #     args.db = STATE_DIR / "zeus-world.db"
    # run(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    raise NotImplementedError("T2 production pass owns execution")
