#!/usr/bin/env python3
# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PHASE_0_V4_ADDENDUM.md §R-4.2, migration_dry_runs.json
"""Migrate calibration_pairs_v2 to add NOT NULL on decision_group_id.

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
            CREATE TRIGGER IF NOT EXISTS {table}_dgid_not_null_ins
            BEFORE INSERT ON {table}
            WHEN NEW.decision_group_id IS NULL
            BEGIN SELECT RAISE(ABORT, 'NOT NULL: {table}.decision_group_id'); END;

            CREATE TRIGGER IF NOT EXISTS {table}_dgid_not_null_upd
            BEFORE UPDATE OF decision_group_id ON {table}
            WHEN NEW.decision_group_id IS NULL
            BEGIN SELECT RAISE(ABORT, 'NOT NULL: {table}.decision_group_id'); END;

    --mode=rebuild  (canonical NOT NULL column — requires ~50 GiB free):
        SQLite cannot ALTER COLUMN to add NOT NULL directly.
        Required steps per table under SAVEPOINT:
            1. SAVEPOINT migrate_not_null_{table}
            2. CREATE TABLE {table}_new (same DDL, decision_group_id TEXT NOT NULL)
            3. INSERT INTO {table}_new SELECT * FROM {table}
            4. DROP TABLE {table}
            5. ALTER TABLE {table}_new RENAME TO {table}
            6. RELEASE SAVEPOINT

Tables:
    zeus-forecasts.db :: calibration_pairs_v2 (91M rows)
    zeus-world.db :: calibration_pairs_v2_archived_2026_05_11 (53M rows)

Usage:
    # Dry run (default, safe):
    python scripts/migrate_calibration_pairs_v2_not_null.py

    # Trigger mode (RECOMMENDED — disk-safe):
    python scripts/migrate_calibration_pairs_v2_not_null.py --apply --mode trigger --table both

    # Rebuild mode (canonical NOT NULL — requires disk headroom):
    python scripts/migrate_calibration_pairs_v2_not_null.py \\
        --apply --mode rebuild --require-free-disk-gib 55 --table forecasts

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
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

# K1 DB split: separate DBs, sequential migration.
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
        description="Migrate calibration_pairs_v2 to NOT NULL on decision_group_id."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Execute DDL. Without this flag, dry-run only.",
    )
    parser.add_argument(
        "--mode",
        choices=["trigger", "rebuild"],
        default="trigger",
        help="Migration mode: trigger (default, disk-safe) or rebuild (canonical NOT NULL).",
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
        help="Which DB/table to migrate (default: both).",
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


def preflight_check(db_path: str, table_name: str) -> dict:
    """Check NULL count on the table. Return {'null_count': int, 'ok': bool, 'error': str|None}."""
    result = {"null_count": None, "ok": False, "error": None}
    if not Path(db_path).exists():
        result["error"] = f"DB not found: {db_path}"
        return result
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            nulls = conn.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE decision_group_id IS NULL"  # noqa: S608
            ).fetchone()[0]
            result["null_count"] = nulls
            result["ok"] = (nulls == 0)
            if nulls > 0:
                result["error"] = f"BLOCKED: {nulls} NULL rows in {table_name}"
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        result["error"] = f"Schema error: {exc}"
    return result


def build_migration_plan(mode: str, target_tables: list[tuple[str, str]]) -> list[str]:
    """Return ordered list of DDL/DML steps as human-readable strings."""
    plan = []
    if mode == "trigger":
        for db_file, table in target_tables:
            plan.append(f"[{db_file}] CREATE TRIGGER IF NOT EXISTS {table}_dgid_not_null_ins")
            plan.append(f"[{db_file}] CREATE TRIGGER IF NOT EXISTS {table}_dgid_not_null_upd")
    elif mode == "rebuild":
        for db_file, table in target_tables:
            plan.append(f"[{db_file}] SAVEPOINT migrate_not_null_{table}")
            plan.append(f"[{db_file}] CREATE TABLE {table}_new (same DDL, decision_group_id TEXT NOT NULL)")
            plan.append(f"[{db_file}] INSERT INTO {table}_new SELECT * FROM {table}")
            plan.append(f"[{db_file}] DROP TABLE {table}")
            plan.append(f"[{db_file}] ALTER TABLE {table}_new RENAME TO {table}")
            plan.append(f"[{db_file}] RELEASE SAVEPOINT migrate_not_null_{table}")
    return plan


def _apply_trigger_mode(conn: sqlite3.Connection, table: str) -> None:
    """Create NOT NULL enforcement triggers on table. Idempotent via IF NOT EXISTS."""
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS {table}_dgid_not_null_ins
        BEFORE INSERT ON {table}
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: {table}.decision_group_id');
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS {table}_dgid_not_null_upd
        BEFORE UPDATE OF decision_group_id ON {table}
        WHEN NEW.decision_group_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'NOT NULL: {table}.decision_group_id');
        END
    """)


