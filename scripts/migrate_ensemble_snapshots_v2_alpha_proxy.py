# Created: 2026-05-19
# Last reused or audited: 2026-05-19
# Authority basis: PR 6 WAVE_B_PR_3_6_FIELD_MAP.md rows 9-11; pr36_scaffold.md PR 6 migrations
"""PR 6 migration: add timing chain + alpha proxy columns to ensemble_snapshots_v2.

Adds 3 columns to ensemble_snapshots_v2 in forecasts.db:
  - first_member_observed_time TEXT  (UTC ISO; when first ENS member was downloaded)
  - run_complete_time TEXT            (UTC ISO; when all 51 members were present)
  - raw_orderbook_hash_transition_delta_ms INTEGER  (alpha proxy; NULL on first observation)

All columns are nullable. No backfill needed — pre-PR-6 rows will have NULL
(expected; instrument measures forward from merge date).

These columns are also added idempotently via v2_schema.py::_create_ensemble_snapshots_v2()
which runs on every db init. This script is for manual one-shot production migration.

Usage:
    python scripts/migrate_ensemble_snapshots_v2_alpha_proxy.py [--dry-run] [--db <path>]

Dry-run is the default. Pass --no-dry-run to apply.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


_ALTERS = [
    (
        "ensemble_snapshots_v2",
        "first_member_observed_time",
        "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN first_member_observed_time TEXT",
    ),
    (
        "ensemble_snapshots_v2",
        "run_complete_time",
        "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN run_complete_time TEXT",
    ),
    (
        "ensemble_snapshots_v2",
        "raw_orderbook_hash_transition_delta_ms",
        "ALTER TABLE ensemble_snapshots_v2 ADD COLUMN raw_orderbook_hash_transition_delta_ms INTEGER",
    ),
]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def migrate(conn: sqlite3.Connection, dry_run: bool) -> list[str]:
    applied: list[str] = []
    for table, column, sql in _ALTERS:
        if not _table_exists(conn, table):
            print(f"  SKIP {table}.{column}: table does not exist")
            continue
        if _column_exists(conn, table, column):
            print(f"  SKIP {table}.{column}: already exists")
            continue
        print(f"  {'DRY-RUN: would apply' if dry_run else 'APPLY'}: {sql}")
        if not dry_run:
            conn.execute(sql)
            applied.append(f"{table}.{column}")
    if not dry_run and applied:
        conn.commit()
    return applied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument(
        "--db",
        default=None,
        help="Path to forecasts.db (default: auto-detect via src.state.db)",
    )
    args = parser.parse_args()

    if args.db:
        db_path = args.db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    else:
        repo_root = Path(__file__).parent.parent
        sys.path.insert(0, str(repo_root))
        from src.state.db import get_forecasts_connection
        conn = get_forecasts_connection()

    print(f"{'DRY-RUN' if args.dry_run else 'APPLY'} — ensemble_snapshots_v2 PR 6 migrations")
    applied = migrate(conn, dry_run=args.dry_run)
    if args.dry_run:
        print("Dry-run complete. Pass --no-dry-run to apply.")
    else:
        print(f"Applied {len(applied)} column(s): {applied}")
    conn.close()


if __name__ == "__main__":
    main()
