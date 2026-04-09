"""ETL: Ladder backfill → forecast_skill + model_bias tables.

Source: rainstorm.db:settlement_forecast_ladder_backfill (53,600 rows)
Target: zeus.db:forecast_skill + model_bias

Validates:
- London/Paris → unit must be 'C'. Values > 50 for London winter → REJECT.
- ABS(error) > 30 → REJECT (likely unit contamination)
- Reconstructs available_at from forecast_basis_date + source model delay
"""

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date, lat_for_city
from src.state.db import get_shared_connection as get_connection, init_schema


RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"

# Source family mapping from raw source names
SOURCE_MAP = {
    "ecmwf_previous_runs": "ecmwf",
    "gfs_previous_runs": "gfs",
    "icon_previous_runs": "icon",
    "openmeteo_previous_runs": "openmeteo",
    "ukmo_previous_runs": "ukmo",
}

# European cities where unit must be °C
CELSIUS_CITIES = {"London", "Paris", "Seoul", "Tokyo", "Shanghai"}

# Model delay hours (time from model run to data availability)
MODEL_DELAYS = {"ecmwf": 8, "gfs": 6, "icon": 6, "openmeteo": 4, "ukmo": 10}


def run_etl() -> dict:
    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

    zeus = get_connection()
    init_schema(zeus)

    # Check existing data
    existing = zeus.execute("SELECT COUNT(*) FROM forecast_skill").fetchone()[0]
    if existing > 0:
        print(f"forecast_skill already has {existing} rows. Skipping ETL.")
        rs.close()
        zeus.close()
        return {"imported": 0, "rejected": 0, "existing": existing}

    rows = rs.execute("""
        SELECT city, target_date, forecast_source, source_family,
               lead_days, forecast_high_f, forecast_error_f,
               temp_unit, forecast_basis_date, error_basis
        FROM settlement_forecast_ladder_backfill
        WHERE forecast_high_f IS NOT NULL
    """).fetchall()

    print(f"Source rows: {len(rows):,}")

    imported = 0
    rejected = 0
    reject_reasons = defaultdict(int)

    for r in rows:
        city = r["city"]
        unit = r["temp_unit"]
        forecast = r["forecast_high_f"]
        error = r["forecast_error_f"]
        actual = forecast - error  # error = forecast - actual → actual = forecast - error

        # Validation 1: European cities must be °C
        if city in CELSIUS_CITIES and unit != "C":
            reject_reasons["wrong_unit_europe"] += 1
            rejected += 1
            continue

        # Validation 2: Temperature sanity
        if unit == "C" and (forecast > 50 or forecast < -30):
            reject_reasons["temp_out_of_range_C"] += 1
            rejected += 1
            continue
        if unit == "F" and (forecast > 130 or forecast < -40):
            reject_reasons["temp_out_of_range_F"] += 1
            rejected += 1
            continue

        # Validation 3: Error magnitude
        if abs(error) > 30:
            reject_reasons["error_too_large"] += 1
            rejected += 1
            continue

        # Map source
        source = SOURCE_MAP.get(r["forecast_source"], r["source_family"])
        season = season_from_date(r["target_date"], lat=lat_for_city(r["city"]))

        # Reconstruct available_at
        basis = r["forecast_basis_date"] or r["target_date"]
        delay_h = MODEL_DELAYS.get(source, 6)
        available_at = f"{basis}T{delay_h:02d}:00:00Z"

        try:
            zeus.execute("""
                INSERT OR IGNORE INTO forecast_skill
                (city, target_date, source, lead_days, forecast_temp,
                 actual_temp, error, temp_unit, season, available_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                city, r["target_date"], source, r["lead_days"],
                forecast, actual, error, unit, season, available_at,
            ))
            imported += 1
        except sqlite3.Error as e:
            reject_reasons[f"sql_error_{e}"] += 1
            rejected += 1

    zeus.commit()

    # Compute model_bias
    _compute_model_bias(zeus)

    rs.close()
    zeus.close()

    print(f"\nImported: {imported:,}")
    print(f"Rejected: {rejected:,}")
    if reject_reasons:
        print("Reject reasons:")
        for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")

    return {"imported": imported, "rejected": rejected}


def _compute_model_bias(conn):
    """Compute per-city×season×source bias and MAE."""
    conn.execute("DELETE FROM model_bias")  # Recompute from scratch

    rows = conn.execute("""
        SELECT city, season, source,
               AVG(error) as bias,
               AVG(ABS(error)) as mae,
               COUNT(*) as n
        FROM forecast_skill
        GROUP BY city, season, source
        HAVING COUNT(*) >= 5
    """).fetchall()

    for r in rows:
        conn.execute("""
            INSERT OR REPLACE INTO model_bias
            (city, season, source, bias, mae, n_samples, discount_factor)
            VALUES (?, ?, ?, ?, ?, ?, 0.7)
        """, (r["city"], r["season"], r["source"],
              r["bias"], r["mae"], r["n"]))

    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM model_bias").fetchone()[0]
    print(f"\nmodel_bias entries: {n}")


if __name__ == "__main__":
    result = run_etl()
    print(f"\nDone: {result}")
