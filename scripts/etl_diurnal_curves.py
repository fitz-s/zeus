"""ETL: Aggregate DST-safe intraday observations -> diurnal_curves/diurnal_peak_prob.

Primary source is Zeus-owned `observation_instants`, not legacy blind local_hour
fields. Ambiguous DST fallback hours are excluded from statistical tables rather
than forced into a normal local-hour bucket. Multi-source hourly observations are
collapsed to one canonical city/date/hour sample before seasonal aggregation.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection as get_connection, init_schema


def season_from_date(date_str: str, city_name: str = "") -> str:
    """Hemisphere-aware season code."""
    from src.calibration.manager import season_from_date as _sfd, lat_for_city
    lat = lat_for_city(city_name) if city_name else 90.0
    return _sfd(date_str, lat=lat)


def _obs_hour(local_timestamp: str) -> int:
    return datetime.fromisoformat(local_timestamp).hour


def run_etl() -> dict:
    zeus = get_connection()
    init_schema(zeus)

    zeus.execute("DELETE FROM diurnal_curves")
    zeus.execute("DELETE FROM diurnal_peak_prob")

    instant_count = zeus.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0]
    if instant_count == 0:
        print("ERROR: observation_instants is empty. Run etl_observation_instants.py first.")
        zeus.close()
        return {"stored": 0, "error": "no observation_instants"}

    rows = zeus.execute(
        """
        SELECT city, target_date, source, local_timestamp, utc_timestamp,
               temp_current, running_max
        FROM observation_instants
        WHERE temp_current IS NOT NULL
          AND is_missing_local_hour = 0
          AND is_ambiguous_local_hour = 0
        ORDER BY city, target_date, source, utc_timestamp
        """
    ).fetchall()

    print(f"Source: {instant_count:,} observation_instants")
    print(f"Using {len(rows):,} non-missing, non-ambiguous observation_instants for diurnal aggregation")

    canonical_day_hour = defaultdict(list)
    for row in rows:
        city = str(row["city"])
        target_date = str(row["target_date"])
        hour = _obs_hour(str(row["local_timestamp"]))
        canonical_day_hour[(city, target_date, hour)].append(
            {
                "temp_current": float(row["temp_current"]),
                "running_max": float(row["running_max"]) if row["running_max"] is not None else None,
            }
        )

    grouped = defaultdict(list)
    seasonal_high_set = defaultdict(list)
    monthly_high_set = defaultdict(list)
    per_day = defaultdict(list)

    for (city, target_date, hour), source_samples in canonical_day_hour.items():
        season = season_from_date(target_date, city_name=city)
        month = int(target_date.split("-")[1])
        temp = float(np.mean([sample["temp_current"] for sample in source_samples]))
        running_max_candidates = [
            sample["running_max"] if sample["running_max"] is not None else sample["temp_current"]
            for sample in source_samples
        ]
        running_max = max(running_max_candidates)

        grouped[(city, season, hour)].append(temp)
        per_day[(city, target_date)].append(
            {
                "hour": hour,
                "month": month,
                "season": season,
                "temp_current": temp,
                "running_max": running_max,
            }
        )

    for (city, target_date), samples in per_day.items():
        final_high = max(sample["running_max"] for sample in samples)
        for sample in samples:
            hour = int(sample["hour"])
            season = str(sample["season"])
            month = int(sample["month"])
            high_set = 1.0 if sample["running_max"] >= final_high - 1e-9 else 0.0
            seasonal_high_set[(city, season, hour)].append(high_set)
            monthly_high_set[(city, month, hour)].append(high_set)

    stored = 0
    for (city, season, hour), temps in sorted(grouped.items()):
        if len(temps) < 5:
            continue
        arr = np.array(temps, dtype=float)
        high_set_obs = seasonal_high_set.get((city, season, hour), [])
        p_high_set = float(np.mean(high_set_obs)) if high_set_obs else None
        zeus.execute(
            """
            INSERT OR REPLACE INTO diurnal_curves
            (city, season, hour, avg_temp, std_temp, n_samples, p_high_set)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (city, season, hour, float(arr.mean()), float(arr.std()), len(temps), p_high_set),
        )
        stored += 1

    monthly_rows = 0
    for (city, month, hour), obs in sorted(monthly_high_set.items()):
        if len(obs) < 5:
            continue
        zeus.execute(
            """
            INSERT OR REPLACE INTO diurnal_peak_prob
            (city, month, hour, p_high_set, n_obs)
            VALUES (?, ?, ?, ?, ?)
            """,
            (city, month, hour, float(np.mean(obs)), len(obs)),
        )
        monthly_rows += 1

    zeus.commit()

    peak_check = zeus.execute(
        """
        SELECT hour, avg_temp FROM diurnal_curves
        WHERE city = 'NYC' AND season = 'DJF'
        ORDER BY avg_temp DESC
        LIMIT 3
        """
    ).fetchall()
    if peak_check:
        print("\nVerification - NYC DJF peak hours:")
        for row in peak_check:
            print(f"  Hour {row['hour']:2d}: avg_temp={row['avg_temp']:.1f}")

    zeus.close()
    print(f"\nStored {stored} diurnal curve entries and {monthly_rows} monthly probability rows")
    return {"stored": stored, "monthly_rows": monthly_rows}


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
