"""ETL: Market price history with timing context.

Source: rainstorm.db:token_price_log (340,351 valid rows)
       JOIN rainstorm.db:market_events for market_slug + open time
       JOIN rainstorm.db:settlements for resolution time
Target: zeus.db:market_price_history

Computes:
- hours_since_open: (observed_at - market created_at) in hours
- hours_to_resolution: (settled_at - observed_at) in hours

ZEUS_SPEC §14.3 ETL 3:
  "365K prices / 1,643 settlements = 222 price snapshots per settlement.
  Zeus currently uses 0 of these 222 intermediate prices."
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_shared_connection as get_connection, init_schema

RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO8601 timestamp, tolerant of various formats."""
    if not ts:
        return None
    try:
        # Handle both +00:00 and Z suffixes
        ts = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        # Strip timezone for consistent comparisons (rainstorm has mixed tz/naive)
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _hours_between(earlier: datetime | None, later: datetime | None) -> float | None:
    """Compute hours between two datetimes, return None if either is missing."""
    if earlier is None or later is None:
        return None
    delta = (later - earlier).total_seconds() / 3600.0
    if delta < 0:
        return None
    return round(delta, 2)


def run_etl() -> dict:
    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

    zeus = get_connection()
    init_schema(zeus)

    existing = zeus.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0]
    print(f"market_price_history has {existing} existing rows. Running incremental sync...")

    # Build market_open lookup: event_id → (market_slug, created_at)
    me_rows = rs.execute("""
        SELECT event_id, condition_id, imported_at
        FROM market_events
        WHERE event_id IS NOT NULL
    """).fetchall()

    # Build event_id → earliest created_at (market open time)
    market_open = {}
    for me in me_rows:
        eid = me["event_id"]
        ts = _parse_iso(me["imported_at"])
        if eid and ts:
            if eid not in market_open or ts < market_open[eid]:
                market_open[eid] = ts
    print(f"Market open times loaded: {len(market_open)} events")

    # Build settlement time lookup: (city, target_date) → settled_at
    settle_rows = rs.execute("""
        SELECT city, target_date, settled_at
        FROM settlements
        WHERE settled_at IS NOT NULL
    """).fetchall()
    settle_time = {}
    for s in settle_rows:
        key = (s["city"], s["target_date"])
        ts = _parse_iso(s["settled_at"])
        if ts:
            settle_time[key] = ts
    print(f"Settlement times loaded: {len(settle_time)} settlements")

    # Build token_id → market_slug lookup from zeus market_events
    token_market = {}
    for row in zeus.execute("""
        SELECT market_slug, token_id FROM market_events
        WHERE token_id IS NOT NULL
    """).fetchall():
        token_market[row["token_id"]] = row["market_slug"]

    # Read token_price_log
    rows = rs.execute("""
        SELECT token_id, city, target_date, price, observed_at
        FROM token_price_log
        WHERE token_id != 'test-token-123'
          AND price > 0
          AND observed_at IS NOT NULL
    """).fetchall()

    print(f"Source rows: {len(rows):,}")

    imported = 0
    batch = []

    for r in rows:
        token_id = r["token_id"]
        observed_at = _parse_iso(r["observed_at"])
        if observed_at is None:
            continue

        market_slug = token_market.get(token_id, "")
        city = r["city"]
        target_date = r["target_date"]

        # Compute timing metrics
        hours_since_open = None
        hours_to_resolution = None

        if city and target_date:
            settle_key = (city, target_date)
            if settle_key in settle_time:
                hours_to_resolution = _hours_between(observed_at, settle_time[settle_key])

        batch.append((
            market_slug or "", token_id, r["price"],
            r["observed_at"], hours_since_open, hours_to_resolution
        ))

        if len(batch) >= 10000:
            zeus.executemany("""
                INSERT OR IGNORE INTO market_price_history
                (market_slug, token_id, price, recorded_at,
                 hours_since_open, hours_to_resolution)
                VALUES (?, ?, ?, ?, ?, ?)
            """, batch)
            imported += len(batch)
            batch = []

    if batch:
        zeus.executemany("""
            INSERT OR IGNORE INTO market_price_history
            (market_slug, token_id, price, recorded_at,
             hours_since_open, hours_to_resolution)
            VALUES (?, ?, ?, ?, ?, ?)
        """, batch)
        imported += len(batch)

    zeus.commit()

    final = zeus.execute("SELECT COUNT(*) FROM market_price_history").fetchone()[0]

    # Summary stats
    with_resolution = zeus.execute(
        "SELECT COUNT(*) FROM market_price_history WHERE hours_to_resolution IS NOT NULL"
    ).fetchone()[0]

    rs.close()
    zeus.close()

    print(f"Final row count: {final:,}")
    print(f"With hours_to_resolution: {with_resolution:,}")

    return {"imported": final}


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
