from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "state" / "zeus.db"

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--ci", action="store_true")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"db not found: {args.db} (parity check skipped)")
        return 0

    conn = sqlite3.connect(str(args.db))
    try:
        if not table_exists(conn, "position_events") or not table_exists(conn, "position_current"):
            print("canonical ledger tables not present yet; replay parity is staged")
            return 0

        position_count = conn.execute("SELECT COUNT(*) FROM position_current").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM position_events").fetchone()[0]
        print(f"position_current rows={position_count}, position_events rows={event_count}")
        # Placeholder for strict parity fold. Once P1 lands, replace with real fold + compare.
        return 0
    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())
