#!/usr/bin/env python3
# Lifecycle: created=2026-03-18; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Materialize forecast_skill + model_bias from local forecasts table;
#          joins forecasts × settlements on (city, target_date) + HIGH metric.
# Reuse: H3 (2026-04-24) hardened the JOIN to pin s.temperature_metric='high'
#        because forecasts stores forecast_high without a metric column; LOW
#        settlements would otherwise spuriously double-match. See packet
#        docs/operations/task_2026-04-24_midstream_tier2_adversarial_followups/.
"""Materialize forecast_skill/model_bias from the local forecasts table.

This fills the gap left by the older ladder backfill, which only covered the
original city set. It is local-only: no external downloads.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import lat_for_city, season_from_date
from src.config import cities_by_name
from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.state.db import get_world_connection, init_schema

SOURCE_MAP = {
    "ecmwf_previous_runs": "ecmwf",
    "gfs_previous_runs": "gfs",
    "icon_previous_runs": "icon",
    "openmeteo_previous_runs": "openmeteo",
    "ukmo_previous_runs": "ukmo",
    "noaa_forecast_archive": "noaa",
    "noaa_ndfd_historical_forecast": "noaa_ndfd",
}

MODEL_DELAYS = {
    "ecmwf": 8,
    "gfs": 6,
    "icon": 6,
    "openmeteo": 4,
    "ukmo": 10,
    "noaa": 6,
    "noaa_ndfd": 6,
}


def _source(raw: str) -> str:
    return SOURCE_MAP.get(raw, raw)


def _available_at(row: sqlite3.Row, source: str) -> str:
    issue_time = row["forecast_issue_time"]
    if issue_time:
        return str(issue_time)
    basis = row["forecast_basis_date"] or row["target_date"]
    delay_h = MODEL_DELAYS.get(source, 6)
    return f"{basis}T{delay_h:02d}:00:00Z"


def _valid_temp(value: float, unit: str) -> bool:
    if unit == "C":
        return -50.0 <= value <= 60.0
    return -80.0 <= value <= 150.0


def _recompute_model_bias(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM model_bias")
    rows = conn.execute(
        """
        SELECT city, season, source,
               AVG(error) AS bias,
               AVG(ABS(error)) AS mae,
               COUNT(*) AS n
        FROM forecast_skill
        GROUP BY city, season, source
        HAVING COUNT(*) >= 5
        """
    ).fetchall()
    conn.executemany(
        """
        INSERT OR REPLACE INTO model_bias
        (city, season, source, bias, mae, n_samples, discount_factor)
        VALUES (?, ?, ?, ?, ?, ?, 0.7)
        """,
        [
            (
                row["city"],
                row["season"],
                row["source"],
                float(row["bias"]),
                float(row["mae"]),
                int(row["n"]),
            )
            for row in rows
        ],
    )
    return len(rows)


def run_etl(*, dry_run: bool = False) -> dict:
    conn = get_world_connection()
    init_schema(conn)
    before = conn.execute("SELECT COUNT(*) FROM forecast_skill").fetchone()[0]
    # H3 (2026-04-24): pin s.temperature_metric='high' because forecasts
    # stores both forecast_high and forecast_low without a metric tag; this
    # path selects forecast_high only, so the JOIN must restrict settlements
    # to HIGH rows or a future LOW settlement would spuriously match and
    # corrupt the forecast_skill row.
    rows = conn.execute(
        """
        SELECT
            f.city, f.target_date, f.source, f.forecast_basis_date,
            f.forecast_issue_time, f.lead_days, f.forecast_high, f.temp_unit,
            s.settlement_value
        FROM forecasts f
        JOIN settlements s
          ON s.city = f.city
         AND s.target_date = f.target_date
         AND s.temperature_metric = 'high'
        WHERE f.forecast_high IS NOT NULL
          AND f.lead_days IS NOT NULL
          AND s.settlement_value IS NOT NULL
        ORDER BY f.city, f.target_date, f.source, f.lead_days
        """
    ).fetchall()

    batch = []
    rejected = Counter()
    for row in rows:
        city_name = str(row["city"])
        city = cities_by_name.get(city_name)
        if city is None:
            rejected["unknown_city"] += 1
            continue
        source = _source(str(row["source"]))
        unit = str(row["temp_unit"] or city.settlement_unit)
        forecast = float(row["forecast_high"])
        actual = round_wmo_half_up_value(float(row["settlement_value"]))
        if unit != city.settlement_unit:
            rejected["unit_mismatch"] += 1
            continue
        if not _valid_temp(forecast, unit):
            rejected["forecast_out_of_range"] += 1
            continue
        error = forecast - actual
        if abs(error) > 80:
            rejected["error_out_of_range"] += 1
            continue
        batch.append(
            (
                city_name,
                row["target_date"],
                source,
                int(row["lead_days"]),
                forecast,
                actual,
                error,
                unit,
                season_from_date(str(row["target_date"]), lat=lat_for_city(city_name)),
                _available_at(row, source),
            )
        )

    if not dry_run and batch:
        conn.executemany(
            """
            INSERT OR IGNORE INTO forecast_skill
            (city, target_date, source, lead_days, forecast_temp,
             actual_temp, error, temp_unit, season, available_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        model_bias_rows = _recompute_model_bias(conn)
        conn.commit()
    else:
        model_bias_rows = conn.execute("SELECT COUNT(*) FROM model_bias").fetchone()[0]

    after = conn.execute("SELECT COUNT(*) FROM forecast_skill").fetchone()[0]
    city_count = conn.execute("SELECT COUNT(DISTINCT city) FROM forecast_skill").fetchone()[0]
    bias_city_count = conn.execute("SELECT COUNT(DISTINCT city) FROM model_bias").fetchone()[0]
    conn.close()
    return {
        "dry_run": dry_run,
        "source_rows": len(rows),
        "candidate_rows": len(batch),
        "forecast_skill_before": int(before),
        "forecast_skill_after": int(after),
        "forecast_skill_added": 0 if dry_run else int(after - before),
        "forecast_skill_cities": int(city_count),
        "model_bias_rows": int(model_bias_rows),
        "model_bias_cities": int(bias_city_count),
        "rejected": dict(sorted(rejected.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_etl(dry_run=args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
