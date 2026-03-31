"""P1 ETL: ASOS→WU offset calibration per city×season.

Source: rainstorm.db observations (wu_daily_observed + iem_asos on same date)
Target: zeus.db:asos_wu_offsets

When using ASOS as Day0 observation (WU not available),
apply this offset to correct for station differences.
"""

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date
from src.state.db import get_connection, init_schema

RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"


def run_etl() -> dict:
    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

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
    pairs = rs.execute("""
        SELECT wu.city, wu.target_date,
               wu.temp_high_f as wu_high,
               asos.temp_high_f as asos_high
        FROM observations wu
        INNER JOIN observations asos
            ON wu.city = asos.city AND wu.target_date = asos.target_date
        WHERE wu.source = 'wu_daily_observed'
          AND asos.source = 'iem_asos'
          AND wu.granularity = 'daily'
          AND asos.granularity = 'daily'
          AND wu.temp_high_f IS NOT NULL
          AND asos.temp_high_f IS NOT NULL
    """).fetchall()

    print(f"Paired WU+ASOS observations: {len(pairs)}")

    # Group by city×season
    grouped = defaultdict(list)
    for p in pairs:
        season = season_from_date(p["target_date"])
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
    rs.close()
    zeus.close()

    print(f"\nStored {count} city×season offsets")
    return {"pairs_found": len(pairs), "offsets_stored": count}


if __name__ == "__main__":
    run_etl()
