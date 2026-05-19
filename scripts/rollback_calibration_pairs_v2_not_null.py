#!/usr/bin/env python3
# Lifecycle: created=2026-05-19; last_reviewed=2026-05-19; last_reused=never
# Purpose: Operator rollback script — removes NOT NULL trigger or DDL enforcement on calibration_pairs_v2.decision_group_id
# Reuse: Only needed if migrate script SAVEPOINT did not roll back cleanly; dry-run first
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.2, migration_dry_runs.json
"""Rollback NOT NULL constraint on calibration_pairs_v2.decision_group_id.

Purpose:
    Reverses the NOT NULL migration applied by migrate_calibration_pairs_v2_not_null.py.

    For --mode=trigger rollback: DROP TRIGGER IF EXISTS for both triggers per table.
    For --mode=rebuild rollback: requires backup table {table}_pre_not_null_backup
        created by the migrate script (if any). Rebuilds the table with nullable
        decision_group_id.

    If no --mode is specified, defaults to trigger rollback (the recommended forward
    migration is trigger-mode, so the default rollback is trigger-mode).

CRITICAL SAFETY RULES:
    - DEFAULT: dry_run=True. Never executes DDL without --apply.
    - Trigger rollback: DROP TRIGGER IF EXISTS — idempotent.
    - Rebuild rollback: requires ~50 GiB free disk (same as rebuild mode).

Usage:
    # Dry run (default):
    python scripts/rollback_calibration_pairs_v2_not_null.py

    # Live trigger rollback:
    python scripts/rollback_calibration_pairs_v2_not_null.py --apply --mode trigger

    # Live rebuild rollback:
    python scripts/rollback_calibration_pairs_v2_not_null.py \\
        --apply --mode rebuild --require-free-disk-gib 55 --table forecasts

Flags:
    --dry-run               Default. Print rollback plan, touch nothing.
    --apply                 Execute DDL rollback.
    --mode {trigger,rebuild}  Rollback mode matching the forward migration (default: trigger).
    --require-free-disk-gib N  Disk guard for --mode=rebuild (default: 55).
    --table {forecasts,world,both}
    --db-dir DIR

Exit codes:
    0  Rollback succeeded (or dry-run completed)
    1  Pre-flight failure (disk insufficient, DB not found)
    2  Rollback failed (see error output)
    3  Unexpected error
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

_FORECASTS_DB = "zeus-forecasts.db"
_WORLD_DB = "zeus-world.db"
_TABLE_FORECASTS = "calibration_pairs_v2"
_TABLE_WORLD = "calibration_pairs_v2_archived_2026_05_11"

_DB_TABLE_MAP = {
    "forecasts": [(_FORECASTS_DB, _TABLE_FORECASTS)],
    "world": [(_WORLD_DB, _TABLE_WORLD)],
    "both": [(_FORECASTS_DB, _TABLE_FORECASTS), (_WORLD_DB, _TABLE_WORLD)],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rollback NOT NULL constraint on calibration_pairs_v2.decision_group_id."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Execute DDL rollback. Without this flag, dry-run only.",
    )
    parser.add_argument(
        "--mode",
        choices=["trigger", "rebuild"],
        default="trigger",
        help="Rollback mode matching the forward migration (default: trigger).",
    )
    parser.add_argument(
        "--require-free-disk-gib",
        type=float,
        default=55.0,
        metavar="N",
        help="Minimum free disk GiB required for --mode=rebuild (default: 55).",
    )
    parser.add_argument(
        "--table",
        choices=["forecasts", "world", "both"],
        default="both",
        help="Which DB/table to roll back (default: both).",
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
    from src.config import STATE_DIR
    return STATE_DIR


def build_rollback_plan(mode: str, target_tables: list[tuple[str, str]]) -> list[str]:
    """Return ordered list of DDL/DML steps as human-readable strings."""
    plan = []
    if mode == "trigger":
        for db_file, table in target_tables:
            plan.append(f"[{db_file}] DROP TRIGGER IF EXISTS {table}_dgid_not_null_ins")
            plan.append(f"[{db_file}] DROP TRIGGER IF EXISTS {table}_dgid_not_null_upd")
    elif mode == "rebuild":
        for db_file, table in target_tables:
            plan.append(f"[{db_file}] SAVEPOINT rollback_not_null_{table}")
            plan.append(f"[{db_file}] CREATE TABLE {table}_new (same DDL, decision_group_id TEXT nullable)")
            plan.append(f"[{db_file}] INSERT INTO {table}_new SELECT * FROM {table}")
            plan.append(f"[{db_file}] DROP TABLE {table}")
            plan.append(f"[{db_file}] ALTER TABLE {table}_new RENAME TO {table}")
            plan.append(f"[{db_file}] RELEASE SAVEPOINT rollback_not_null_{table}")
    return plan


def _drop_triggers(conn: sqlite3.Connection, table: str) -> None:
    """Drop NOT NULL enforcement triggers. Idempotent via IF EXISTS."""
    conn.execute(f"DROP TRIGGER IF EXISTS {table}_dgid_not_null_ins")
    conn.execute(f"DROP TRIGGER IF EXISTS {table}_dgid_not_null_upd")


def _get_table_create_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Table {table!r} not found in sqlite_master")
    return row[0]


def _strip_not_null(create_sql: str, table: str) -> str:
    """Rewrite CREATE TABLE DDL to remove NOT NULL on decision_group_id column."""
    import re
    # Replace 'decision_group_id TEXT NOT NULL' -> 'decision_group_id TEXT'
    pattern = r'(decision_group_id\s+TEXT)\s+NOT NULL'
    replacement = r'\1'
    new_sql = re.sub(pattern, replacement, create_sql)
    new_sql = new_sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_new", 1)
    new_sql = new_sql.replace(f'CREATE TABLE "{table}"', f'CREATE TABLE "{table}_new"', 1)
    return new_sql


def _get_index_ddl(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return CREATE INDEX DDL statements for all user-defined indexes on table."""
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    return [row[0] for row in rows if row[0]]


