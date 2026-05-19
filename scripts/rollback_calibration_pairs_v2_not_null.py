#!/usr/bin/env python3
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.2, migration_dry_runs.json
"""Rollback NOT NULL constraint on calibration_pairs_v2.decision_group_id.

SCAFFOLD — outline only. No production logic implemented.
Implementation deferred to PR 4 production phase.

Purpose:
    Restore calibration_pairs_v2 (and the archived table on world.db) to the
    pre-migration schema (decision_group_id TEXT, nullable) in the event that
    the NOT NULL migration must be reversed.

    Rollback is only needed if the migration's own SAVEPOINT did not roll back
    cleanly (e.g., OS crash mid-rebuild). The SAVEPOINT approach in
    migrate_calibration_pairs_v2_not_null.py should handle most failure cases
    automatically.

CRITICAL SAFETY RULES:
    - DEFAULT: dry_run=True. Never executes DDL without --apply.
    - Rollback uses the same SAVEPOINT rebuild pattern as the forward migration.
    - A .pre_migration_backup table must exist (created by migrate script).
      If backup table is missing, rollback refuses with exit code 1.

Usage:
    # Dry run (default):
    python scripts/rollback_calibration_pairs_v2_not_null.py

    # Live rollback:
    python scripts/rollback_calibration_pairs_v2_not_null.py \\
        --apply \\
        --require-free-disk-gib 55 \\
        --table forecasts

Flags:
    --dry-run               Default. Print rollback plan, touch nothing.
    --apply                 Execute DDL rollback.
    --require-free-disk-gib N  Disk guard (same as migrate script, default: 55).
    --table {forecasts,world,both}
    --db-dir DIR

Exit codes:
    0  Rollback succeeded (or dry-run completed)
    1  Pre-flight failure (backup missing, disk insufficient)
    2  Rollback failed (see error output)
    3  Unexpected error

SCAFFOLD outline:
    1. parse_args()
    2. Check backup table exists: {table}_pre_not_null_backup
    3. build_rollback_plan() — DDL steps to restore nullable schema
    4. If --dry-run: print plan, exit 0
    5. If --apply:
        a. Disk guard check
        b. For each target table:
            i.  BEGIN SAVEPOINT rollback_not_null_{table}
            ii. CREATE TABLE {table}_new with nullable decision_group_id
            iii. INSERT INTO {table}_new SELECT * FROM {table}
            iv. DROP TABLE {table}
            v.  ALTER TABLE {table}_new RENAME TO {table}
            vi. RELEASE SAVEPOINT or ROLLBACK
    6. Exit code
"""

import argparse
import sys


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("rollback_calibration_pairs_v2_not_null: SCAFFOLD only")


def check_backup_table_exists(db_path: str, backup_table: str) -> bool:
    """Return True if backup table exists in db_path.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("check_backup_table_exists: SCAFFOLD only")


def build_rollback_plan(target_tables: list[str]) -> list[str]:
    """Return ordered list of DDL/DML steps as human-readable strings.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("build_rollback_plan: SCAFFOLD only")


def execute_rollback(args: argparse.Namespace, plan: list[str]) -> int:
    """Execute rollback under SAVEPOINT. Return exit code.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("execute_rollback: SCAFFOLD only")


def main() -> int:
    """Entry point.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("rollback_calibration_pairs_v2_not_null.main: SCAFFOLD only")


if __name__ == "__main__":
    sys.exit(main())
