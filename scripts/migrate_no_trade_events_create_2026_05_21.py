# Created: 2026-05-20
# Last reused or audited: 2026-05-29
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742); Phase 2 T2 production pass
# Writer-lock: db_writer_lock(BULK) wraps the sqlite3.connect write path (run() opens live
# zeus-world.db by default with dry_run=False; daemon also writes zeus-world.db).
"""Idempotent schema migration for no_trade_events (world DB).

Migration semantic policy: additive-only / idempotent.
  - Only CREATE TABLE/INDEX IF NOT EXISTS; no DROP, no DML.
  - Safe to re-run: all statements guarded by IF NOT EXISTS.
  - Runs under db_writer_lock(BULK) to prevent race with live daemon.

Creates the no_trade_events table and its two indexes if they do not
already exist.  Safe to run on any DB — all statements use IF NOT EXISTS.

This migration does NOT bump PRAGMA user_version; that is handled by
db.py init_schema (SCHEMA_VERSION 15) on next daemon start.

Usage
-----
    python scripts/migrate_no_trade_events_create_2026_05_21.py [--dry-run] [--db PATH]

--dry-run: print DDL without touching the DB.
--db PATH: override world DB path (default: from src.config STATE_DIR).
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.state.db_writer_lock import WriteClass, db_writer_lock  # noqa: E402

logger = logging.getLogger(__name__)


def _get_default_db_path() -> Path:
    from src.config import STATE_DIR
    return STATE_DIR / "zeus-world.db"


def run(db_path: Path, dry_run: bool = False) -> None:
    from src.state.schema.no_trade_events_schema import (
        CREATE_INDEX_MARKET_TIME_SQL,
        CREATE_INDEX_REASON_SQL,
        CREATE_TABLE_SQL,
    )

    ddl_statements = [
        CREATE_TABLE_SQL,
        CREATE_INDEX_MARKET_TIME_SQL,
        CREATE_INDEX_REASON_SQL,
    ]

    if dry_run:
        for stmt in ddl_statements:
            print(stmt.strip())
            print("---")
        logger.info("dry-run complete — no DB changes made")
        return

    with db_writer_lock(db_path, WriteClass.BULK):
        conn = sqlite3.connect(str(db_path))
        try:
            for stmt in ddl_statements:
                conn.execute(stmt)
            conn.commit()
            logger.info("no_trade_events migration complete on %s", db_path)
        finally:
            conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db or _get_default_db_path()
    run(db_path=db_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