def _apply_rebuild_rollback(conn: sqlite3.Connection, table: str) -> None:
    """Rebuild table removing NOT NULL on decision_group_id under SAVEPOINT.

    Preserves all user-defined indexes: captured before DROP, recreated
    after RENAME.
    """
    sp = f"rollback_not_null_{table}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        original_sql = _get_table_create_sql(conn, table)
        # Capture index DDL before dropping the table.
        index_ddl_list = _get_index_ddl(conn, table)
        new_sql = _strip_not_null(original_sql, table)
        conn.execute(new_sql)
        cols = [
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})")
        ]
        col_list = ", ".join(cols)
        conn.execute(
            f"INSERT INTO {table}_new ({col_list}) SELECT {col_list} FROM {table}"  # noqa: S608
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        # Recreate indexes on the renamed table.
        for idx_sql in index_ddl_list:
            conn.execute(idx_sql)
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise


def execute_rollback(db_path: str, table_name: str, mode: str) -> dict:
    """Execute rollback on a single DB/table. Return result dict."""
    result = {"db_path": db_path, "table": table_name, "mode": mode, "ok": False, "error": None}
    if not Path(db_path).exists():
        result["error"] = f"DB not found: {db_path}"
        return result
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            if mode == "trigger":
                _drop_triggers(conn, table_name)
                conn.commit()
            elif mode == "rebuild":
                _apply_rebuild_rollback(conn, table_name)
                conn.commit()
            result["ok"] = True
        finally:
            conn.close()
    except (sqlite3.Error, RuntimeError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def main() -> int:
    args = parse_args()

    try:
        db_dir = _resolve_db_dir(args.db_dir)
    except Exception as exc:
        print(f"ERROR resolving DB dir: {exc}", file=sys.stderr)
        return 3

    target_entries = _DB_TABLE_MAP[args.table]
    plan = build_rollback_plan(args.mode, target_entries)

    print(f"Rollback plan (mode={args.mode}, apply={args.apply}):")
    for step in plan:
        print(f"  {step}")

    if not args.apply:
        print("\nDry-run complete (run with --apply to execute rollback).")
        return 0

    # Disk guard for rebuild rollback.
    if args.mode == "rebuild":
        db_check_dir = db_dir if db_dir.exists() else Path(".")
        disk = shutil.disk_usage(db_check_dir)
        free_gib = disk.free / (1024 ** 3)
        if free_gib < args.require_free_disk_gib:
            print(
                f"ERROR: --mode=rebuild requires {args.require_free_disk_gib:.1f} GiB free disk, "
                f"only {free_gib:.1f} GiB available. Aborting.",
                file=sys.stderr,
            )
            return 1

    all_ok = True
    for db_filename, table_name in target_entries:
        db_path = str(db_dir / db_filename)
        print(f"\nRolling back {table_name} in {db_path} (mode={args.mode})...")
        result = execute_rollback(db_path, table_name, args.mode)
        if result["ok"]:
            print(f"  OK: {table_name} rollback complete.")
        else:
            print(f"  FAILED: {table_name}: {result['error']}", file=sys.stderr)
            all_ok = False

    if all_ok:
        print("\nRollback complete.")
        return 0
    else:
        print("\nRollback FAILED (see errors above).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
