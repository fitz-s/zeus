#!/usr/bin/env python3
"""Materialize forecast error profiles from forecast_skill."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.signal.forecast_error_distribution import (
    build_forecast_error_profiles,
    write_forecast_error_profiles,
)
from src.state.db import get_shared_connection, init_schema


def run_etl(*, dry_run: bool = False) -> dict:
    conn = get_shared_connection()
    init_schema(conn)
    before_rows = conn.execute("SELECT COUNT(*) FROM forecast_error_profile").fetchone()[0]
    before_cities = conn.execute("SELECT COUNT(DISTINCT city) FROM forecast_error_profile").fetchone()[0]
    profiles = build_forecast_error_profiles(conn)
    written = 0
    if not dry_run:
        written = write_forecast_error_profiles(
            conn,
            profiles,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        conn.commit()
    after_rows = conn.execute("SELECT COUNT(*) FROM forecast_error_profile").fetchone()[0]
    after_cities = conn.execute("SELECT COUNT(DISTINCT city) FROM forecast_error_profile").fetchone()[0]
    conn.close()
    return {
        "dry_run": dry_run,
        "profiles_built": len(profiles),
        "profiles_written": written,
        "forecast_error_profile_before": int(before_rows),
        "forecast_error_profile_after": int(after_rows),
        "forecast_error_profile_added": 0 if dry_run else int(after_rows - before_rows),
        "forecast_error_profile_cities_before": int(before_cities),
        "forecast_error_profile_cities_after": int(after_cities),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_etl(dry_run=args.dry_run), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
