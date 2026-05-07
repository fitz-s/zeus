# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — daemon-correctness fix.
#   src/state/db.py:395-440 declares 12-column K1 dual-atom observations
#   shape; live state/zeus-world.db carries the legacy single-atom shape.
#   Drift caused every WU daily insert since 2026-04-19 to fail with
#   "table observations has no column named high_raw_value". This script is
#   the structural cure: ALTER TABLE adds the missing columns idempotently,
#   then backfills the high_*/low_* split from any rows that have a single
#   raw_value+value_type pair (legacy K1-A writes).
"""K1 observations migration — add the dual-atom columns to live state/zeus-world.db.

Invariant restored
------------------
After this migration, ``observations`` matches the K1 contract declared in
``src/state/db.py::init_schema``. Daily WU writers in
``src/data/daily_obs_append.py`` can persist (high_raw_value, low_raw_value)
pairs without falling back to "no column named ..." errors.

Idempotency
-----------
Re-runs are no-ops. ``PRAGMA table_info`` gates each ADD COLUMN.

Concurrency
-----------
The ingest daemon may be live. We use ``BEGIN IMMEDIATE`` to take an
exclusive write lock, then issue ALTERs. SQLite serialises ALTER vs.
concurrent writes; the daemon's writers will block briefly (~ms) and resume
once the migration commits.

Backfill
--------
Legacy rows shaped as (raw_value, value_type='high'|'low') get pivoted into
high_*/low_* columns. We do NOT delete legacy columns (raw_value,
value_type, provenance_metadata) — leaving them keeps existing readers
queryable while new readers consume high_*/low_*.

Usage
-----
    python scripts/migrate_observations_k1.py [--dry-run]

Dry-run prints the ALTER plan and backfill row count without touching the DB.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

logger = logging.getLogger(__name__)

# Columns required by src/state/db.py::init_schema observations CREATE TABLE.
# (column_name, sqlite_type_with_constraints) — kept in source-order so the
# diff against ``PRAGMA table_info`` is human-readable.
REQUIRED_K1_COLUMNS: tuple[tuple[str, str], ...] = (
    ("high_raw_value", "REAL"),
    ("high_raw_unit", "TEXT"),
    ("high_target_unit", "TEXT"),
    ("low_raw_value", "REAL"),
    ("low_raw_unit", "TEXT"),
    ("low_target_unit", "TEXT"),
    ("high_fetch_utc", "TEXT"),
    ("high_local_time", "TEXT"),
    ("high_collection_window_start_utc", "TEXT"),
    ("high_collection_window_end_utc", "TEXT"),
    ("low_fetch_utc", "TEXT"),
    ("low_local_time", "TEXT"),
    ("low_collection_window_start_utc", "TEXT"),
    ("low_collection_window_end_utc", "TEXT"),
    ("high_provenance_metadata", "TEXT"),
    ("low_provenance_metadata", "TEXT"),
)


def existing_columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(observations)").fetchall()}


def plan_migration(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return ALTER TABLE statements (column, type) needed to bring the
    live table up to the K1 declared shape. Empty list = already migrated."""
    have = existing_columns(conn)
    return [(name, sql_type) for name, sql_type in REQUIRED_K1_COLUMNS if name not in have]


