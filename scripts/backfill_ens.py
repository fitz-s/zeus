"""ENS P_raw backfill for historical settlements within 93-day API window.

Open-Meteo past_days max = 93. No historical ensemble endpoint.
For each settlement within the window:
1. Fetch ECMWF 51-member ENS
2. Compute P_raw vector
3. Store to ensemble_snapshots with all 4 mandatory timestamps

CRITICAL: Every day we don't store = calibration pairs we'll never recover.
"""

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import cities_by_name, load_cities
from src.data.ensemble_client import fetch_ensemble, validate_ensemble
from src.signal.ensemble_signal import EnsembleSignal
from src.state.db import get_connection, init_schema
from src.types import Bin


# API rate limiting
CALLS_PER_MINUTE = 50  # Conservative; free tier allows ~600/min
SLEEP_BETWEEN_CALLS = 60.0 / CALLS_PER_MINUTE


def get_settlements_in_window(conn, days_back: int = 93) -> list[dict]:
    """Get settlements within the backfill window."""
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    rows = conn.execute("""
        SELECT DISTINCT s.city, s.target_date, s.winning_bin
        FROM settlements s
        LEFT JOIN ensemble_snapshots e
            ON s.city = e.city AND s.target_date = e.target_date
        WHERE s.target_date >= ?
          AND e.snapshot_id IS NULL
        ORDER BY s.target_date DESC
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def get_bin_structure(conn, city: str, target_date: str) -> list[Bin]:
    """Get bin structure from market_events. Parse labels using market_scanner."""
    from src.data.market_scanner import _parse_temp_range

    rows = conn.execute("""
        SELECT range_label FROM market_events
        WHERE city = ? AND target_date = ?
    """, (city, target_date)).fetchall()

    if not rows:
        return []

    bins = []
    for r in rows:
        low, high = _parse_temp_range(r["range_label"])
        if low is None and high is None:
            continue
        bins.append(Bin(low=low, high=high, label=r["range_label"]))

    return bins


def backfill_one(conn, city_name: str, target_date_str: str) -> bool:
    """Backfill one settlement. Returns True if successful."""
    city = cities_by_name.get(city_name)
    if city is None:
        print(f"  SKIP {city_name} — not in cities.json")
        return False

    target_date = date.fromisoformat(target_date_str)
    days_ago = (date.today() - target_date).days

    if days_ago > 93 or days_ago < 0:
        print(f"  SKIP {city_name} {target_date_str} — {days_ago} days ago, outside 93-day window")
        return False

    # Fetch ECMWF ensemble with past_days to cover the target date
    result = fetch_ensemble(city, forecast_days=days_ago + 2, model="ecmwf_ifs025")

    if result is None or not validate_ensemble(result):
        print(f"  FAIL {city_name} {target_date_str} — ENS fetch failed or < 51 members")
        return False

    # Extract hours for target date
    members_hourly = result["members_hourly"]  # (51, hours)
    n_hours = members_hourly.shape[1]

    # Select hours for the target date
    # The API returns hours from the start of the forecast period
    # For a past_days request, target_date is near the end of the range
    # We approximate by taking a 24-hour slice for the target date
    target_hour_start = max(0, (days_ago - 1) * 24)
    target_hour_end = min(n_hours, target_hour_start + 24)

    if target_hour_end <= target_hour_start:
        print(f"  SKIP {city_name} {target_date_str} — no hours for target date in response")
        return False

    target_slice = members_hourly[:, target_hour_start:target_hour_end]

    try:
        ens = EnsembleSignal(target_slice, city, target_date)
    except ValueError as e:
        print(f"  FAIL {city_name} {target_date_str} — {e}")
        return False

    # Get bin structure for P_raw computation
    bins = get_bin_structure(conn, city_name, target_date_str)

    p_raw_json = None
    if bins:
        np.random.seed(42)  # Reproducible P_raw for backfill
        p_raw = ens.p_raw_vector(bins, n_mc=2000)  # Fewer MC for speed
        p_raw_json = json.dumps(p_raw.tolist())

    # Store with all 4 mandatory timestamps
    now = datetime.now(timezone.utc)
    conn.execute("""
        INSERT OR IGNORE INTO ensemble_snapshots
        (city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal,
         model_version, data_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        city_name, target_date_str,
        result["issue_time"].isoformat(),        # issue_time
        target_date_str + "T12:00:00Z",          # valid_time (midday target)
        result["issue_time"].isoformat(),         # available_at = issue_time for backfill
        result["fetch_time"].isoformat(),         # fetch_time
        float(days_ago * 24),                     # lead_hours
        json.dumps(ens.member_maxes.tolist()),     # members_json
        p_raw_json,
        ens.spread(),
        int(ens.is_bimodal()),
        "ecmwf_ifs025",
        "backfill_v1",
    ))
    conn.commit()

    print(f"  OK {city_name} {target_date_str} — {result['n_members']} members, "
          f"spread={ens.spread():.2f}, bimodal={ens.is_bimodal()}")
    return True


def main():
    conn = get_connection()
    init_schema(conn)

    settlements = get_settlements_in_window(conn)
    print(f"Found {len(settlements)} settlements in 93-day window without ENS data\n")

    if not settlements:
        print("Nothing to backfill.")
        conn.close()
        return

    success = 0
    fail = 0

    for i, s in enumerate(settlements):
        print(f"[{i+1}/{len(settlements)}] {s['city']} {s['target_date']}")
        try:
            if backfill_one(conn, s["city"], s["target_date"]):
                success += 1
            else:
                fail += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            fail += 1

        time.sleep(SLEEP_BETWEEN_CALLS)

    conn.close()
    print(f"\nBackfill complete: {success} success, {fail} failed/skipped")


if __name__ == "__main__":
    main()
