# Created: (pre-rule legacy)
# Last reused or audited: 2026-07-19
"""ETL: Temperature persistence from daily observations.

Source: zeus-forecasts.db:observations (canonical daily observations)
Target: zeus-world.db:temp_persistence

Computes day-over-day temperature change distribution per city×season.
Used for ENS anomaly detection: when ENS predicts a 10°F drop but persistence
data says that only happens 5% of the time → flag and widen CI.

ZEUS_SPEC §14.4 ETL 7.
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name  # noqa: E402


def season_from_date(date_str: str, city_name: str = "") -> str:
    """Hemisphere-aware season code."""
    from src.calibration.manager import season_from_date as _sfd, lat_for_city
    lat = lat_for_city(city_name) if city_name else 90.0
    return _sfd(date_str, lat=lat)


from src.state.db import (  # noqa: E402
    get_forecasts_connection_read_only as get_read_connection,
    get_world_connection as get_write_connection,
)


ETL_WORLD_WRITE_BUSY_TIMEOUT_MS = 250

# Delta buckets for temperature change classification
DELTA_BUCKETS = [
    ("<-10", lambda d: d < -10),
    ("-10 to -5", lambda d: -10 <= d < -5),
    ("-5 to -3", lambda d: -5 <= d < -3),
    ("-3 to -1", lambda d: -3 <= d < -1),
    ("-1 to 1", lambda d: -1 <= d < 1),
    ("1 to 3", lambda d: 1 <= d < 3),
    ("3 to 5", lambda d: 3 <= d < 5),
    ("5 to 10", lambda d: 5 <= d < 10),
    (">10", lambda d: d >= 10),
]


def _classify_delta(delta: float) -> str:
    """Classify a temperature delta into a bucket."""
    for label, pred in DELTA_BUCKETS:
        if pred(delta):
            return label
    return ">10"  # fallback


def _is_canonical_daily_observation(
    city_name: str,
    source: str,
    station_id: str | None,
) -> bool:
    """Match the executable harvester's finalized source-family contract."""
    city = cities_by_name.get(city_name)
    if city is None:
        return False
    src = source.strip().lower()
    if city.settlement_source_type == "wu_icao":
        family_match = src == "wu_icao_history" or src.startswith("wu_icao_history_")
        expected_station = str(city.wu_station or "").strip().upper()
    elif city.settlement_source_type == "noaa":
        family_match = src.startswith("ogimet_metar_")
        expected_station = str(city.wu_station or "").strip().upper()
    elif city.settlement_source_type == "hko":
        family_match = src == "hko_daily_api" or src.startswith("hko_daily_api_")
        expected_station = "HKO"
    else:
        return False
    if not family_match or not expected_station:
        return False
    station = str(station_id or "").strip().upper()
    return station == expected_station or station.startswith(f"{expected_station}:")


def run_etl() -> dict:
    source = get_read_connection()
    try:
        # observations is forecast-class canonical truth. The world table with
        # the same name is a retained ghost, not an input authority.
        rows = source.execute("""
            SELECT city, target_date, high_temp, source, station_id
            FROM observations
            WHERE high_temp IS NOT NULL
              AND authority = 'VERIFIED'
            ORDER BY city, target_date, source
        """).fetchall()
    finally:
        source.close()

    # The executable source contract admits at most one finalized row per city-date.
    daily_temps = {}
    for r in rows:
        if not _is_canonical_daily_observation(
            str(r["city"]),
            str(r["source"]),
            r["station_id"],
        ):
            continue
        key = (r["city"], r["target_date"])
        if key not in daily_temps:
            daily_temps[key] = r["high_temp"]

    print(f"Unique city-date observations: {len(daily_temps):,}")

    # Group by city, sort by date, compute day-over-day deltas
    by_city = defaultdict(list)
    for (city, date_str), temp in sorted(daily_temps.items()):
        by_city[city].append((date_str, temp))

    # Compute deltas and classify
    persistence_data = defaultdict(list)  # (city, season, bucket) → [next_day_reversions]

    for city, date_temps in by_city.items():
        date_temps.sort()
        for i in range(len(date_temps) - 1):
            date1, temp1 = date_temps[i]
            date2, temp2 = date_temps[i + 1]

            # Only consecutive dates (skip gaps)
            from datetime import date as dt_date
            d1 = dt_date.fromisoformat(date1)
            d2 = dt_date.fromisoformat(date2)
            if (d2 - d1).days != 1:
                continue

            delta = temp2 - temp1
            season = season_from_date(date1, city_name=city)
            bucket = _classify_delta(delta)

            # Compute next-day reversion: if temp went up 5°F, did it come back?
            if i + 2 < len(date_temps):
                date3, temp3 = date_temps[i + 2]
                d3 = dt_date.fromisoformat(date3)
                if (d3 - d2).days == 1:
                    reversion = temp3 - temp2  # negative = reverting
                    persistence_data[(city, season, bucket)].append(reversion)
                else:
                    persistence_data[(city, season, bucket)].append(None)
            else:
                persistence_data[(city, season, bucket)].append(None)

    # Count total per (city, season) for frequency calculation
    total_per_cs = defaultdict(int)
    for (city, season, bucket), reversions in persistence_data.items():
        total_per_cs[(city, season)] += len(reversions)

    persistence_rows: list[tuple] = []
    for (city, season, bucket), reversions in sorted(persistence_data.items()):
        n = len(reversions)
        if n < 3:
            continue

        total = total_per_cs[(city, season)]
        frequency = n / total if total > 0 else 0.0

        valid_reversions = [r for r in reversions if r is not None]
        avg_reversion = float(np.mean(valid_reversions)) if valid_reversions else None

        persistence_rows.append(
            (
                city,
                season,
                bucket,
                round(frequency, 4),
                round(avg_reversion, 2) if avg_reversion is not None else None,
                n,
            )
        )

    # Publish only the finished projection. Schema initialization and canonical
    # history reads must never run while WORLD's single writer is held.
    zeus = get_write_connection(
        write_class="bulk",
        busy_timeout_ms=ETL_WORLD_WRITE_BUSY_TIMEOUT_MS,
    )
    try:
        zeus.execute("BEGIN IMMEDIATE")
        zeus.execute("DELETE FROM temp_persistence")
        zeus.executemany(
            """
            INSERT OR REPLACE INTO temp_persistence
            (city, season, delta_bucket, frequency, avg_next_day_reversion, n_samples)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            persistence_rows,
        )
        zeus.commit()
    except BaseException:
        zeus.rollback()
        raise
    finally:
        zeus.close()
    stored = len(persistence_rows)

    print(f"Stored {stored} persistence entries")
    return {"stored": stored}


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
