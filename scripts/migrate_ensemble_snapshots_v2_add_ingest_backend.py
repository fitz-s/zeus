# Created: 2026-05-07
# Last reused/audited: 2026-05-07
# Authority basis: docs/operations/TIGGE_DOWNLOAD_SPEC_v3_2026_05_07.md §3 Phase 0 #5
#                  + critic v2 reject for spec v3 (A1 BLOCKER):
#                  ALTER ensemble_snapshots_v2 ADD COLUMN ingest_backend so live
#                  ECDS-routed rows are distinguishable from legacy webapi-routed
#                  rows. Pre-2026-05-07 historical = 'unknown'. Post-cutover
#                  writes carry 'ecds' or 'webapi'.
"""Schema migration: add ``ingest_backend`` column to ``ensemble_snapshots_v2``.

What this migrates
------------------
- ``ensemble_snapshots_v2 ADD COLUMN ingest_backend TEXT NOT NULL DEFAULT 'unknown'``

Idempotent — repeats are no-ops:
- ``ALTER TABLE ... ADD COLUMN`` raises ``OperationalError: duplicate column name``
  on second invocation. We catch that exact error and skip.
- Legacy rows pre-cutover keep value ``'unknown'`` (cannot be retroactively
  distinguished). Post-cutover writers set ``'ecds'`` / ``'webapi'`` per
  src/data/ecmwf_open_data.py + scripts/ingest_grib_to_snapshots.py.

Live trade daemon must remain DOWN during this migration (mirrors Phase 2
cycle stratification migration discipline). The ALTER itself is atomic,
but the cohort intent is consistent metadata across DB.

Usage (from zeus repo root, zeus venv active)::

    python scripts/migrate_ensemble_snapshots_v2_add_ingest_backend.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

ZEUS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ZEUS_ROOT))

from src.state.db import get_world_connection  # noqa: E402

logger = logging.getLogger(__name__)


COLUMN_NAME = "ingest_backend"
TABLE_NAME = "ensemble_snapshots_v2"
ALTER_SQL = (
    f"ALTER TABLE {TABLE_NAME} "
    f"ADD COLUMN {COLUMN_NAME} TEXT NOT NULL DEFAULT 'unknown'"
)


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # PRAGMA table_info returns rows whose column[1] is the column name.
    return any(r[1] == column for r in rows)


def migrate(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    """Apply the ``ingest_backend`` ALTER. Returns a status dict.

    Idempotent: if the column already exists, returns ``{"applied": False, ...}``
    without modifying the DB. Otherwise applies the ALTER inside an explicit
    transaction.
    """
    if _has_column(conn, TABLE_NAME, COLUMN_NAME):
        return {
            "applied": False,
            "reason": "column_already_present",
            "table": TABLE_NAME,
            "column": COLUMN_NAME,
        }

    if dry_run:
        return {
            "applied": False,
            "reason": "dry_run",
            "table": TABLE_NAME,
            "column": COLUMN_NAME,
            "sql": ALTER_SQL,
        }

    conn.execute("BEGIN")
    try:
        conn.execute(ALTER_SQL)
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        # Race-safe: another writer applied the same ALTER between our PRAGMA
        # check and our execute. Treat as success.
        if "duplicate column name" in str(exc).lower():
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            return {
                "applied": False,
                "reason": "race_already_applied",
                "table": TABLE_NAME,
                "column": COLUMN_NAME,
            }
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise

    return {
        "applied": True,
        "table": TABLE_NAME,
        "column": COLUMN_NAME,
        "sql": ALTER_SQL,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ALTER without applying it",
    )
    p.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Override DB path (default: zeus-world.db via get_world_connection)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)

    if args.db_path:
        conn = sqlite3.connect(str(args.db_path))
        conn.row_factory = sqlite3.Row
    else:
        conn = get_world_connection()

    try:
        result = migrate(conn, dry_run=args.dry_run)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    logger.info("migration result: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
