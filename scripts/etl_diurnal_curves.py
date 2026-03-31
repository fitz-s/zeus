"""ETL: Aggregate hourly_observations → diurnal_curves.

Source: zeus.db:hourly_observations (must be populated first via etl_hourly_observations.py)
Target: zeus.db:diurnal_curves

Computes per (city, season, hour): avg_temp, std_temp, n_samples.

WARNING from ZEUS_SPEC §14.2:
  Timezone is critical. If meteostat stores UTC, the diurnal curve
  shifts by the city's UTC offset. The 'local_hour' in rainstorm.db
  appears to already be local time — we verify by checking that peak
  temperature occurs at a reasonable local hour (13-17).
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))



def season_from_date(date_str: str) -> str:
    """Map date string (YYYY-MM-DD) to season code."""
    month = int(date_str.split("-")[1])
    if month in (12, 1, 2):
        return "DJF"
    elif month in (3, 4, 5):
        return "MAM"
    elif month in (6, 7, 8):
        return "JJA"
    return "SON"


from src.state.db import get_connection, init_schema
def run_etl() -> dict:
    zeus = get_connection()
    init_schema(zeus)

    # Always recompute from full hourly dataset (aggregation table)
    zeus.execute("DELETE FROM diurnal_curves")

    hourly_count = zeus.execute("SELECT COUNT(*) FROM hourly_observations").fetchone()[0]
    if hourly_count == 0:
        print("ERROR: hourly_observations is empty. Run etl_hourly_observations.py first.")
        zeus.close()
        return {"stored": 0, "error": "no hourly data"}

    print(f"Source: {hourly_count:,} hourly observations")

    # Group by (city, season, hour)
    rows = zeus.execute("""
        SELECT city, obs_date, obs_hour, temp
        FROM hourly_observations
    """).fetchall()

    grouped = defaultdict(list)
    for r in rows:
        season = season_from_date(r["obs_date"])
        key = (r["city"], season, r["obs_hour"])
        grouped[key].append(r["temp"])

    # Compute stats and insert
    stored = 0
    for (city, season, hour), temps in sorted(grouped.items()):
        if len(temps) < 5:
            continue
        arr = np.array(temps)
        zeus.execute("""
            INSERT OR REPLACE INTO diurnal_curves
            (city, season, hour, avg_temp, std_temp, n_samples)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (city, season, hour, float(arr.mean()), float(arr.std()), len(temps)))
        stored += 1

    zeus.commit()

    # Verification: Check peak hour for NYC
    peak_check = zeus.execute("""
        SELECT hour, avg_temp FROM diurnal_curves
        WHERE city = 'NYC' AND season = 'DJF'
        ORDER BY avg_temp DESC
        LIMIT 3
    """).fetchall()

    if peak_check:
        print(f"\nVerification — NYC DJF peak hours:")
        for row in peak_check:
            print(f"  Hour {row['hour']:2d}: avg_temp={row['avg_temp']:.1f}")
        peak_hour = peak_check[0]["hour"]
        if 12 <= peak_hour <= 17:
            print("  ✅ Peak is in expected local afternoon window")
        else:
            print(f"  ⚠️  Peak at hour {peak_hour} — may indicate timezone issue!")

    zeus.close()
    print(f"\nStored {stored} diurnal curve entries")
    return {"stored": stored}


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
