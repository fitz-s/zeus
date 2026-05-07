#!/usr/bin/env python3
"""Seed token_price_log from current Gamma weather markets.

This is a runtime-readiness backfill, not a historical market replay. It records
the latest YES-side Gamma price for every currently discoverable weather bin so
newly onboarded cities are not blank in diagnostic data readiness before evaluator
cycles naturally visit them.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_scanner import find_weather_markets
from src.state.db import get_world_connection, init_schema


def _snapshot_rows(*, min_hours_to_resolution: float = 0.0) -> list[tuple]:
    observed_at = datetime.now(timezone.utc).isoformat()
    rows: list[tuple] = []
    for event in find_weather_markets(min_hours_to_resolution=min_hours_to_resolution):
        city = event["city"]
        city_name = city.name
        target_date = event["target_date"]
        for outcome in event["outcomes"]:
            token_id = outcome.get("token_id")
            price = outcome.get("price")
            if not token_id or price is None:
                continue
            rows.append(
                (
                    str(token_id),
                    city_name,
                    str(target_date),
                    str(outcome.get("title") or ""),
                    float(price),
                    None,
                    None,
                    None,
                    None,
                    observed_at,
                    observed_at,
                )
            )
    return rows


def run_backfill(*, dry_run: bool = False, min_hours_to_resolution: float = 0.0) -> dict:
    rows = _snapshot_rows(min_hours_to_resolution=min_hours_to_resolution)
    cities = sorted({row[1] for row in rows})
    if not dry_run and rows:
        conn = get_world_connection(write_class="bulk")
        init_schema(conn)
        conn.executemany(
            """
            INSERT INTO token_price_log
            (token_id, city, target_date, range_label, price, volume, bid, ask, spread, source_timestamp, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
    return {
        "dry_run": dry_run,
        "rows": len(rows),
        "cities": len(cities),
        "city_names": cities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-hours-to-resolution", type=float, default=0.0)
    args = parser.parse_args()
    print(json.dumps(run_backfill(dry_run=args.dry_run, min_hours_to_resolution=args.min_hours_to_resolution), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
