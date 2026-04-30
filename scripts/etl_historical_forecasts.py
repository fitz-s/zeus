# Lifecycle: created=2025-11-08; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: ETL historical forecasts + derive per-city×season×source MAE/bias
#          into model_skill from historical_forecasts × settlements JOIN.
# Reuse: H3 (2026-04-24) hardened the MAE JOIN to pin
#        s.temperature_metric='high' because historical_forecasts stores
#        forecast_high without a metric column; LOW settlements would
#        spuriously double-match and corrupt MAE/bias statistics.
"""ETL: Historical forecasts from zeus-world.db → historical_forecasts + model_skill.

Source: zeus-world.db:forecasts (migrated from legacy predecessor, 171K+ rows, 5 NWP models)
  - ecmwf_previous_runs: 35,518 (lead 0-7)
  - gfs_previous_runs: 35,518 (lead 0-7)
  - openmeteo_previous_runs: 34,939 (lead 0-7)
  - icon_previous_runs: 30,502 (lead 0-6)
  - ukmo_previous_runs: 30,148 (lead 0-6)
Target: zeus-world.db:historical_forecasts + model_skill

Validates:
- Temperature range per unit (C/F)
- Reconstructs available_at from forecast_basis_date + model delay
- UNIQUE(city, target_date, source, lead_days) — modified from original schema
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.config import cities_by_name


def season_from_date(date_str: str) -> str:
    """Map date string (YYYY-MM-DD) to season code."""
    from src.calibration.manager import season_from_date as _sfd
    return _sfd(date_str)


def season_from_date_with_city(date_str: str, city_name: str) -> str:
    """Hemisphere-aware season code for a city."""
    from src.calibration.manager import season_from_date as _sfd, lat_for_city
    return _sfd(date_str, lat=lat_for_city(city_name))

from src.state.db import get_world_connection as get_connection, init_schema

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

MODEL_DELAYS = {"ecmwf": 8, "gfs": 6, "icon": 6, "openmeteo": 4, "ukmo": 10, "noaa": 6, "noaa_ndfd": 6}


def run_etl() -> dict:
    zeus = get_connection()
    init_schema(zeus)

    existing = zeus.execute("SELECT COUNT(*) FROM historical_forecasts").fetchone()[0]
    print(f"historical_forecasts has {existing} existing rows. Running incremental sync...")

    # F11.5-migrate (2026-04-28): SKILL eligibility filter wired in.
    # availability_provenance IS NULL remains tolerated for non-Open-Meteo
    # legacy rows, but Open-Meteo previous-runs without provenance is too weak
    # for skill ETL once the column exists. Post-F11 backfilled rows must be
    # tier'd SKILL-eligible (FETCH_TIME / RECORDED /
    # DERIVED_FROM_DISSEMINATION); RECONSTRUCTED rows are excluded.
    from src.backtest.training_eligibility import SKILL_ELIGIBLE_SQL
    rows = zeus.execute(f"""
        SELECT city, target_date, source, forecast_high, forecast_low, lead_days,
               forecast_basis_date, forecast_issue_time, temp_unit
        FROM forecasts
        WHERE forecast_high IS NOT NULL
          AND (
            {SKILL_ELIGIBLE_SQL}
            OR (
              availability_provenance IS NULL
              AND source != 'openmeteo_previous_runs'
            )
          )
    """).fetchall()

    print(f"Source rows: {len(rows):,}")

    imported = 0
    rejected = 0
    batch = []

    for r in rows:
        city = r["city"]
        source_raw = r["source"]
        source = SOURCE_MAP.get(source_raw, source_raw)
        forecast = r["forecast_high"]
        city_cfg = cities_by_name.get(city)
        unit = r["temp_unit"] or (city_cfg.settlement_unit if city_cfg else "F")
        lead_days = r["lead_days"]

        # Validation: temperature sanity
        if unit == "C" and (forecast > 55 or forecast < -40):
            rejected += 1
            continue
        if unit == "F" and (forecast > 135 or forecast < -50):
            rejected += 1
            continue

        # Reconstruct available_at
        if r["forecast_issue_time"]:
            available_at = r["forecast_issue_time"]
        else:
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

    zeus.close()

    return {"imported": final, "rejected": rejected}


def _compute_model_skill(conn):
    """Compute per-city×season×source MAE and bias from historical_forecasts JOIN settlements."""
    conn.execute("DELETE FROM model_skill")  # Recompute from scratch

    # We need season — do a second pass with Python grouping
    from collections import defaultdict

    # H3 (2026-04-24): pin s.temperature_metric='high' because historical_
    # forecasts tracks only forecast_high (no temperature_metric column).
    # Without the filter, once LOW settlements exist the JOIN produces
    # 2× spurious rows per (city, target_date) and corrupts MAE/bias.
    detail_rows = conn.execute("""
        SELECT f.city, f.target_date, f.source,
               f.forecast_high, s.settlement_value
        FROM historical_forecasts f
        JOIN settlements s
          ON f.city = s.city
         AND f.target_date = s.target_date
         AND s.temperature_metric = 'high'
        WHERE f.lead_days = 1
          AND s.settlement_value IS NOT NULL
    """).fetchall()

    grouped = defaultdict(list)
    for r in detail_rows:
        season = season_from_date_with_city(r["target_date"], r["city"])
        key = (r["city"], season, r["source"])
        settlement_value = round_wmo_half_up_value(float(r["settlement_value"]))
        error = r["forecast_high"] - settlement_value
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
