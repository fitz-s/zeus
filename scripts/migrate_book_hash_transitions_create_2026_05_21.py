# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: 2026-05-20 live substrate repair; trade-owned executable snapshot substrate
"""Idempotent CREATE TABLE migration for book_hash_transitions (trade DB).

Creates the book_hash_transitions table and its two indexes if they do not
already exist.  Safe to run on any DB — all statements use IF NOT EXISTS.

This migration does NOT bump PRAGMA user_version; live boot creates the same
table through db.py init_schema_trade_only before registry assertion.

Usage
-----
    python scripts/migrate_book_hash_transitions_create_2026_05_21.py [--dry-run | --apply] [--db PATH]

Default is dry-run. Use --apply to modify the DB.
--db PATH: override trade DB path (default: from src.config STATE_DIR).
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _get_default_db_path() -> Path:
    from src.config import STATE_DIR
    return STATE_DIR / "zeus_trades.db"


def run(db_path: Path, dry_run: bool = False) -> None:
    from src.state.schema.book_hash_transitions_schema import (
        CREATE_INDEX_MARKET_TIME_SQL,
        CREATE_INDEX_NEW_HASH_SQL,
        CREATE_TABLE_SQL,
    )

    ddl_statements = [
        CREATE_TABLE_SQL,
        CREATE_INDEX_MARKET_TIME_SQL,
        CREATE_INDEX_NEW_HASH_SQL,
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
        logger.info("book_hash_transitions migration complete on %s", db_path)
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()

    db_path = args.db or _get_default_db_path()
    run(db_path=db_path, dry_run=not args.apply)


if __name__ == "__main__":
    main()
