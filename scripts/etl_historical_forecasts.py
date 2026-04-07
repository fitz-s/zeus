"""ETL: Historical forecasts from rainstorm.db → zeus.db:historical_forecasts + model_skill.

Source: rainstorm.db:forecasts (171,003 rows, 5 NWP models)
  - ecmwf_previous_runs: 35,518 (lead 0-7)
  - gfs_previous_runs: 35,518 (lead 0-7)
  - openmeteo_previous_runs: 34,939 (lead 0-7)
  - icon_previous_runs: 30,502 (lead 0-6)
  - ukmo_previous_runs: 30,148 (lead 0-6)
Target: zeus.db:historical_forecasts + model_skill

Validates:
- Temperature range per unit (C/F)
- Reconstructs available_at from forecast_basis_date + model delay
- UNIQUE(city, target_date, source, lead_days) — modified from original schema
"""

import sqlite3
import sys
from pathlib import Path

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


from src.state.db import get_shared_connection as get_connection, init_schema

RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"

# Source name normalization
SOURCE_MAP = {
    "ecmwf_previous_runs": "ecmwf",
    "gfs_previous_runs": "gfs",
    "icon_previous_runs": "icon",
    "openmeteo_previous_runs": "openmeteo",
    "ukmo_previous_runs": "ukmo",
    "noaa_forecast_archive": "noaa",
    "noaa_ndfd_historical_forecast": "noaa_ndfd",
}

CELSIUS_CITIES = {
    "London", "Paris", "Seoul", "Tokyo", "Shanghai", "Shenzhen",
    "Munich", "Wellington", "Buenos Aires", "Hong Kong", "Singapore",
    "Taipei", "Beijing",
}

MODEL_DELAYS = {"ecmwf": 8, "gfs": 6, "icon": 6, "openmeteo": 4, "ukmo": 10, "noaa": 6, "noaa_ndfd": 6}


def run_etl() -> dict:
    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

    zeus = get_connection()
    init_schema(zeus)

    existing = zeus.execute("SELECT COUNT(*) FROM historical_forecasts").fetchone()[0]
    print(f"historical_forecasts has {existing} existing rows. Running incremental sync...")

    rows = rs.execute("""
        SELECT city, target_date, source, forecast_high_f, lead_days,
               forecast_basis_date
        FROM forecasts
        WHERE forecast_high_f IS NOT NULL
    """).fetchall()

    print(f"Source rows: {len(rows):,}")

    imported = 0
    rejected = 0
    batch = []

    for r in rows:
        city = r["city"]
        source_raw = r["source"]
        source = SOURCE_MAP.get(source_raw, source_raw)
        forecast = r["forecast_high_f"]
        unit = "C" if city in CELSIUS_CITIES else "F"
        lead_days = r["lead_days"]

        # Validation: temperature sanity
        if unit == "C" and (forecast > 55 or forecast < -40):
            rejected += 1
            continue
        if unit == "F" and (forecast > 135 or forecast < -50):
            rejected += 1
            continue

        # Reconstruct available_at
        basis = r["forecast_basis_date"] or r["target_date"]
        delay_h = MODEL_DELAYS.get(source, 6)
        available_at = f"{basis}T{delay_h:02d}:00:00Z"

        batch.append((
            city, r["target_date"], source, forecast, unit, lead_days, available_at
        ))

        if len(batch) >= 10000:
            zeus.executemany("""
                INSERT OR IGNORE INTO historical_forecasts
                (city, target_date, source, forecast_high, temp_unit, lead_days, available_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, batch)
            imported += len(batch)
            batch = []

    if batch:
        zeus.executemany("""
            INSERT OR IGNORE INTO historical_forecasts
            (city, target_date, source, forecast_high, temp_unit, lead_days, available_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, batch)
        imported += len(batch)

    zeus.commit()

    final = zeus.execute("SELECT COUNT(*) FROM historical_forecasts").fetchone()[0]
    print(f"Attempted: {imported:,}, Rejected: {rejected:,}")
    print(f"Final row count: {final:,}")

    # Now compute model_skill
    _compute_model_skill(zeus)

    rs.close()
    zeus.close()

    return {"imported": final, "rejected": rejected}


def _compute_model_skill(conn):
    """Compute per-city×season×source MAE and bias from historical_forecasts JOIN settlements."""
    conn.execute("DELETE FROM model_skill")  # Recompute from scratch

    rows = conn.execute("""
        SELECT f.city,
               f.source,
               AVG(ABS(f.forecast_high - s.settlement_value)) as mae,
               AVG(f.forecast_high - s.settlement_value) as bias,
               COUNT(*) as n
        FROM historical_forecasts f
        JOIN settlements s ON f.city = s.city AND f.target_date = s.target_date
        WHERE f.lead_days = 1
          AND s.settlement_value IS NOT NULL
        GROUP BY f.city, f.source
        HAVING COUNT(*) >= 10
    """).fetchall()

    # We need season — do a second pass with Python grouping
    from collections import defaultdict

    detail_rows = conn.execute("""
        SELECT f.city, f.target_date, f.source,
               f.forecast_high, s.settlement_value
        FROM historical_forecasts f
        JOIN settlements s ON f.city = s.city AND f.target_date = s.target_date
        WHERE f.lead_days = 1
          AND s.settlement_value IS NOT NULL
    """).fetchall()

    grouped = defaultdict(list)
    for r in detail_rows:
        season = season_from_date(r["target_date"])
        key = (r["city"], season, r["source"])
        error = r["forecast_high"] - r["settlement_value"]
        grouped[key].append(error)

    import numpy as np
    stored = 0
    for (city, season, source), errors in sorted(grouped.items()):
        if len(errors) < 10:
            continue
        arr = np.array(errors)
        conn.execute("""
            INSERT OR REPLACE INTO model_skill
            (city, season, source, mae, bias, n_samples)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (city, season, source, float(np.abs(arr).mean()),
              float(arr.mean()), len(errors)))
        stored += 1

    conn.commit()
    print(f"\nmodel_skill entries: {stored}")


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