def backfill_dual_atom(conn: sqlite3.Connection) -> dict:
    """Pivot legacy single-atom rows into high_*/low_* columns where possible.

    Strategy: a legacy row carries ``raw_value`` + ``value_type`` ('high' or
    'low') + ``provenance_metadata``. The K1 dual-atom shape splits these
    by metric. We backfill in two passes (high then low) using ``raw_unit``
    + ``target_unit`` if those columns exist; otherwise we skip safely.

    Idempotent: only updates rows where the target column is NULL.
    """
    cols = existing_columns(conn)
    has_legacy_split = (
        "raw_value" in cols
        and "value_type" in cols
        and "raw_unit" in cols
        and "target_unit" in cols
    )
    if not has_legacy_split:
        return {"backfilled_high": 0, "backfilled_low": 0, "note": "no legacy single-atom columns; skipping"}

    high_updates = conn.execute(
        """
        UPDATE observations
           SET high_raw_value = raw_value,
               high_raw_unit = raw_unit,
               high_target_unit = target_unit,
               high_fetch_utc = COALESCE(high_fetch_utc, fetch_utc),
               high_local_time = COALESCE(high_local_time, local_time),
               high_collection_window_start_utc = COALESCE(
                   high_collection_window_start_utc, collection_window_start_utc),
               high_collection_window_end_utc = COALESCE(
                   high_collection_window_end_utc, collection_window_end_utc),
               high_provenance_metadata = COALESCE(
                   high_provenance_metadata, provenance_metadata)
         WHERE value_type = 'high'
           AND high_raw_value IS NULL
        """
    ).rowcount

    low_updates = conn.execute(
        """
        UPDATE observations
           SET low_raw_value = raw_value,
               low_raw_unit = raw_unit,
               low_target_unit = target_unit,
               low_fetch_utc = COALESCE(low_fetch_utc, fetch_utc),
               low_local_time = COALESCE(low_local_time, local_time),
               low_collection_window_start_utc = COALESCE(
                   low_collection_window_start_utc, collection_window_start_utc),
               low_collection_window_end_utc = COALESCE(
                   low_collection_window_end_utc, collection_window_end_utc),
               low_provenance_metadata = COALESCE(
                   low_provenance_metadata, provenance_metadata)
         WHERE value_type = 'low'
           AND low_raw_value IS NULL
        """
    ).rowcount

    return {
        "backfilled_high": int(high_updates),
        "backfilled_low": int(low_updates),
        "note": "pivoted legacy raw_value/value_type into high_*/low_*",
    }


def run_migration(
    *,
    db_path: Optional[Path] = None,
    dry_run: bool = False,
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """Apply the migration. Returns a summary dict.

    Parameters
    ----------
    db_path: Override the default world DB path (used in tests).
    dry_run: If True, prints plan and rolls back. Default False.
    conn: Optional pre-opened connection (used in tests). Caller owns close.
    """
    own_conn = conn is None
    if own_conn:
        if db_path is None:
            from src.state.db import ZEUS_WORLD_DB_PATH

            db_path = ZEUS_WORLD_DB_PATH
        conn = sqlite3.connect(str(db_path), timeout=120)
        conn.execute("PRAGMA journal_mode=WAL")

    try:
        plan = plan_migration(conn)
        if not plan:
            logger.info("observations already migrated; no ALTER TABLE needed.")
            backfill = backfill_dual_atom(conn) if not dry_run else {
                "backfilled_high": 0,
                "backfilled_low": 0,
                "note": "dry-run; backfill skipped",
            }
            if not dry_run:
                conn.commit()
            return {
                "status": "noop_already_migrated",
                "altered": [],
                "backfill": backfill,
            }

        # BEGIN IMMEDIATE — exclusive write lock so the daemon's K2 writers
        # block briefly instead of racing the ALTERs.
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            # Already in a transaction (e.g., test fixture). Continue.
            pass
        altered: list[str] = []
        for col, sql_type in plan:
            stmt = f"ALTER TABLE observations ADD COLUMN {col} {sql_type}"
            if dry_run:
                logger.info("[dry-run] %s", stmt)
            else:
                conn.execute(stmt)
                logger.info("Applied: %s", stmt)
            altered.append(col)

        backfill = backfill_dual_atom(conn) if not dry_run else {
            "backfilled_high": 0,
            "backfilled_low": 0,
            "note": "dry-run; backfill skipped",
        }

        if dry_run:
            conn.rollback()
            return {"status": "dry_run", "altered": altered, "backfill": backfill}
        conn.commit()
        return {"status": "migrated", "altered": altered, "backfill": backfill}
    finally:
        if own_conn:
            conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print plan and exit; no ALTERs applied")
    parser.add_argument("--db-path", type=Path, default=None, help="Override DB path (default: state/zeus-world.db)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from src.state.db import ZEUS_WORLD_DB_PATH  # noqa: PLC0415
    _lock_path = args.db_path if args.db_path else ZEUS_WORLD_DB_PATH
    with db_writer_lock(_lock_path, WriteClass.BULK):
        summary = run_migration(db_path=args.db_path, dry_run=args.dry_run)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
