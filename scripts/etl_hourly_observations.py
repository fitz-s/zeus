"""ETL: Hourly observations from rainstorm.db → zeus.db:hourly_observations.

Source: rainstorm.db:observations WHERE granularity='hourly'
  - openmeteo_archive_hourly: 114,168 rows
  - meteostat_hourly: 105,351 rows
Target: zeus.db:hourly_observations

Validates:
- local_hour in 0-23 range
- temp value within sane bounds per unit
- European cities → unit='C', US cities → unit='F'
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema

RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"

# European/Asian cities where unit is °C
CELSIUS_CITIES = {
    "London", "Paris", "Seoul", "Tokyo", "Shanghai", "Shenzhen",
    "Munich", "Wellington", "Buenos Aires", "Hong Kong", "Singapore",
    "Taipei", "Beijing", "Chengdu", "Chongqing", "Istanbul", "Madrid",
    "Milan", "Moscow", "Sao Paulo", "Warsaw", "Wuhan", "Ankara",
    "Lucknow", "Mexico City", "Tel Aviv",
}


def run_etl() -> dict:
    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

    zeus = get_connection()
    init_schema(zeus)

    existing = zeus.execute("SELECT COUNT(*) FROM hourly_observations").fetchone()[0]
    print(f"hourly_observations has {existing} existing rows. Running incremental sync...")

    # Read hourly observations — use temp_current_f for hourly, fallback to temp_high_f
    rows = rs.execute("""
        SELECT city, target_date, local_hour, source,
               COALESCE(temp_current_f, temp_high_f) as temp
        FROM observations
        WHERE granularity = 'hourly'
          AND local_hour IS NOT NULL
          AND COALESCE(temp_current_f, temp_high_f) IS NOT NULL
    """).fetchall()

    print(f"Source rows: {len(rows):,}")

    imported = 0
    rejected = 0
    batch = []

    for r in rows:
        city = r["city"]
        hour = int(r["local_hour"])
        temp = r["temp"]
        unit = "C" if city in CELSIUS_CITIES else "F"

        # Validation: hour range
        if hour < 0 or hour > 23:
            rejected += 1
            continue

        # Validation: temperature sanity
        if unit == "C" and (temp > 55 or temp < -45):
            rejected += 1
            continue
        if unit == "F" and (temp > 135 or temp < -50):
            rejected += 1
            continue

        batch.append((
            city, r["target_date"], hour, temp, unit, r["source"]
        ))

        if len(batch) >= 10000:
            zeus.executemany("""
                INSERT OR IGNORE INTO hourly_observations
                (city, obs_date, obs_hour, temp, temp_unit, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, batch)
            imported += len(batch)
            batch = []

    if batch:
        zeus.executemany("""
            INSERT OR IGNORE INTO hourly_observations
            (city, obs_date, obs_hour, temp, temp_unit, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, batch)
        imported += len(batch)

    zeus.commit()

    final = zeus.execute("SELECT COUNT(*) FROM hourly_observations").fetchone()[0]

    rs.close()
    zeus.close()

    print(f"Attempted: {imported:,}, Rejected: {rejected:,}")
    print(f"Final row count: {final:,}")

    # Show per-city breakdown
    zeus2 = get_connection()
    for row in zeus2.execute("""
        SELECT city, source, COUNT(*) as n
        FROM hourly_observations
        GROUP BY city, source
        ORDER BY n DESC
        LIMIT 20
    """).fetchall():
        print(f"  {row['city']:20s} {row['source']:30s} {row['n']:,}")
    zeus2.close()

    return {"imported": final, "rejected": rejected}


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
