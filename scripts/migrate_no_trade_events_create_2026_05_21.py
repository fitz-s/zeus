# Lifecycle: created=2026-05-20; last_reviewed=2026-05-20; last_reused=never
# Purpose: Idempotent one-shot migration creating no_trade_events table and indexes in zeus-world.db.
# Reuse: Verify DDL matches no_trade_events_schema.py and that no_trade_events does not already exist.
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742); Phase 2 T2 production pass
"""Idempotent CREATE TABLE migration for no_trade_events (world DB).

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
from pathlib import Path

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
