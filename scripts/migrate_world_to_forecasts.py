# Created: 2026-05-11
# Last reused or audited: 2026-05-11
# Authority basis: PLAN docs/operations/task_2026-05-11_forecast_db_split/PLAN.md §5.4 + §5.4.1
"""K1 forecast DB migration: copy 7 forecast-class tables from zeus-world.db
to zeus-forecasts.db with checkpoint-resume (§5.4.1).

OPERATOR INSTRUCTIONS — DO NOT RUN without reading:
  1. Stop both daemons first:
       launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist
       launchctl unload ~/Library/LaunchAgents/com.zeus.trade.plist
  2. Verify no open file descriptors:
       lsof state/zeus-world.db
     Must return empty (exit 1 means no fds — that's correct).
  3. From the zeus repo root, run:
       python scripts/migrate_world_to_forecasts.py
     To resume an interrupted migration:
       python scripts/migrate_world_to_forecasts.py --resume
  4. After migration completes, restart daemons:
       launchctl load ~/Library/LaunchAgents/com.zeus.data-ingest.plist
       launchctl load ~/Library/LaunchAgents/com.zeus.trade.plist

Estimated wall-clock: 30-60 min (dominated by calibration_pairs_v2: 53M rows).

DO NOT RUN IN THIS SESSION (§5.4 migration deferred to operator window).

ROLLBACK (§8, ~30 s):
  1. Stop daemons.
  2. On zeus-world.db: ALTER TABLE X_archived_2026_05_11 RENAME TO X (7×).
  3. git revert <K1 commit> (caller routes + lock topology + boot supplement).
  4. Restart daemons.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

# Ensure zeus repo root on sys.path when run as script.
_ZEUS_ROOT = Path(__file__).resolve().parent.parent
if str(_ZEUS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ZEUS_ROOT))

from src.state.db import (
    ZEUS_WORLD_DB_PATH,
    ZEUS_FORECASTS_DB_PATH,
    init_schema_forecasts,
    assert_schema_current_forecasts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("migrate_world_to_forecasts")


# ---------------------------------------------------------------------------
# Table inventory per PLAN §5.4 / §5.4.1
# Migration order: ascending row count to maximize checkpoint usefulness.
# source_run has TEXT PK — migrated as single batch (only 7 rows).
# ---------------------------------------------------------------------------

# (table_name, pk_col, chunked)
# chunked=False → single INSERT SELECT (small tables or TEXT PK)
# chunked=True  → 50k-row batches with checkpoint-resume
_TABLES: list[tuple[str, str, bool]] = [
    ("source_run",          "source_run_id", False),  # TEXT PK, 7 rows — single batch
    ("settlements_v2",      "settlement_id", False),  # 3,987 rows — single batch
    ("settlements",         "id",            False),  # 5,570 rows — single batch
    ("market_events_v2",    "event_id",      False),  # 6,713 rows — single batch
    ("observations",        "id",            False),  # 43,903 rows — single batch
    ("ensemble_snapshots_v2", "snapshot_id", True),   # 1,119,662 rows — chunked
    ("calibration_pairs_v2",  "pair_id",     True),   # 53,490,902 rows — chunked
]

_CHUNK_SIZE = 50_000
_PROGRESS_LOG_EVERY = 10  # log every N chunks


def _init_progress_table(target: sqlite3.Connection) -> None:
    """Create migration_progress table on TARGET DB per §5.4.1."""
    target.execute("""
        CREATE TABLE IF NOT EXISTS migration_progress (
            source_table   TEXT PRIMARY KEY,
            last_copied_pk INTEGER NOT NULL DEFAULT -1,
            last_seen_count INTEGER NOT NULL DEFAULT 0,
            updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    target.commit()


def _get_last_pk(target: sqlite3.Connection, table: str) -> int:
    """Return last_copied_pk from migration_progress, or -1 if not started."""
    row = target.execute(
        "SELECT last_copied_pk FROM migration_progress WHERE source_table = ?",
        (table,),
    ).fetchone()
    return row[0] if row else -1


def _update_progress(
    target: sqlite3.Connection,
    table: str,
    last_pk: int,
    count: int,
) -> None:
    target.execute(
        """
        INSERT OR REPLACE INTO migration_progress
            (source_table, last_copied_pk, last_seen_count, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (table, last_pk, count),
    )


def _copy_single_batch(
    target: sqlite3.Connection,
    source_alias: str,
    table: str,
    resume: bool,
) -> int:
    """Copy entire table in one INSERT SELECT. Returns rows copied."""
    if resume:
        existing = target.execute(
            f"SELECT COUNT(*) FROM main.{table}"
        ).fetchone()[0]
        if existing > 0:
            logger.info(
                "  %s: resume — %d rows already present, skipping single-batch copy",
                table,
                existing,
            )
            return existing

    rows = target.execute(
        f"INSERT OR IGNORE INTO main.{table} SELECT * FROM {source_alias}.{table}"
    ).rowcount
    target.commit()
    return rows


def _copy_chunked(
    target: sqlite3.Connection,
    source_alias: str,
    table: str,
    pk_col: str,
    resume: bool,
) -> int:
    """Copy table in 50k-row chunks ordered by PK. Returns total rows copied."""
    last_pk = _get_last_pk(target, table) if resume else -1
    if resume and last_pk >= 0:
        logger.info("  %s: resuming from pk=%d", table, last_pk)

    total = 0
    chunk_idx = 0
    t0 = time.monotonic()

    while True:
        rows = target.execute(
            f"""
            INSERT OR IGNORE INTO main.{table}
            SELECT * FROM {source_alias}.{table}
            WHERE {pk_col} > ?
            ORDER BY {pk_col}
            LIMIT ?
            """,
            (last_pk, _CHUNK_SIZE),
        ).rowcount

        if rows == 0:
            break

        # Advance pk cursor
        last_pk = target.execute(
            f"SELECT MAX({pk_col}) FROM main.{table}"
        ).fetchone()[0]

        _update_progress(target, table, last_pk, rows)
        target.commit()

        total += rows
        chunk_idx += 1

        if chunk_idx % _PROGRESS_LOG_EVERY == 0:
            elapsed = time.monotonic() - t0
            rate = total / elapsed if elapsed > 0 else 0
            logger.info(
                "  %s: chunk=%d total=%d last_pk=%d rate=%.0f rows/s",
                table,
                chunk_idx,
                total,
                last_pk,
                rate,
            )

    return total


def _verify_counts(
    target: sqlite3.Connection,
    source_alias: str,
    table: str,
) -> None:
    """REL-2: assert target COUNT == source COUNT. Raises on mismatch."""
    src_count = target.execute(
        f"SELECT COUNT(*) FROM {source_alias}.{table}"
    ).fetchone()[0]
    tgt_count = target.execute(
        f"SELECT COUNT(*) FROM main.{table}"
    ).fetchone()[0]
    if src_count != tgt_count:
        raise RuntimeError(
            f"REL-2 VIOLATION: {table} source={src_count} target={tgt_count}; "
            "migration incomplete or data changed during copy. Stop and investigate."
        )
    logger.info("  %s: REL-2 OK — %d rows verified", table, tgt_count)


def run_migration(*, resume: bool = False, dry_run: bool = False) -> None:
    """Execute the 7-table migration per PLAN §5.4."""
    if dry_run:
        logger.info("DRY RUN — no data will be moved")

    # Pre-flight: source must exist.
    if not ZEUS_WORLD_DB_PATH.exists():
        raise FileNotFoundError(f"Source DB not found: {ZEUS_WORLD_DB_PATH}")

    logger.info("Source: %s (%.2f GB)", ZEUS_WORLD_DB_PATH,
                ZEUS_WORLD_DB_PATH.stat().st_size / 1e9)
    logger.info("Target: %s", ZEUS_FORECASTS_DB_PATH)

    if dry_run:
        logger.info("DRY RUN complete — exiting without writing")
        return

    # Open target + initialize schema.
    target = sqlite3.connect(str(ZEUS_FORECASTS_DB_PATH))
    target.execute("PRAGMA journal_mode=WAL")
    target.execute("PRAGMA synchronous=NORMAL")
    target.execute("PRAGMA cache_size=-65536")  # 64 MB page cache

    init_schema_forecasts(target)
    target.commit()
    assert_schema_current_forecasts(target)
    logger.info("Target schema initialized (SCHEMA_FORECASTS_VERSION=1)")

    _init_progress_table(target)

    # ATTACH source.
    source_alias = "source_world"
    target.execute(f"ATTACH DATABASE ? AS {source_alias}", (str(ZEUS_WORLD_DB_PATH),))
    logger.info("Attached source as '%s'", source_alias)

    t_total_start = time.monotonic()
    grand_total = 0

    for table, pk_col, chunked in _TABLES:
        logger.info("Migrating %s (pk=%s, chunked=%s) ...", table, pk_col, chunked)
        t_start = time.monotonic()

        if chunked:
            copied = _copy_chunked(target, source_alias, table, pk_col, resume)
        else:
            copied = _copy_single_batch(target, source_alias, table, resume)

        elapsed = time.monotonic() - t_start
        logger.info("  %s: %d rows in %.1fs", table, copied, elapsed)

        _verify_counts(target, source_alias, table)
        grand_total += copied

    target.execute(f"DETACH DATABASE {source_alias}")

    assert_schema_current_forecasts(target)
    target.close()

    elapsed_total = time.monotonic() - t_total_start
    logger.info(
        "Migration complete: %d total rows in %.1fs (%.1f min)",
        grand_total,
        elapsed_total,
        elapsed_total / 60,
    )
    logger.info(
        "Next step: ALTER TABLE X RENAME TO X_archived_2026_05_11 "
        "for each of the 7 tables on zeus-world.db (§5.4 step 7), "
        "then restart daemons."
    )


def _rename_world_tables(conn: sqlite3.Connection) -> None:
    """§5.4 step 7: RENAME (not DROP) the 7 source tables on world DB.

    O(1) catalog edits. Preserves 30-second rollback via §8 RENAME-back.
    Call this AFTER verifying migration_progress counts and daemon restart.
    """
    suffix = "_archived_2026_05_11"
    for table, _pk, _chunked in _TABLES:
        archived = f"{table}{suffix}"
        conn.execute(f"ALTER TABLE {table} RENAME TO {archived}")
        logger.info("Renamed %s → %s on world DB", table, archived)
    conn.commit()
    logger.info("All 7 tables renamed. Rollback: RENAME each back (§8).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="K1 forecast DB migration: zeus-world.db → zeus-forecasts.db"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume interrupted migration using migration_progress table",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check paths and schema only; do not move data",
    )
    parser.add_argument(
        "--rename-world-tables",
        action="store_true",
        help=(
            "§5.4 step 7: RENAME the 7 source tables on zeus-world.db "
            "(run AFTER migration + verification; requires --confirm)"
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required with --rename-world-tables to prevent accidental rename",
    )
    args = parser.parse_args()

    if args.rename_world_tables:
        if not args.confirm:
            print(
                "ERROR: --rename-world-tables requires --confirm. "
                "This renames the 7 source tables on zeus-world.db. "
                "Rollback via: ALTER TABLE X_archived_2026_05_11 RENAME TO X"
            )
            sys.exit(1)
        logger.info("Renaming 7 source tables on zeus-world.db ...")
        _world = sqlite3.connect(str(ZEUS_WORLD_DB_PATH))
        _rename_world_tables(_world)
        _world.close()
        sys.exit(0)

    run_migration(resume=args.resume, dry_run=args.dry_run)
