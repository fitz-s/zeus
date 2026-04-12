"""P1 ETL: ASOS→WU offset calibration per city×season.

Source: zeus-world.db observations (wu_daily_observed + iem_asos on same date)
Target: zeus-world.db:asos_wu_offsets

When using ASOS as Day0 observation (WU not available),
apply this offset to correct for station differences.
"""

import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date, lat_for_city
from src.state.db import get_world_connection as get_connection, init_schema


def run_etl() -> dict:
    zeus = get_connection()
    init_schema(zeus)

    # Create asos_wu_offsets table if not exists
    zeus.execute("""
        CREATE TABLE IF NOT EXISTS asos_wu_offsets (
            city TEXT NOT NULL,
            season TEXT NOT NULL,
            offset REAL NOT NULL,
            std REAL NOT NULL,
            n_samples INTEGER NOT NULL,
            UNIQUE(city, season)
        )
    """)

    # Get paired WU + ASOS observations on same city-date
    # All migrated observations are daily (hourly excluded during migration)
    pairs = zeus.execute("""
        SELECT wu.city, wu.target_date,
               wu.high_temp as wu_high,
               asos.high_temp as asos_high
        FROM observations wu
        INNER JOIN observations asos
            ON wu.city = asos.city AND wu.target_date = asos.target_date
        WHERE wu.source = 'wu_daily_observed'
          AND asos.source = 'iem_asos'
          AND wu.high_temp IS NOT NULL
          AND asos.high_temp IS NOT NULL
    """).fetchall()

    print(f"Paired WU+ASOS observations: {len(pairs)}")

    # Group by city×season
    grouped = defaultdict(list)
    for p in pairs:
        season = season_from_date(p["target_date"], lat=lat_for_city(p["city"]))
        key = (p["city"], season)
        offset = p["wu_high"] - p["asos_high"]
        # Reject extreme outliers (> 10° difference likely unit contamination)
        if abs(offset) < 10:
            grouped[key].append(offset)

    # Compute and store offsets
    import numpy as np
    zeus.execute("DELETE FROM asos_wu_offsets")
    count = 0
    for (city, season), offsets in sorted(grouped.items()):
        if len(offsets) < 5:
            continue
        arr = np.array(offsets)
        zeus.execute("""
            INSERT OR REPLACE INTO asos_wu_offsets (city, season, offset, std, n_samples)
            VALUES (?, ?, ?, ?, ?)
        """, (city, season, float(arr.mean()), float(arr.std()), len(offsets)))
        print(f"  {city:15s} {season}: offset={arr.mean():+.2f}°, std={arr.std():.2f}, n={len(offsets)}")
        count += 1

    zeus.commit()
    zeus.close()

    print(f"\nStored {count} city×season offsets")
    return {"pairs_found": len(pairs), "offsets_stored": count}


if __name__ == "__main__":
    run_etl()
