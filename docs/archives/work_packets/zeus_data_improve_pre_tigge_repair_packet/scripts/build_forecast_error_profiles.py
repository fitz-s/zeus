#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



def season_from_date(target_date: str) -> str:
    month = int(target_date[5:7])
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--min-samples", type=int, default=10)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    now = datetime.now(timezone.utc).isoformat()
    try:
        grouped: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
        for city, target_date, source, lead_days, forecast_high, settlement_value in conn.execute(
            """
            SELECT h.city, h.target_date, h.source, COALESCE(h.lead_days, 0), h.forecast_high, s.settlement_value
            FROM historical_forecasts h
            JOIN settlements s
              ON s.city = h.city
             AND s.target_date = h.target_date
            WHERE h.forecast_high IS NOT NULL
              AND s.settlement_value IS NOT NULL
            """
        ):
            key = (city, season_from_date(target_date), source, int(lead_days))
            grouped[key].append(float(forecast_high) - float(settlement_value))

        inserted = 0
        for (city, season, source, lead_days), errors in grouped.items():
            if len(errors) < args.min_samples:
                continue
            n = len(errors)
            bias = sum(errors) / n
            mae = sum(abs(e) for e in errors) / n
            mse = sum(e * e for e in errors) / n
            var = sum((e - bias) ** 2 for e in errors) / n
            std = math.sqrt(var)
            conn.execute(
                """
                INSERT OR REPLACE INTO forecast_error_profile (
                    profile_id, city, season, source, lead_days,
                    n_samples, bias, mae, mse, error_variance, error_stddev, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), city, season, source, lead_days,
                    n, bias, mae, mse, var, std, now,
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    print({"profiles_inserted": inserted, "min_samples": args.min_samples})


if __name__ == "__main__":
    main()
