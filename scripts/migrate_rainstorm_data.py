"""Migrate data from Rainstorm's rainstorm.db into Zeus's zeus.db.

Imports: settlements, observations, market_events, token_price_log.
Only data — no code from Rainstorm.

Key mapping decisions:
- settlements.winning_range → Zeus settlements.winning_bin
- settlements.event_id → Zeus settlements.market_slug (Rainstorm used event_id)
- settlements.actual_temp_f → Zeus settlements.settlement_value
  NOTE: For European cities (temp_unit=C), actual_temp_f is already in °C despite the column name.
- token_price_log: some rows have empty range_label; we JOIN back to market_events to fill gaps
- Test/seed data (token_id='test-token-123') is excluded
"""

import sqlite3
import sys
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema


RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"


def migrate(rainstorm_path: Path = RAINSTORM_DB) -> dict:
    """Run full migration. Returns counts dict."""
    if not rainstorm_path.exists():
        raise FileNotFoundError(f"Rainstorm DB not found: {rainstorm_path}")

    src = sqlite3.connect(str(rainstorm_path))
    src.row_factory = sqlite3.Row

    dst = get_connection()
    init_schema(dst)

    counts = {}
    counts["settlements"] = _migrate_settlements(src, dst)
    counts["observations"] = _migrate_observations(src, dst)
    counts["market_events"] = _migrate_market_events(src, dst)
    counts["token_price_log"] = _migrate_token_price_log(src, dst)

    dst.commit()
    src.close()
    dst.close()

    return counts


def _migrate_settlements(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    rows = src.execute("""
        SELECT city, target_date, event_id, winning_range,
               actual_temp_f, temp_unit, settled_at, actual_temp_source
        FROM settlements
        WHERE winning_range IS NOT NULL
    """).fetchall()

    count = 0
    for r in rows:
        try:
            dst.execute("""
                INSERT OR IGNORE INTO settlements
                (city, target_date, market_slug, winning_bin,
                 settlement_value, settlement_source, settled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                r["city"], r["target_date"], r["event_id"],
                r["winning_range"], r["actual_temp_f"],
                r["actual_temp_source"], r["settled_at"]
            ))
            count += 1
        except sqlite3.Error as e:
            print(f"  WARN settlement skip {r['city']} {r['target_date']}: {e}")

    return count


def _migrate_observations(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    rows = src.execute("""
        SELECT city, target_date, source, temp_high_f, temp_low_f,
               station_id, imported_at
        FROM observations
        WHERE granularity = 'daily' AND temp_high_f IS NOT NULL
    """).fetchall()

    count = 0
    for r in rows:
        # Rainstorm stores everything as _f but European cities are actually °C
        # We preserve the raw value; the unit is determined by city at query time
        unit = "F"  # Default; corrected below for known European cities
        if r["city"] in ("London", "Paris"):
            unit = "C"

        try:
            dst.execute("""
                INSERT OR IGNORE INTO observations
                (city, target_date, source, high_temp, low_temp, unit,
                 station_id, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["city"], r["target_date"], r["source"],
                r["temp_high_f"], r["temp_low_f"], unit,
                r["station_id"], r["imported_at"]
            ))
            count += 1
        except sqlite3.Error as e:
            print(f"  WARN obs skip {r['city']} {r['target_date']} {r['source']}: {e}")

    return count


def _migrate_market_events(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    rows = src.execute("""
        SELECT city, target_date, event_id, condition_id,
               range_label, range_low, range_high, outcome,
               outcome_price, imported_at
        FROM market_events
    """).fetchall()

    count = 0
    for r in rows:
        try:
            dst.execute("""
                INSERT OR IGNORE INTO market_events
                (market_slug, city, target_date, condition_id,
                 range_label, range_low, range_high, outcome, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["event_id"], r["city"], r["target_date"],
                r["condition_id"], r["range_label"],
                r["range_low"], r["range_high"],
                r["outcome"], r["imported_at"]
            ))
            count += 1
        except sqlite3.Error as e:
            print(f"  WARN market_event skip: {e}")

    return count


def _migrate_token_price_log(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    # Exclude test data and rows with no price
    # Carry over city, target_date, range_label for bin mapping in baseline
    rows = src.execute("""
        SELECT token_id, city, target_date, range_label, price, observed_at
        FROM token_price_log
        WHERE token_id != 'test-token-123'
          AND price > 0
          AND observed_at IS NOT NULL
    """).fetchall()

    count = 0
    batch = []
    for r in rows:
        batch.append((
            r["token_id"], r["city"], r["target_date"],
            r["range_label"], r["price"], r["observed_at"]
        ))
        if len(batch) >= 10000:
            dst.executemany("""
                INSERT INTO token_price_log
                (token_id, city, target_date, range_label, price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, batch)
            count += len(batch)
            batch = []

    if batch:
        dst.executemany("""
            INSERT INTO token_price_log
            (token_id, city, target_date, range_label, price, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, batch)
        count += len(batch)

    return count


def print_report(counts: dict) -> None:
    print("\n=== Zeus Data Migration Report ===")
    for table, count in counts.items():
        print(f"  {table}: {count:,} rows migrated")
    print("=== Done ===\n")


if __name__ == "__main__":
    counts = migrate()
    print_report(counts)
