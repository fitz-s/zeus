"""ETL: TIGGE ENS + WU settlement_value → calibration_pairs (direct pairing).

Instead of requiring market_events bins, this uses the actual settlement temperature
from WU (settlement_value) to determine bin outcomes directly.

For each TIGGE ENS snapshot:
1. Synthesize an 11-bin structure centered on the ENS median
2. Compute P_raw for each bin via member counting
3. Use settlement_value to determine which bin the actual temperature fell in
4. Generate calibration pairs with outcome=1 for the winning bin, outcome=0 for the rest

This unlocks calibration pairs for ANY city-date that has:
- TIGGE ENS member JSON (51 members)
- A settlement record with a known settlement_value

Source: 51 source data/raw/tigge_ecmwf_ens/{city}/{date}/members_step_024.json
Target: zeus.db:calibration_pairs + ensemble_snapshots
"""

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair
from src.config import cities_by_name
from src.contracts import SettlementSemantics
from src.state.db import get_shared_connection as get_connection, init_schema


TIGGE_BASE = Path.home() / ".openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens"

# TIGGE dir name → Zeus canonical name
CITY_MAP = {
    "ankara": "Ankara", "atlanta": "Atlanta", "austin": "Austin",
    "beijing": "Beijing", "buenos-aires": "Buenos Aires",
    "chengdu": "Chengdu", "chicago": "Chicago", "chongqing": "Chongqing",
    "dallas": "Dallas", "denver": "Denver",
    "hong-kong": "Hong Kong", "houston": "Houston", "istanbul": "Istanbul",
    "london": "London", "los-angeles": "Los Angeles", "lucknow": "Lucknow",
    "madrid": "Madrid", "mexico-city": "Mexico City", "miami": "Miami",
    "milan": "Milan", "moscow": "Moscow", "munich": "Munich",
    "nyc": "NYC", "paris": "Paris",
    "san-francisco": "San Francisco", "sao-paulo": "Sao Paulo",
    "seattle": "Seattle", "seoul": "Seoul", "shanghai": "Shanghai",
    "shenzhen": "Shenzhen", "singapore": "Singapore",
    "taipei": "Taipei", "tel-aviv": "Tel Aviv", "tokyo": "Tokyo",
    "toronto": "Toronto", "warsaw": "Warsaw",
    "wellington": "Wellington", "wuhan": "Wuhan",
}

# Cities where T+24h at 00Z is a poor proxy for daily max
OVERNIGHT_CITIES = {
    "Ankara", "Beijing", "Buenos Aires", "Chengdu", "Chongqing",
    "Hong Kong", "Istanbul", "London", "Lucknow", "Madrid",
    "Mexico City", "Milan", "Moscow", "Munich", "Paris", "Sao Paulo",
    "Seoul", "Shanghai", "Shenzhen", "Singapore", "Taipei",
    "Tel Aviv", "Tokyo", "Toronto", "Warsaw", "Wellington", "Wuhan",
}


def _synthesize_bins(median_val: float, unit: str) -> list[tuple[str, float | None, float | None]]:
    """Synthesize standard 11-bin structure centered on the ENS median.
    
    US (°F): 2°F wide bins. Europe (°C): 1°C wide bins.
    Returns: list of (label, low, high) tuples.
    """
    width = 1 if unit == "C" else 2
    n_center = 9
    half = n_center // 2
    start = int(round(median_val)) - half * width

    bins = []
    # Shoulder low
    low_bound = start
    label = f"{low_bound}{'°C' if unit == 'C' else '°F'} or below"
    bins.append((label, None, float(low_bound - 1)))

    # Center bins
    for i in range(n_center):
        lo = start + i * width
        hi = lo + width - 1
        if unit == "C":
            label = f"{lo}°C" if width == 1 else f"{lo}-{hi}°C"
        else:
            label = f"{lo}-{hi}°F"
        bins.append((label, float(lo), float(hi)))

    # Shoulder high
    high_bound = start + n_center * width
    label = f"{high_bound}{'°C' if unit == 'C' else '°F'} or higher"
    bins.append((label, float(high_bound), None))

    return bins


def _temp_in_bin(temp: float, low: float | None, high: float | None) -> bool:
    """Check if a temperature falls in a bin.

    Defensive: temp should already be integer-rounded by caller.
    """
    if low is None and high is not None:
        return temp <= high
    elif high is None and low is not None:
        return temp >= low
    elif low is not None and high is not None:
        return low <= temp <= high
    return False


