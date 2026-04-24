# Created: (pre-rule legacy)
# Last reused/audited: 2026-04-23
# Authority basis: .omc/plans/observation-instants-migration-iter3.md Phase 2 +
#                  docs/operations/task_2026-04-21_gate_f_data_backfill/step4_phase2_cutover.md
"""ETL: observation_instants_current -> hourly_observations compatibility view.

Source: `observation_instants_current` VIEW (Phase 2 atomic cutover indirection
over `observation_instants_v2`). Pre-Phase-2 flip the VIEW returns 0 rows,
which this script treats as a fail-closed condition.

Temperature source: `COALESCE(temp_current, running_max)`. Legacy
`observation_instants` populated `temp_current`; `observation_instants_v2`
populates `running_max`/`running_min` via extremum-preserving aggregation.
COALESCE keeps this script shape-agnostic.

`hourly_observations` remains a lossy compatibility table for older code that
expects one row per local hour. Ambiguous fallback hours are excluded so legacy
consumers do not silently treat repeated local hours as ordinary observations.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection as get_connection, init_schema


# Inlined from the retired etl_observation_instants.py (legacy-predecessor migrator).
# Temp-unit inference is cheap city-lookup; stays local to this script.
CELSIUS_CITIES = {
    "London", "Paris", "Seoul", "Tokyo", "Shanghai", "Shenzhen",
    "Munich", "Wellington", "Buenos Aires", "Hong Kong", "Singapore",
    "Taipei", "Beijing", "Chengdu", "Chongqing", "Istanbul", "Madrid",
    "Milan", "Moscow", "Sao Paulo", "Warsaw", "Wuhan", "Ankara",
    "Lucknow", "Mexico City", "Tel Aviv",
}


def infer_temp_unit(city: str) -> str:
    return "C" if city in CELSIUS_CITIES else "F"


def run_etl() -> dict:
    zeus = get_connection()
    init_schema(zeus)

    existing = zeus.execute("SELECT COUNT(*) FROM hourly_observations").fetchone()[0]
    print(
        f"hourly_observations has {existing} existing rows. "
        f"Rebuilding from observation_instants_current..."
    )

    source_count = zeus.execute(
        "SELECT COUNT(*) FROM observation_instants_current"
    ).fetchone()[0]
    if source_count == 0:
        zeus.close()
        print(
            "ERROR: observation_instants_current is empty. "
            "Check zeus_meta.observation_data_version (Phase 2 cutover) and "
            "observation_instants_v2 population."
        )
        return {"imported": 0, "rejected": 0, "error": "no observation_instants_current"}

    rows = zeus.execute(
        """
        SELECT city, target_date, source, local_timestamp, utc_timestamp,
               COALESCE(temp_current, running_max) AS temp_current,
               temp_unit, is_ambiguous_local_hour, is_missing_local_hour
        FROM observation_instants_current
        WHERE COALESCE(temp_current, running_max) IS NOT NULL
        ORDER BY city, target_date, source, utc_timestamp
        """
    ).fetchall()

    print(f"Source rows: {len(rows):,}")

    rejected = 0
    collapsed_ambiguous = 0
    excluded_ambiguous = 0
    canonical: dict[tuple[str, str, int, str], tuple[float, str, datetime]] = {}

    for row in rows:
        if row["is_ambiguous_local_hour"] or row["is_missing_local_hour"]:
            excluded_ambiguous += 1
            continue

        try:
            local_ts = datetime.fromisoformat(str(row["local_timestamp"]))
            utc_ts = datetime.fromisoformat(str(row["utc_timestamp"]))
        except ValueError:
            rejected += 1
            continue

        hour = int(local_ts.hour)
        if hour < 0 or hour > 23:
            rejected += 1
            continue

        city = str(row["city"])
        temp = float(row["temp_current"])
        unit = str(row["temp_unit"] or infer_temp_unit(city))

        if unit == "C" and not (-45 <= temp <= 55):
            rejected += 1
            continue
        if unit == "F" and not (-50 <= temp <= 135):
            rejected += 1
            continue

        key = (city, str(row["target_date"]), hour, str(row["source"]))
        existing_row = canonical.get(key)
        if existing_row is not None:
            collapsed_ambiguous += 1
            _, _, existing_utc = existing_row
            if utc_ts <= existing_utc:
                continue

        canonical[key] = (temp, unit, utc_ts)

    zeus.execute("DELETE FROM hourly_observations")
    batch = [
        (city, obs_date, obs_hour, temp, unit, source)
        for (city, obs_date, obs_hour, source), (temp, unit, _) in sorted(canonical.items())
    ]
    if batch:
        zeus.executemany(
            """
            INSERT OR REPLACE INTO hourly_observations
            (city, obs_date, obs_hour, temp, temp_unit, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            batch,
        )

    zeus.commit()
    final = zeus.execute("SELECT COUNT(*) FROM hourly_observations").fetchone()[0]
    zeus.close()

    print(
        f"Canonical rows: {final:,}, Rejected: {rejected:,}, "
        f"Collapsed ambiguous duplicates: {collapsed_ambiguous:,}, "
        f"Excluded ambiguous rows: {excluded_ambiguous:,}"
    )
    return {
        "imported": final,
        "rejected": rejected,
        "collapsed_ambiguous": collapsed_ambiguous,
        "excluded_ambiguous": excluded_ambiguous,
    }


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
