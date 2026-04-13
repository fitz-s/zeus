"""K4 migration: add authority column to world tables + downgrade non-TIGGE rows.

WORKTREE MODE: init_schema already creates observations with authority column
(K1-A). Other tables (settlements, calibration_pairs, platt_models,
ensemble_snapshots) lack it in the current init_schema. This script adds it
to all five tables for both worktree and production use.

PRODUCTION MODE: At merge time, the operator runs this script ONCE (under
explicit approval per k0-freeze / data-rebuild.md K4 rollback note):
  1. ALTER TABLE observations ADD COLUMN authority (if missing)
  2. Same for settlements, calibration_pairs, platt_models, ensemble_snapshots
  3. DELETE FROM observations WHERE source NOT IN ('tigge_derived',)
     and DELETE FROM settlements, calibration_pairs, platt_models
     -- the Revision 3 "destructive overwrite" step
  4. Preserve TIGGE ensemble_snapshots with authority='VERIFIED'
  5. No backup (user directive: no history preservation)

The script checks for ZEUS_DESTRUCTIVE_CONFIRMED=1 before running ANY DELETE.
Without that env var, it runs in dry-run mode and only reports what would happen.

Usage:
    python scripts/migrate_add_authority_column.py [--dry-run] [--db <path>]

To run destructive deletes:
    ZEUS_DESTRUCTIVE_CONFIRMED=1 python scripts/migrate_add_authority_column.py --no-dry-run
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection, init_schema


# Tables that gain authority='UNVERIFIED' by default
_UNVERIFIED_TABLES = [
    "observations",
    "settlements",
    "calibration_pairs",
    "platt_models",
]

# ensemble_snapshots default to VERIFIED (TIGGE data is trusted at ingestion)
_VERIFIED_TABLES = [
    "ensemble_snapshots",
]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def run_migration(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    destructive_confirmed: bool = False,
) -> dict:
    """Run the K4 authority column migration.

    Returns a summary dict with steps_performed and row_counts.
    """
    steps = []
    row_counts: dict[str, dict] = {}

    # Step 1: ADD COLUMN authority to unverified-default tables
    for table in _UNVERIFIED_TABLES:
        if not _table_exists(conn, table):
            steps.append(f"SKIP {table}: table does not exist")
            continue
        cols = _table_columns(conn, table)
        if "authority" in cols:
            steps.append(f"OK   {table}: authority column already present")
        else:
            if not dry_run:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN "
                    f"authority TEXT NOT NULL DEFAULT 'UNVERIFIED'"
                )
                conn.commit()
                steps.append(f"ADD  {table}: authority TEXT DEFAULT 'UNVERIFIED'")
            else:
                steps.append(f"DRY  {table}: would ADD authority TEXT DEFAULT 'UNVERIFIED'")

    # Step 2: ADD COLUMN authority to verified-default tables
    for table in _VERIFIED_TABLES:
        if not _table_exists(conn, table):
            steps.append(f"SKIP {table}: table does not exist")
            continue
        cols = _table_columns(conn, table)
        if "authority" in cols:
            steps.append(f"OK   {table}: authority column already present")
        else:
            if not dry_run:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN "
                    f"authority TEXT NOT NULL DEFAULT 'VERIFIED'"
                )
                # Non-TIGGE ensemble_snapshots downgraded to UNVERIFIED
                conn.execute(
                    "UPDATE ensemble_snapshots SET authority = 'UNVERIFIED' "
                    "WHERE model_version NOT LIKE 'ecmwf_tigge%'"
                )
                conn.commit()
                steps.append(f"ADD  {table}: authority TEXT DEFAULT 'VERIFIED' + downgrade non-TIGGE")
            else:
                steps.append(
                    f"DRY  {table}: would ADD authority TEXT DEFAULT 'VERIFIED' "
                    f"+ downgrade non-TIGGE to UNVERIFIED"
                )

    # Record row counts before destructive steps
    for table in _UNVERIFIED_TABLES + _VERIFIED_TABLES:
        if _table_exists(conn, table):
            row_counts[table] = {"before": _count(conn, table), "after": None}

    # Step 3 (DESTRUCTIVE): DELETE non-VERIFIED rows
    # Gated on ZEUS_DESTRUCTIVE_CONFIRMED=1 AND --no-dry-run
    if destructive_confirmed and not dry_run:
        steps.append("DESTRUCTIVE: ZEUS_DESTRUCTIVE_CONFIRMED=1 -- running deletes")

        # observations: keep only tigge_derived (or rows already marked VERIFIED)
        if _table_exists(conn, "observations"):
            n_before = _count(conn, "observations")
            conn.execute(
                "DELETE FROM observations "
                "WHERE authority != 'VERIFIED' AND source NOT IN ('tigge_derived')"
            )
            n_after = _count(conn, "observations")
            row_counts["observations"]["after"] = n_after
            steps.append(f"DEL  observations: {n_before} -> {n_after} rows")

        # settlements: wipe all (rebuilt by rebuild_settlements.py)
        if _table_exists(conn, "settlements"):
            n_before = _count(conn, "settlements")
            conn.execute("DELETE FROM settlements WHERE authority != 'VERIFIED'")
            n_after = _count(conn, "settlements")
            row_counts["settlements"]["after"] = n_after
            steps.append(f"DEL  settlements: {n_before} -> {n_after} rows")

        # calibration_pairs: wipe all (rebuilt by rebuild_calibration.py)
        if _table_exists(conn, "calibration_pairs"):
            n_before = _count(conn, "calibration_pairs")
            conn.execute("DELETE FROM calibration_pairs WHERE authority != 'VERIFIED'")
            n_after = _count(conn, "calibration_pairs")
            row_counts["calibration_pairs"]["after"] = n_after
            steps.append(f"DEL  calibration_pairs: {n_before} -> {n_after} rows")

        # platt_models: wipe all (rebuilt by refit_platt.py)
        if _table_exists(conn, "platt_models"):
            n_before = _count(conn, "platt_models")
            conn.execute("DELETE FROM platt_models WHERE authority != 'VERIFIED'")
            n_after = _count(conn, "platt_models")
            row_counts["platt_models"]["after"] = n_after
            steps.append(f"DEL  platt_models: {n_before} -> {n_after} rows")

        # ensemble_snapshots: do NOT delete TIGGE rows; only downgrade non-TIGGE
        # (authority column already set correctly in Step 2 above)
        if _table_exists(conn, "ensemble_snapshots"):
            n_tigge = conn.execute(
                "SELECT COUNT(*) FROM ensemble_snapshots "
                "WHERE model_version LIKE 'ecmwf_tigge%'"
            ).fetchone()[0]
            steps.append(f"KEEP ensemble_snapshots: {n_tigge} TIGGE rows preserved")

        conn.commit()

    elif not destructive_confirmed and not dry_run:
        steps.append(
            "BLOCKED: --no-dry-run specified but ZEUS_DESTRUCTIVE_CONFIRMED != '1'. "
            "Schema migration (ALTER TABLE) ran but DELETE steps were skipped. "
            "Set ZEUS_DESTRUCTIVE_CONFIRMED=1 to enable destructive deletes."
        )
    else:
        for table in _UNVERIFIED_TABLES:
            if _table_exists(conn, table):
                n = _count(conn, table)
                steps.append(f"DRY  {table}: {n} rows would be evaluated for delete")

    return {"steps": steps, "row_counts": row_counts}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="K4: Add authority column to world tables + optional destructive wipe"
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Preview only - no schema changes (default: True)",
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Apply schema changes (ALTER TABLE). Deletes still require ZEUS_DESTRUCTIVE_CONFIRMED=1.",
    )
    parser.add_argument(
        "--db", dest="db_path", default=None,
        help="Path to SQLite DB (default: production world DB)",
    )
    args = parser.parse_args()

    destructive_confirmed = os.environ.get("ZEUS_DESTRUCTIVE_CONFIRMED") == "1"

    if args.db_path:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        init_schema(conn)
    else:
        conn = get_world_connection()
        init_schema(conn)

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    destructive_note = " [DESTRUCTIVE=YES]" if destructive_confirmed and not args.dry_run else ""
    print(f"\n=== migrate_add_authority_column [{mode}{destructive_note}] ===")

    if not destructive_confirmed:
        print("  Note: ZEUS_DESTRUCTIVE_CONFIRMED not set. DELETE steps will be skipped.")

    try:
        summary = run_migration(
            conn,
            dry_run=args.dry_run,
            destructive_confirmed=destructive_confirmed,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        conn.close()
        return 1

    conn.close()

    print("\nSteps:")
    for step in summary["steps"]:
        print(f"  {step}")

    if summary["row_counts"]:
        print("\nRow counts:")
        for table, counts in summary["row_counts"].items():
            before = counts["before"]
            after = counts["after"]
            if after is not None:
                print(f"  {table:25s}: {before:6d} -> {after:6d}")
            else:
                print(f"  {table:25s}: {before:6d} rows (no delete ran)")

    # Check for success signal
    all_ok = all(
        "already present" in s or "ADD" in s or "DRY" in s or "KEEP" in s or "DEL" in s
        or "SKIP" in s or "BLOCKED" in s
        for s in summary["steps"]
    )
    if all_ok:
        if args.dry_run:
            print("\nMigration dry-run complete.")
        else:
            print("\nMigration OK: authority column operations complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
