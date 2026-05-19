#!/usr/bin/env python3
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.2, migration_dry_runs.json
"""Migrate calibration_pairs_v2 to add NOT NULL on decision_group_id.

SCAFFOLD — outline only. No production logic implemented.
Implementation deferred to PR 4 production phase.

CRITICAL SAFETY RULES:
    - DEFAULT: dry_run=True. Never executes DDL or DML without --apply.
    - DISK GUARD: refuses to run if free disk < --require-free-disk-gib.
      Peak disk usage: ~49 GiB (forecasts.db rebuild) + safety margin.
      Operator must free disk before running with --apply.
    - K1 DB SPLIT: forecasts.db and world.db are SEPARATE migrations.
      Each runs independently with its own SAVEPOINT.
    - INV-37: Cross-DB writes use ATTACH+SAVEPOINT, never independent conns.

Migration algorithm (SQLite rebuild pattern):
    SQLite cannot ALTER COLUMN to add NOT NULL constraint directly.
    Required steps per table:
        1. BEGIN SAVEPOINT migrate_not_null_{table};
        2. CREATE TABLE {table}_new AS (same DDL with decision_group_id TEXT NOT NULL)
        3. INSERT INTO {table}_new SELECT * FROM {table}
           (will fail if any NULL exists — preflight audit must be clean)
        4. DROP TABLE {table}
        5. ALTER TABLE {table}_new RENAME TO {table}
        6. RELEASE SAVEPOINT migrate_not_null_{table};

    On any error: ROLLBACK SAVEPOINT.

Tables:
    zeus-forecasts.db :: calibration_pairs_v2
    zeus-world.db :: calibration_pairs_v2_archived_2026_05_11

Usage:
    # Dry run (default, safe):
    python scripts/migrate_calibration_pairs_v2_not_null.py

    # Dry run explicit:
    python scripts/migrate_calibration_pairs_v2_not_null.py --dry-run

    # Live run (DESTRUCTIVE — requires disk headroom):
    python scripts/migrate_calibration_pairs_v2_not_null.py \\
        --apply \\
        --require-free-disk-gib 55 \\
        --table forecasts

Flags:
    --dry-run               Default. Print plan, touch nothing.
    --apply                 Execute DDL. Requires --require-free-disk-gib.
    --require-free-disk-gib N  Refuse if free disk < N GiB (default: 55).
    --table {forecasts,world,both}  Which DB to migrate (default: both).
    --db-dir DIR            Override state/ directory for DB paths.

Exit codes:
    0  Migration succeeded (or dry-run completed cleanly)
    1  Pre-flight failure (NULLs found, disk insufficient, etc.)
    2  Migration failed mid-flight (SAVEPOINT rolled back)
    3  Unexpected error

SCAFFOLD outline:
    1. parse_args() — validate --apply implies --require-free-disk-gib set
    2. preflight_check() — run audit (NULL count + disk free)
    3. build_migration_plan() — emit DDL steps as plan text
    4. If --dry-run: print plan, exit 0
    5. If --apply:
        a. Check disk >= require_free_disk_gib
        b. For each target table:
            i.  Open connection, BEGIN SAVEPOINT
            ii. Execute rebuild steps
            iii. Verify new table row count == old row count
            iv. RELEASE SAVEPOINT or ROLLBACK on error
        c. Emit migration report
    6. Exit with appropriate code
"""

import argparse
import sys


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("migrate_calibration_pairs_v2_not_null: SCAFFOLD only")


def preflight_check(args: argparse.Namespace) -> dict:
    """Run audit and disk check. Return preflight result dict.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("preflight_check: SCAFFOLD only")


def build_migration_plan(target_tables: list[str]) -> list[str]:
    """Return ordered list of DDL/DML steps as human-readable strings.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("build_migration_plan: SCAFFOLD only")


def execute_migration(args: argparse.Namespace, plan: list[str]) -> int:
    """Execute migration under SAVEPOINT. Return exit code.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("execute_migration: SCAFFOLD only")


def main() -> int:
    """Entry point.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("migrate_calibration_pairs_v2_not_null.main: SCAFFOLD only")


if __name__ == "__main__":
    sys.exit(main())
