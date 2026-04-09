"""Generate calibration pairs from ENS snapshots + settlements.

For each settlement that has a matching ensemble_snapshot with P_raw:
1. Parse bin structure from market_events (or from P_raw vector indices)
2. Match winning_bin to determine outcome per bin
3. Create 1 pair per bin: (p_raw, lead_days, outcome=0|1, season, cluster)

This is the bridge between Phase A (backfill) and Phase B (Platt fitting).
"""

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.calibration.manager import season_from_date
from src.calibration.store import add_calibration_pair, get_pairs_count
from src.config import cities_by_name
from src.data.market_scanner import _parse_temp_range
from src.state.db import get_shared_connection as get_connection, init_schema


def generate_pairs() -> dict:
    """Generate calibration pairs from all matched snapshot-settlement pairs.

    Returns: {"total_pairs": int, "settlements_processed": int, "by_bucket": dict}
    """
    conn = get_connection()
    init_schema(conn)

    # Find settlements with matching ENS snapshots that have P_raw
    matched = conn.execute("""
        SELECT s.city, s.target_date, s.winning_bin, s.settlement_value,
               e.p_raw_json, e.lead_hours, e.available_at, e.snapshot_id
        FROM settlements s
        INNER JOIN ensemble_snapshots e
            ON s.city = e.city AND s.target_date = e.target_date
        WHERE e.p_raw_json IS NOT NULL
          AND s.winning_bin IS NOT NULL
        ORDER BY s.target_date
    """).fetchall()

    print(f"Found {len(matched)} settlement-snapshot pairs with P_raw\n")

    # Check how many calibration pairs already exist
    existing = conn.execute("SELECT COUNT(*) FROM calibration_pairs").fetchone()[0]
    print(f"Existing calibration pairs: {existing}")

    if existing > 0:
        print("Skipping — pairs already generated. Delete calibration_pairs to regenerate.")
        conn.close()
        return {"total_pairs": existing, "settlements_processed": 0, "by_bucket": {}}

    total_pairs = 0
    settlements_processed = 0
    by_bucket = defaultdict(int)
    skipped = 0

    for row in matched:
        city_name = row["city"]
        target_date = row["target_date"]
        winning_bin = row["winning_bin"]
        p_raw_json = row["p_raw_json"]
        lead_hours = row["lead_hours"]
        available_at = row["available_at"]

        city = cities_by_name.get(city_name)
        if city is None:
            skipped += 1
            continue

        # Parse P_raw vector
        try:
            p_raw_vec = json.loads(p_raw_json)
        except (json.JSONDecodeError, TypeError):
            skipped += 1
            continue

        # Get bin structure from market_events
        bins = _get_bin_labels(conn, city_name, target_date)
        if not bins or len(bins) != len(p_raw_vec):
            # P_raw length mismatch with bin count — skip
            skipped += 1
            continue

        season = season_from_date(target_date, lat=city.lat)
        lead_days = lead_hours / 24.0
        bucket_key = f"{city.cluster}_{season}"

        # Determine which bin won
        # winning_bin format from settlements: "39-40", "-999-32", "51-999" etc.
        # We match by index since P_raw and bins are aligned
        winning_idx = _match_winning_bin(winning_bin, bins)

        for i, (label, p_raw) in enumerate(zip(bins, p_raw_vec)):
            outcome = 1 if i == winning_idx else 0

            add_calibration_pair(
                conn,
                city=city_name,
                target_date=target_date,
                range_label=label,
                p_raw=float(p_raw),
                outcome=outcome,
                lead_days=lead_days,
                season=season,
                cluster=city.cluster,
                forecast_available_at=available_at,
                settlement_value=row["settlement_value"],
            )
            total_pairs += 1

        by_bucket[bucket_key] += len(bins)
        settlements_processed += 1

    conn.commit()
    conn.close()

    print(f"\nGenerated {total_pairs} calibration pairs from {settlements_processed} settlements")
    print(f"Skipped: {skipped}")
    print(f"\nPairs by bucket:")
    for bk, count in sorted(by_bucket.items()):
        print(f"  {bk}: {count}")

    return {
        "total_pairs": total_pairs,
        "settlements_processed": settlements_processed,
        "by_bucket": dict(by_bucket),
    }


def _get_bin_labels(conn, city: str, target_date: str) -> list[str]:
    """Get ordered bin labels from market_events."""
    rows = conn.execute("""
        SELECT range_label FROM market_events
        WHERE city = ? AND target_date = ?
        ORDER BY range_label
    """, (city, target_date)).fetchall()

    if not rows:
        return []

    # Sort numerically by parsed boundary, not lexicographically
    labeled = []
    for r in rows:
        label = r["range_label"]
        low, high = _parse_temp_range(label)
        sort_key = low if low is not None else (high - 1000 if high is not None else -2000)
        labeled.append((sort_key, label))

    labeled.sort(key=lambda x: x[0])
    return [label for _, label in labeled]


def _match_winning_bin(winning_bin: str, bin_labels: list[str]) -> int:
    """Match settlement winning_bin to a bin index.

    winning_bin format: "39-40", "-999-32", "51-999", "4-5", etc.
    bin_labels: parsed and sorted labels from market_events

    Returns: index of the winning bin, or -1 if no match.
    """
    # Parse winning bin boundaries
    parts = winning_bin.split("-")
    if len(parts) == 2:
        try:
            w_low = float(parts[0])
            w_high = float(parts[1])
        except ValueError:
            return -1
    elif len(parts) == 3 and parts[0] == "":
        # Negative number: "-999-32" → parts = ["", "999", "32"]
        try:
            w_low = -float(parts[1])
            w_high = float(parts[2])
        except ValueError:
            return -1
    else:
        return -1

    # Match against bin labels by comparing parsed boundaries
    for i, label in enumerate(bin_labels):
        low, high = _parse_temp_range(label)

        # Shoulder low: winning_bin "-999-X" matches label "X°F or below"
        if w_low <= -998 and low is None and high is not None:
            if abs(w_high - high) < 1.0:
                return i

        # Shoulder high: winning_bin "X-999" matches label "X°F or higher"
        elif w_high >= 998 and high is None and low is not None:
            if abs(w_low - low) < 1.0:
                return i

        # Interior bin: winning_bin "39-40" matches label "39-40°F" or "39–40 °F"
        elif low is not None and high is not None:
            if abs(w_low - low) < 1.0 and abs(w_high - high) < 1.0:
                return i

        # Point bin (°C): winning_bin "4-5" might match "4°C" (value 4, width 1°C)
        elif low is not None and high is not None and low == high:
            if abs(w_low - low) < 1.0:
                return i

    return -1


if __name__ == "__main__":
    result = generate_pairs()
    print(f"\nDone. Total: {result['total_pairs']} pairs")
