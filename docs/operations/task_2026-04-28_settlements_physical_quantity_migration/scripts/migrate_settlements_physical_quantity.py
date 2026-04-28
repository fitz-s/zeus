# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: docs/operations/task_2026-04-28_settlements_physical_quantity_migration/plan.md
"""Migrate settlements.physical_quantity from legacy to canonical string.

Legacy string : "daily_maximum_air_temperature"
Canonical string: "mx2t6_local_calendar_day_max"  (HIGH_LOCALDAY_MAX.physical_quantity)

Targets all rows where temperature_metric='high' AND physical_quantity is the legacy string.
All other columns (provenance_json, settlement_value, authority, etc.) are unchanged.

Usage:
    # Dry-run (safe, no mutations):
    python3 migrate_settlements_physical_quantity.py --db-path state/zeus-world.db

    # Apply (requires operator approval — see plan.md):
    python3 migrate_settlements_physical_quantity.py --db-path state/zeus-world.db --apply

Stdlib only: sqlite3, argparse, shutil, sys, pathlib.
No imports from src/.
"""

# SettlementSemantics / assert_settlement_value note: this migration never changes settlement_value; it updates only physical_quantity identity after operator-gated snapshot/transaction.
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

OPERATOR_APPLY_APPROVAL_ENV = "ZEUS_OPERATOR_APPROVED_DB_MUTATION"


def _require_operator_apply_approval() -> None:
    if os.environ.get(OPERATOR_APPLY_APPROVAL_ENV) != "YES":
        raise SystemExit(
            "REFUSING --apply: this packet script can mutate zeus DB state or call "
            "external data sources. Set ZEUS_OPERATOR_APPROVED_DB_MUTATION=YES "
            "only after the active packet/current_state authorizes the mutation."
        )

LEGACY_STRING = "daily_maximum_air_temperature"
CANONICAL_STRING = "mx2t6_local_calendar_day_max"
SNAPSHOT_SUFFIX = ".pre-physqty-migration-2026-04-28"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate settlements.physical_quantity from legacy to canonical string."
    )
    parser.add_argument(
        "--db-path",
        required=True,
        help="Path to zeus-world.db (e.g. state/zeus-world.db)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Print what would change without mutating the DB (default).",
    )
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Execute the migration. Takes a snapshot first.",
    )
    return parser.parse_args()


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Open the DB read-only via URI (does not create or modify)."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def dry_run(db_path: Path) -> None:
    """Print migration preview without touching the DB."""
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = _open_ro(db_path)
    try:
        # Total row count by physical_quantity
        rows = conn.execute(
            "SELECT physical_quantity, COUNT(*) AS cnt FROM settlements GROUP BY physical_quantity"
        ).fetchall()
        print("=== Dry-run: settlements.physical_quantity distribution ===")
        for r in rows:
            print(f"  {r['physical_quantity']!r}: {r['cnt']} rows")

        # How many would change
        would_change = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'high' AND physical_quantity = ?",
            (LEGACY_STRING,),
        ).fetchone()[0]

        total = conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
        would_not_change = total - would_change

        print()
        print(f"  total rows       : {total}")
        print(f"  would_change     : {would_change}  ({LEGACY_STRING!r} → {CANONICAL_STRING!r})")
        print(f"  would_not_change : {would_not_change}")
        print()
        if would_change == 0:
            print("INFO: No rows to migrate (already canonical or DB is empty).")
        else:
            print(
                f"INFO: Run with --apply to migrate {would_change} row(s). "
                f"A snapshot will be taken at {db_path}{SNAPSHOT_SUFFIX}"
            )
    finally:
        conn.close()


def apply_migration(db_path: Path) -> None:
    """Execute the migration with snapshot, BEGIN IMMEDIATE txn, and assertion."""
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    if not db_path.is_file():
        print(f"ERROR: {db_path} is not a regular file", file=sys.stderr)
        sys.exit(1)

    # Verify writable before taking snapshot
    if not db_path.stat().st_mode & 0o200:
        print(f"ERROR: DB is not writable: {db_path}", file=sys.stderr)
        sys.exit(1)

    snapshot_path = Path(str(db_path) + SNAPSHOT_SUFFIX)

    # Step 1: Filesystem-level snapshot BEFORE opening any connection
    print(f"Taking snapshot: {db_path} → {snapshot_path}")
    shutil.copy2(db_path, snapshot_path)
    print(f"Snapshot taken: {snapshot_path} ({snapshot_path.stat().st_size} bytes)")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Step 2: BEGIN IMMEDIATE to acquire write lock
        conn.execute("BEGIN IMMEDIATE")

        # Step 3: Pre-count rows that will change
        pre_count = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'high' AND physical_quantity = ?",
            (LEGACY_STRING,),
        ).fetchone()[0]
        print(f"Pre-migration: {pre_count} row(s) with legacy physical_quantity={LEGACY_STRING!r}")

        if pre_count == 0:
            print("INFO: No rows to migrate (already canonical or second run). Exiting cleanly.")
            conn.execute("ROLLBACK")
            conn.close()
            print(f"migrated=0, snapshot={snapshot_path}")
            return

        # Step 4: UPDATE only legacy rows
        conn.execute(
            """
            UPDATE settlements
            SET physical_quantity = ?
            WHERE temperature_metric = 'high'
              AND physical_quantity = ?
            """,
            (CANONICAL_STRING, LEGACY_STRING),
        )

        # Step 5: Post-count assertion — no high-temp row should still carry legacy string
        remaining = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE temperature_metric = 'high' AND physical_quantity != ?",
            (CANONICAL_STRING,),
        ).fetchone()[0]

        if remaining != 0:
            # Integrity failure — rollback, restore snapshot, abort
            print(
                f"ERROR: Post-migration assertion failed. {remaining} row(s) still have "
                f"temperature_metric='high' but physical_quantity != {CANONICAL_STRING!r}.",
                file=sys.stderr,
            )
            print("Rolling back transaction...", file=sys.stderr)
            conn.execute("ROLLBACK")
            conn.close()
            print(f"Restoring snapshot from {snapshot_path}...", file=sys.stderr)
            shutil.copy2(snapshot_path, db_path)
            print("Snapshot restored. DB is unchanged.", file=sys.stderr)
            sys.exit(1)

        # Step 6: COMMIT
        conn.execute("COMMIT")
        conn.close()
        print(f"migrated={pre_count}, snapshot={snapshot_path}")

    except Exception as exc:
        # Any unexpected error: rollback and restore
        try:
            conn.execute("ROLLBACK")
            conn.close()
        except Exception:
            pass
        print(f"ERROR: Unexpected exception: {exc}", file=sys.stderr)
        print(f"Restoring snapshot from {snapshot_path}...", file=sys.stderr)
        shutil.copy2(snapshot_path, db_path)
        print("Snapshot restored. DB is unchanged.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    args = parse_args()
    if not args.dry_run:
        _require_operator_apply_approval()
    db_path = Path(args.db_path).expanduser().resolve()

    if args.dry_run:
        dry_run(db_path)
    else:
        apply_migration(db_path)


if __name__ == "__main__":
    main()