def _get_table_create_sql(conn: sqlite3.Connection, table: str) -> str:
    """Return the CREATE TABLE SQL for table from sqlite_master."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Table {table!r} not found in sqlite_master")
    return row[0]


def _inject_not_null(create_sql: str, table: str) -> str:
    """Rewrite CREATE TABLE DDL to add NOT NULL on decision_group_id column.

    Replaces 'decision_group_id TEXT' (with optional trailing comma or spaces)
    with 'decision_group_id TEXT NOT NULL'. Works for both nullable and already-
    NOT-NULL columns (idempotent).
    """
    import re
    # Replace 'decision_group_id TEXT,' or 'decision_group_id TEXT\n'
    # Handles cases where NOT NULL already present (idempotent).
    pattern = r'(decision_group_id\s+TEXT)(\s+NOT NULL)?'
    replacement = r'\1 NOT NULL'
    new_sql = re.sub(pattern, replacement, create_sql)
    # Rename table reference in the CREATE TABLE statement.
    new_sql = new_sql.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}_new", 1)
    new_sql = new_sql.replace(f'CREATE TABLE "{table}"', f'CREATE TABLE "{table}_new"', 1)
    return new_sql


def _apply_rebuild_mode(conn: sqlite3.Connection, table: str) -> None:
    """Rebuild table with NOT NULL on decision_group_id under SAVEPOINT."""
    sp = f"migrate_not_null_{table}"
    conn.execute(f"SAVEPOINT {sp}")
    try:
        original_sql = _get_table_create_sql(conn, table)
        new_sql = _inject_not_null(original_sql, table)
        conn.execute(new_sql)
        # Copy all rows — will fail with IntegrityError if any NULL exists.
        # Get column list to ensure correct ordering.
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
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise


def execute_migration(
    db_path: str,
    table_name: str,
    mode: str,
) -> dict:
    """Execute migration on a single DB/table. Return result dict."""
    result = {"db_path": db_path, "table": table_name, "mode": mode, "ok": False, "error": None}
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            if mode == "trigger":
                _apply_trigger_mode(conn, table_name)
                conn.commit()
                # Verify: triggers exist.
                triggers = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                        (table_name,),
                    )
                }
                ins_name = f"{table_name}_dgid_not_null_ins"
                upd_name = f"{table_name}_dgid_not_null_upd"
                if ins_name not in triggers or upd_name not in triggers:
                    raise RuntimeError(
                        f"Trigger verification failed: expected {ins_name!r} and {upd_name!r}, "
                        f"got {triggers!r}"
                    )
            elif mode == "rebuild":
                _apply_rebuild_mode(conn, table_name)
                conn.commit()
                # Verify: notnull=1 on decision_group_id.
                info = {
                    row[1]: row[3]
                    for row in conn.execute(f"PRAGMA table_info({table_name})")
                }
                if info.get("decision_group_id") != 1:
                    raise RuntimeError(
                        f"Rebuild verification failed: notnull={info.get('decision_group_id')!r} "
                        f"on {table_name}.decision_group_id"
                    )
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

    # Build migration plan.
    plan = build_migration_plan(args.mode, target_entries)
    print(f"Migration plan (mode={args.mode}, apply={args.apply}):")
    for step in plan:
        print(f"  {step}")

    if not args.apply:
        # Dry-run: run preflight and show result, but no DDL.
        blocked = False
        for db_filename, table_name in target_entries:
            db_path = str(db_dir / db_filename)
            pf = preflight_check(db_path, table_name)
            if not pf["ok"]:
                status = "BLOCKED" if pf.get("null_count") is not None and pf["null_count"] > 0 else "ERROR"
                print(f"  [{status}] {table_name}: {pf['error']}")
                blocked = True
            else:
                print(f"  [SAFE_TO_MIGRATE] {table_name}: 0 NULLs")
        if blocked:
            print("\nDry-run verdict: BLOCKED")
        else:
            print("\nDry-run verdict: SAFE_TO_MIGRATE (run with --apply to execute)")
        return 0

    # --apply path.

    # Disk guard for rebuild mode only.
    if args.mode == "rebuild":
        disk = shutil.disk_usage(db_dir if db_dir.exists() else Path("."))
        free_gib = disk.free / (1024 ** 3)
        if free_gib < args.require_free_disk_gib:
            print(
                f"ERROR: --mode=rebuild requires {args.require_free_disk_gib:.1f} GiB free disk, "
                f"only {free_gib:.1f} GiB available. Aborting.",
                file=sys.stderr,
            )
            return 1

    # Preflight: refuse if any NULLs exist.
    for db_filename, table_name in target_entries:
        db_path = str(db_dir / db_filename)
        pf = preflight_check(db_path, table_name)
        if not pf["ok"]:
            print(f"PREFLIGHT FAILED: {pf['error']}", file=sys.stderr)
            return 1

    # Execute per-DB sequentially.
    all_ok = True
    for db_filename, table_name in target_entries:
        db_path = str(db_dir / db_filename)
        print(f"\nMigrating {table_name} in {db_path} (mode={args.mode})...")
        result = execute_migration(db_path, table_name, args.mode)
        if result["ok"]:
            print(f"  OK: {table_name} migration complete.")
        else:
            print(f"  FAILED: {table_name}: {result['error']}", file=sys.stderr)
            all_ok = False

    if all_ok:
        print("\nMigration complete.")
        return 0
    else:
        print("\nMigration FAILED (see errors above).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
