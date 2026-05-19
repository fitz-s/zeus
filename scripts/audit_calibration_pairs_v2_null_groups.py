#!/usr/bin/env python3
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.1, migration_dry_runs.json
"""Audit calibration_pairs_v2 for NULL decision_group_id rows.

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
"""

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

# K1 DB split: forecasts.db and world.db are separate.
_DB_TABLE_MAP = [
    ("zeus-forecasts.db", "calibration_pairs_v2"),
    ("zeus-world.db", "calibration_pairs_v2_archived_2026_05_11"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit calibration_pairs_v2 for NULL decision_group_id rows."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON to stdout.",
    )
    parser.add_argument(
        "--db-dir",
        default=None,
        help="Override default state/ directory for DB paths.",
    )
    return parser.parse_args()


def _resolve_db_dir(db_dir_override: str | None) -> Path:
    if db_dir_override:
        return Path(db_dir_override)
    # Import STATE_DIR from src.config (same pattern as src/state/db.py)
    from src.config import STATE_DIR
    return STATE_DIR


def audit_table(db_path: str, table_name: str) -> dict:
    """Return {table, db_path, null_count, total_count, status}.

    Opens a read-only URI connection. Returns status 'SCHEMA_ERROR' if the
    table or column doesn't exist.
    """
    result = {
        "table": table_name,
        "db_path": db_path,
        "null_count": None,
        "total_count": None,
        "status": "UNKNOWN",
    }
    if not Path(db_path).exists():
        result["status"] = "DB_NOT_FOUND"
        return result
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"  # noqa: S608
            ).fetchone()[0]
            nulls = conn.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE decision_group_id IS NULL"  # noqa: S608
            ).fetchone()[0]
            result["total_count"] = total
            result["null_count"] = nulls
            result["status"] = "SAFE_TO_MIGRATE" if nulls == 0 else "BLOCKED"
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        result["status"] = "SCHEMA_ERROR"
        result["error"] = str(exc)
    return result


def main() -> int:
    args = parse_args()

    try:
        db_dir = _resolve_db_dir(args.db_dir)
    except Exception as exc:
        print(f"ERROR resolving DB dir: {exc}", file=sys.stderr)
        return 2

    disk = shutil.disk_usage(db_dir if db_dir.exists() else Path("."))
    disk_free_gib = disk.free / (1024 ** 3)

    results = []
    exit_code = 0
    for db_filename, table_name in _DB_TABLE_MAP:
        db_path = str(db_dir / db_filename)
        row = audit_table(db_path, table_name)
        results.append(row)
        if row["status"] in ("DB_NOT_FOUND", "SCHEMA_ERROR"):
            exit_code = 2
        elif row["status"] == "BLOCKED":
            exit_code = max(exit_code, 1)

    report = {
        "disk_free_gib": round(disk_free_gib, 2),
        "tables": results,
        "verdict": "SAFE_TO_MIGRATE" if exit_code == 0 else (
            "BLOCKED" if exit_code == 1 else "ERROR"
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Disk free: {disk_free_gib:.1f} GiB")
        for row in results:
            status = row["status"]
            db_path = row["db_path"]
            table = row["table"]
            null_count = row.get("null_count")
            total_count = row.get("total_count")
            if null_count is not None:
                print(
                    f"  [{status}] {table} @ {db_path}: "
                    f"{null_count} NULLs / {total_count} rows"
                )
            else:
                err = row.get("error", "")
                print(f"  [{status}] {table} @ {db_path}: {err}")
        print(f"\nVerdict: {report['verdict']}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
