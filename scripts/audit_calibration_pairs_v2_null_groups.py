#!/usr/bin/env python3
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.1, migration_dry_runs.json
"""Audit calibration_pairs_v2 for NULL decision_group_id rows.

SCAFFOLD — outline only. No production logic implemented.
Implementation deferred to PR 4 production phase.

Purpose:
    Read-only preflight check. Counts NULL decision_group_id rows on BOTH
    calibration tables before the NOT NULL migration:
      - zeus-forecasts.db :: calibration_pairs_v2 (live, 91M rows)
      - zeus-world.db :: calibration_pairs_v2_archived_2026_05_11 (53M rows)

    Reports verdict: SAFE_TO_MIGRATE (0 NULLs) or BLOCKED (N NULLs found).

Usage:
    python scripts/audit_calibration_pairs_v2_null_groups.py
    python scripts/audit_calibration_pairs_v2_null_groups.py --json

Flags:
    --json       Emit machine-readable JSON to stdout (for CI consumption)
    --db-dir     Override default state/ directory for DB paths

Exit codes:
    0  SAFE_TO_MIGRATE on all tables
    1  BLOCKED — NULLs found (counts in output)
    2  DB not found or schema error

SCAFFOLD outline:
    1. Resolve DB paths from config (forecasts.db, world.db)
    2. Open read-only connections (uri=True, mode=ro)
    3. COUNT(*) WHERE decision_group_id IS NULL on each table
    4. Collect disk free bytes via shutil.disk_usage
    5. Emit verdict table + JSON if --json
    6. Exit 0 if all counts == 0, else exit 1
"""

import argparse
import sys


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("audit_calibration_pairs_v2_null_groups: SCAFFOLD only")


def audit_table(db_path: str, table_name: str) -> dict:
    """Return {table, db_path, null_count, total_count, status}.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("audit_table: SCAFFOLD only")


def main() -> int:
    """Entry point.

    SCAFFOLD: not implemented.
    """
    raise NotImplementedError("audit_calibration_pairs_v2_null_groups.main: SCAFFOLD only")


if __name__ == "__main__":
    sys.exit(main())
