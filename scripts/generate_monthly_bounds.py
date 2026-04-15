#!/usr/bin/env python3
"""Offline generator for config/city_monthly_bounds.json.

Reads production TIGGE ensemble_snapshots, computes per-city per-month
p01/p99 percentiles from all member values, writes config/city_monthly_bounds.json.

Design decisions:
- TIGGE members_json values are already in the city's settlement_unit (F for US,
  C for all others). Confirmed by inspection: Atlanta Jan 2024 members 35-41°F,
  London Nov 2023 members 1-9°C. No Kelvin conversion needed.
- sample_count < 30 → null entry; guard falls back to lat-band heuristic.
- Entries with null sample_count are also null.
- Output sorted by city name, then month number.

Usage:
    /path/to/venv/bin/python scripts/generate_monthly_bounds.py

Part of K1-B packet. See .omc/plans/k1-freeze.md sections 5, 6.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
PRODUCTION_DB = Path("/Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db")
OUTPUT_PATH = PROJECT_ROOT / "config" / "city_monthly_bounds.json"

# Insert project root on sys.path so src.config is importable even when run
# directly from any CWD.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SAMPLE_COUNT = 30


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not PRODUCTION_DB.exists():
        print(f"ERROR: Production DB not found at {PRODUCTION_DB}", file=sys.stderr)
        sys.exit(1)

    print(f"Connecting to {PRODUCTION_DB} (read-only)")
    conn = sqlite3.connect(f"file:{PRODUCTION_DB}?mode=ro", uri=True)

    # --- Step 1: fetch all TIGGE rows ---
    print("Querying ensemble_snapshots for model_version='ecmwf_tigge' ...")
    rows = conn.execute(
        "SELECT city, target_date, members_json "
        "FROM ensemble_snapshots "
        "WHERE model_version = 'ecmwf_tigge'"
    ).fetchall()
    conn.close()

    print(f"  Fetched {len(rows):,} TIGGE snapshots")

    # --- Step 2: accumulate member values per (city, month) ---
    # Structure: {city: {month_int: {"values": [float,...], "dates": [str,...]}}}
    accumulator: dict[str, dict[int, dict]] = defaultdict(lambda: {m: {"values": [], "dates": []} for m in range(1, 13)})

    parse_errors = 0
    for city, target_date_str, members_json_str in rows:
        try:
            month = int(target_date_str[5:7])  # "YYYY-MM-DD"[5:7]
        except (ValueError, TypeError):
            parse_errors += 1
            continue
        try:
            members = json.loads(members_json_str)
        except (json.JSONDecodeError, TypeError):
            parse_errors += 1
            continue
        if not isinstance(members, list) or len(members) == 0:
            continue
        cell = accumulator[city][month]
        cell["values"].extend(float(v) for v in members)
        cell["dates"].append(target_date_str)

    if parse_errors:
        print(f"  WARNING: {parse_errors} rows had parse errors (skipped)")

    # --- Step 3: compute p01/p99 per (city, month) ---
    all_cities_in_db = sorted(accumulator.keys())
    print(f"  Cities found in TIGGE data: {len(all_cities_in_db)}")

    # Track expected cities from config
    config_cities = set(cities_by_name.keys())
    tigge_cities = set(all_cities_in_db)
    missing_from_config = tigge_cities - config_cities
    missing_from_tigge = config_cities - tigge_cities
    if missing_from_config:
        print(f"  WARNING: Cities in TIGGE but not in config: {sorted(missing_from_config)}")
    if missing_from_tigge:
        print(f"  WARNING: Cities in config but not in TIGGE: {sorted(missing_from_tigge)}")

    # We iterate over all cities in config to ensure 46 city keys in output
    output_cities: dict[str, dict] = {}
    total_entries = 0
    below_threshold = 0
    missing_months: dict[str, list[int]] = {}

    for city in sorted(config_cities):
        city_obj = cities_by_name[city]
        unit = city_obj.settlement_unit  # "F" or "C"
        city_data = accumulator.get(city, {})
        months_dict: dict[str, object] = {}

        for month in range(1, 13):
            cell = city_data.get(month, {"values": [], "dates": []})
            values = cell.get("values", [])
            dates = cell.get("dates", [])
            sample_count = len(values)

            if sample_count < MIN_SAMPLE_COUNT:
                months_dict[str(month)] = None
                below_threshold += 1
                if city not in missing_months:
                    missing_months[city] = []
                missing_months[city].append(month)
                continue

            arr = np.array(values, dtype=float)
            p01 = float(np.percentile(arr, 1))
            p99 = float(np.percentile(arr, 99))

            date_from = min(dates)
            date_to = max(dates)

            months_dict[str(month)] = {
                "p01": round(p01, 2),
                "p99": round(p99, 2),
                "unit": unit,
                "sample_count": sample_count,
                "tigge_date_range": {"from": date_from, "to": date_to},
            }
            total_entries += 1

        output_cities[city] = months_dict

    # --- Step 4: write JSON ---
    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "ecmwf_tigge ensemble_snapshots.members_json",
        "script": "scripts/generate_monthly_bounds.py",
        "cities": output_cities,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, sort_keys=True)

    # --- Step 5: summary ---
    print()
    print("=" * 60)
    print("SUMMARY")
    print(f"  Output: {OUTPUT_PATH}")
    print(f"  Total cities in output: {len(output_cities)}")
    print(f"  Non-null entries (sample_count >= {MIN_SAMPLE_COUNT}): {total_entries}")
    print(f"  Null entries (sample_count < {MIN_SAMPLE_COUNT}): {below_threshold}")
    print(f"  Total possible entries (46 x 12): {46 * 12}")
    if missing_months:
        print(f"  Cities with missing months ({len(missing_months)}):")
        for c, months in sorted(missing_months.items()):
            print(f"    {c}: months {months}")
    else:
        print("  No cities with missing months.")

    # Anomaly check: any p99 > 130F or < -60F; any p01 < -60F or > 60F
    anomalies = []
    for city, months_data in output_cities.items():
        city_unit = cities_by_name[city].settlement_unit if city in cities_by_name else "?"
        for month_str, entry in months_data.items():
            if entry is None:
                continue
            p01 = entry["p01"]
            p99 = entry["p99"]
            # Convert to F for anomaly check
            if entry["unit"] == "C":
                p01_f = p01 * 9 / 5 + 32
                p99_f = p99 * 9 / 5 + 32
            else:
                p01_f = p01
                p99_f = p99
            if p99_f > 130 or p01_f < -60:
                anomalies.append(f"  {city} month={month_str}: p01={p01}{city_unit} p99={p99}{city_unit}")
    if anomalies:
        print(f"  Anomalies (p99>130F or p01<-60F):")
        for a in anomalies:
            print(a)
    else:
        print("  No anomalies detected.")
    print("=" * 60)


if __name__ == "__main__":
    main()