def run_etl(dry_run: bool = False) -> dict:
    conn = get_connection()
    init_schema(conn)

    # Load all settlements with known settlement_value
    # Defensive: round to integer per settlement precision contract
    settlements = {}
    for row in conn.execute("""
        SELECT city, target_date, settlement_value, winning_bin
        FROM settlements
        WHERE settlement_value IS NOT NULL AND settlement_value != ''
    """).fetchall():
        key = (row["city"], row["target_date"])
        settlements[key] = {
            "value": round(float(row["settlement_value"])),
            "winning_bin": row["winning_bin"],
        }

    print(f"Loaded {len(settlements)} settlements with known temperature values")

    pairs_before = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    snapshots_before = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0]

    imported_snapshots = 0
    imported_pairs = 0
    skipped_no_city = 0
    skipped_no_json = 0
    skipped_no_settlement = 0
    skipped_already_exists = 0

    for city_dir in sorted(TIGGE_BASE.iterdir()):
        if not city_dir.is_dir():
            continue

        city_name = CITY_MAP.get(city_dir.name)
        if city_name is None:
            skipped_no_city += 1
            continue

        city = cities_by_name.get(city_name)
        if city is None:
            skipped_no_city += 1
            continue

        for date_dir in sorted(city_dir.iterdir()):
            if not date_dir.is_dir():
                continue

            # Parse date
            dname = date_dir.name
            if len(dname) != 8:
                continue
            target_date = f"{dname[:4]}-{dname[4:6]}-{dname[6:8]}"

            # Check if we have a settlement with real temp for this city-date
            settlement = settlements.get((city_name, target_date))
            if settlement is None:
                skipped_no_settlement += 1
                continue

            # Find step_024 members JSON
            member_file = None
            for f in date_dir.iterdir():
                if "members" in f.name and "step_024" in f.name and f.suffix == ".json":
                    member_file = f
                    break

            if member_file is None:
                skipped_no_json += 1
                continue

            # Check if already imported
            dv = "tigge_direct_cal_v1"
            existing = conn.execute("""
                SELECT COUNT(*) FROM ensemble_snapshots
                WHERE city = ? AND target_date = ? AND data_version = ?
            """, (city_name, target_date, dv)).fetchone()[0]
            if existing > 0:
                skipped_already_exists += 1
                continue

            # Load members
            try:
                with open(member_file) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            members = data.get("members", [])
            if len(members) != 51:
                continue

            values = np.array([m["value_native_unit"] for m in members], dtype=np.float64)
            values_rounded = SettlementSemantics.for_city(city).round_values(values)

            # Quality flag
            quality = "overnight_snapshot" if city_name in OVERNIGHT_CITIES else "near_peak"

            # Store ensemble snapshot
            if not dry_run:
                conn.execute("""
                    INSERT OR IGNORE INTO ensemble_snapshots
                    (city, target_date, issue_time, valid_time, available_at, fetch_time,
                     lead_hours, members_json, spread, is_bimodal, model_version, data_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    city_name, target_date,
                    f"{target_date}T00:00:00Z",
                    f"{target_date}T00:00:00Z",
                    f"{target_date}T08:00:00Z",
                    data.get("generated_at", ""),
                    24.0,
                    json.dumps(values.tolist()),
                    float(np.std(values)),
                    0,
                    "ecmwf_tigge",
                    dv,
                ))
            imported_snapshots += 1

            # Synthesize bins + generate calibration pairs using settlement_value
            settlement_temp = settlement["value"]
            bins = _synthesize_bins(float(np.median(values)), city.settlement_unit)
            season = season_from_date(target_date, lat=city.lat)

            for label, low, high in bins:
                # P_raw: member counting
                if low is None and high is not None:
                    p_raw = float(np.sum(values_rounded <= high)) / 51
                elif high is None and low is not None:
                    p_raw = float(np.sum(values_rounded >= low)) / 51
                elif low is not None and high is not None:
                    p_raw = float(np.sum((values_rounded >= low) & (values_rounded <= high))) / 51
                else:
                    continue

                # Outcome: did the real temperature fall in this bin?
                outcome = 1 if _temp_in_bin(settlement_temp, low, high) else 0

                if not dry_run:
                    try:
                        add_calibration_pair(
                            conn,
                            city=city_name,
                            target_date=target_date,
                            range_label=label,
                            p_raw=p_raw,
                            outcome=outcome,
                            lead_days=1.0,
                            season=season,
                            cluster=city.cluster,
                            forecast_available_at=f"{target_date}T08:00:00Z",
                        )
                        imported_pairs += 1
                    except Exception:
                        pass  # Duplicate

            if imported_snapshots % 50 == 0 and imported_snapshots > 0:
                print(f"  Progress: {imported_snapshots} snapshots, {imported_pairs} pairs...")

    if not dry_run:
        conn.commit()

    pairs_after = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    snapshots_after = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0]
    conn.close()

    report = {
        "snapshots_added": imported_snapshots,
        "pairs_added": imported_pairs,
        "pairs_before": pairs_before,
        "pairs_after": pairs_after,
        "snapshots_before": snapshots_before,
        "snapshots_after": snapshots_after,
        "skipped_no_city": skipped_no_city,
        "skipped_no_json": skipped_no_json,
        "skipped_no_settlement": skipped_no_settlement,
        "skipped_already_exists": skipped_already_exists,
    }

    print(f"\nDirect TIGGE Calibration ETL complete:")
    print(f"  Snapshots: {snapshots_before} → {snapshots_after} (+{imported_snapshots})")
    print(f"  Calibration pairs: {pairs_before} → {pairs_after} (+{imported_pairs})")
    print(f"  Skipped: no JSON={skipped_no_json}, no settlement={skipped_no_settlement}, "
          f"already exists={skipped_already_exists}, no city={skipped_no_city}")

    return report


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("DRY RUN — no changes will be written")
    run_etl(dry_run=dry)
