#!/usr/bin/env python3
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.2, migration_dry_runs.json
"""Migrate calibration_pairs_v2 to add NOT NULL on decision_group_id.

SCAFFOLD — outline only. No production logic implemented.
Implementation deferred to PR 4 production phase.

CRITICAL SAFETY RULES:
    - DEFAULT: dry_run=True. Never executes DDL or DML without --apply.
    - DISK GUARD (--mode=rebuild only): refuses if free disk < --require-free-disk-gib.
      Peak disk usage: ~49 GiB (forecasts.db rebuild) + safety margin.
      NOT required for --mode=trigger (zero disk overhead).
    - K1 DB SPLIT: forecasts.db and world.db are SEPARATE, SEQUENTIAL migrations.
      Each runs independently with its own per-DB SAVEPOINT.
      These are NOT cross-DB operations — INV-37 ATTACH+SAVEPOINT applies only
      to RUNTIME calibration pipeline reads, not to schema migration.

TWO MIGRATION MODES:
    --mode=trigger  (DEFAULT — disk-safe, RECOMMENDED at 22 GiB free):
        Adds two SQLite triggers per table. Zero disk overhead. Validated
        in tests/test_calibration_pairs_v2_migration.py (live test).

        Trigger DDL template (per table, INSERT + UPDATE):
            CREATE TRIGGER {table}_dgid_not_null_ins
            BEFORE INSERT ON {table}
            WHEN NEW.decision_group_id IS NULL
            BEGIN SELECT RAISE(ABORT, 'NOT NULL: {table}.decision_group_id'); END;

            CREATE TRIGGER {table}_dgid_not_null_upd
            BEFORE UPDATE OF decision_group_id ON {table}
            WHEN NEW.decision_group_id IS NULL
            BEGIN SELECT RAISE(ABORT, 'NOT NULL: {table}.decision_group_id'); END;

        Trade-offs:
          PRO: Zero disk overhead. Immediate. Reversible via DROP TRIGGER.
               Enforced at DB layer, not application layer.
          CON: PRAGMA table_info will still show notnull=0 (column DDL unchanged).
               Slightly higher per-row CPU on INSERT/UPDATE (μs-range).
               If someone uses sqlite3 shell to bypass triggers (PRAGMA ignore_check_constraints
               does NOT bypass triggers, but sqlite3 build with OMIT_TRIGGER does).

    --mode=rebuild  (canonical NOT NULL column — requires ~50 GiB free):
        SQLite cannot ALTER COLUMN to add NOT NULL directly.
        Required steps per table under SAVEPOINT:
            1. SAVEPOINT migrate_not_null_{table}
            2. CREATE TABLE {table}_new (same DDL, decision_group_id TEXT NOT NULL)
            3. INSERT INTO {table}_new SELECT * FROM {table}
               (IntegrityError if any NULL exists — preflight must be clean)
            4. DROP TABLE {table}
            5. ALTER TABLE {table}_new RENAME TO {table}
            6. RELEASE SAVEPOINT

        Trade-offs:
          PRO: PRAGMA table_info shows notnull=1. "Pure" NOT NULL constraint.
               No trigger overhead on future inserts/updates.
          CON: ~49 GiB disk headroom required for forecasts.db rebuild.
               Long operation on 91M rows. BLOCKED at current 22 GiB free.

CURRENT RECOMMENDATION:
    Run --mode=trigger first (zero disk overhead, enforces immediately).
    Optionally run --mode=rebuild later when operator frees ~30+ GiB disk.

Tables:
    zeus-forecasts.db :: calibration_pairs_v2 (91M rows)
    zeus-world.db :: calibration_pairs_v2_archived_2026_05_11 (53M rows)

Usage:
    # Dry run (default, safe):
    python scripts/migrate_calibration_pairs_v2_not_null.py

    # Trigger mode (RECOMMENDED — disk-safe):
    python scripts/migrate_calibration_pairs_v2_not_null.py \\
        --apply \\
        --mode trigger \\
        --table both

    # Rebuild mode (canonical NOT NULL — requires disk headroom):
    python scripts/migrate_calibration_pairs_v2_not_null.py \\
        --apply \\
        --mode rebuild \\
        --require-free-disk-gib 55 \\
        --table forecasts

Flags:
    --dry-run               Default. Print plan, touch nothing.
    --apply                 Execute DDL.
    --mode {trigger,rebuild}  Migration mode (default: trigger).
    --require-free-disk-gib N  Disk guard for --mode=rebuild (default: 55).
                               Ignored for --mode=trigger.
    --table {forecasts,world,both}  Which DB to migrate (default: both).
    --db-dir DIR            Override state/ directory for DB paths.

Exit codes:
    0  Migration succeeded (or dry-run completed cleanly)
    1  Pre-flight failure (NULLs found, disk insufficient, etc.)
    2  Migration failed mid-flight (rolled back)
    3  Unexpected error

SCAFFOLD outline:
    1. parse_args() — validate --apply + --mode=rebuild implies disk guard check
    2. preflight_check() — run audit (NULL count + disk free if rebuild mode)
    3. build_migration_plan(mode) — emit DDL steps as plan text for chosen mode
    4. If --dry-run: print plan, exit 0
    5. If --apply:
        a. If --mode=rebuild: check disk >= require_free_disk_gib
        b. For each target table (forecasts then world, sequentially):
            i.  Open per-DB connection
            ii. BEGIN SAVEPOINT migrate_not_null_{table}_{mode}
            iii. Execute mode-specific DDL (triggers or rebuild)
            iv. Verify constraint active (trigger exists or notnull=1)
            v.  RELEASE SAVEPOINT or ROLLBACK on error
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

    For --mode=rebuild: check free disk >= require_free_disk_gib.
    For --mode=trigger: skip disk check (zero overhead).

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("preflight_check: SCAFFOLD only")


def build_migration_plan(mode: str, target_tables: list[str]) -> list[str]:
    """Return ordered list of DDL/DML steps as human-readable strings.

    Args:
        mode: "trigger" or "rebuild"
        target_tables: list of table names to migrate

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("build_migration_plan: SCAFFOLD only")


def execute_migration(args: argparse.Namespace, plan: list[str]) -> int:
    """Execute migration under per-DB SAVEPOINT. Return exit code.

    NOT a cross-DB ATTACH operation. Operates on one DB at a time.

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
