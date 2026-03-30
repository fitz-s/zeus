"""WU Settlement Audit: cross-validate WU observations vs Polymarket settlements.

Spec §12.4: For each settlement with a matching WU observation,
check if round(WU high) falls in the winning bin.

Classifications:
- MATCH: round(WU high) falls in winning bin
- OFF_BY_ONE: round(WU high) falls in adjacent bin (bin boundary discretization)
- MISMATCH: round(WU high) is >= 2 bins away from winning bin

If MISMATCH rate > 5%, our edge model may be systematically wrong.
"""

import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.market_scanner import _parse_temp_range


RAINSTORM_DB = Path.home() / ".openclaw/workspace-venus/rainstorm/state/rainstorm.db"


def run_audit() -> dict:
    rs = sqlite3.connect(str(RAINSTORM_DB))
    rs.row_factory = sqlite3.Row

    # Get settlements with WU observed temperatures
    rows = rs.execute("""
        SELECT s.city, s.target_date, s.winning_range, s.actual_temp_f,
               s.inferred_actual_temp_f, s.temp_unit, s.actual_temp_source
        FROM settlements s
        WHERE s.actual_temp_f IS NOT NULL
          AND s.winning_range IS NOT NULL
    """).fetchall()

    print(f"Total settlements with actual temps: {len(rows)}")

    # Get market bin structures for matching
    results = {"MATCH": [], "OFF_BY_ONE": [], "MISMATCH": [], "NO_BINS": []}
    city_stats = defaultdict(lambda: {"match": 0, "off1": 0, "mismatch": 0, "total": 0})

    for r in rows:
        city = r["city"]
        date = r["target_date"]
        winning = r["winning_range"]
        actual = r["actual_temp_f"]
        unit = r["temp_unit"] or "F"

        # Parse winning bin
        w_low, w_high = _parse_winning_bin(winning)
        if w_low is None and w_high is None:
            continue

        # Round actual temp (WU settlement is integer-rounded)
        actual_int = round(actual)

        # Classify
        classification = _classify(actual_int, w_low, w_high)
        results[classification].append({
            "city": city, "date": date, "winning_bin": winning,
            "actual_temp": actual, "actual_int": actual_int,
            "unit": unit,
        })
        city_stats[city]["total"] += 1
        if classification == "MATCH":
            city_stats[city]["match"] += 1
        elif classification == "OFF_BY_ONE":
            city_stats[city]["off1"] += 1
        else:
            city_stats[city]["mismatch"] += 1

    rs.close()

    # Report
    total = sum(len(v) for v in results.values())
    print(f"\n=== WU Settlement Audit ===")
    print(f"Total analyzed: {total}")
    for k in ["MATCH", "OFF_BY_ONE", "MISMATCH", "NO_BINS"]:
        n = len(results[k])
        pct = n / total * 100 if total > 0 else 0
        print(f"  {k}: {n} ({pct:.1f}%)")

    print(f"\n--- Per City ---")
    for city in sorted(city_stats.keys()):
        s = city_stats[city]
        if s["total"] == 0:
            continue
        match_pct = s["match"] / s["total"] * 100
        off1_pct = s["off1"] / s["total"] * 100
        mis_pct = s["mismatch"] / s["total"] * 100
        print(f"  {city:15s}: {s['total']:4d} total, "
              f"match={match_pct:5.1f}%, off1={off1_pct:5.1f}%, mismatch={mis_pct:5.1f}%")

    # OFF_BY_ONE analysis — these are the bin boundary discretization cases
    if results["OFF_BY_ONE"]:
        print(f"\n--- OFF_BY_ONE Details (bin boundary discretization) ---")
        for r in results["OFF_BY_ONE"][:10]:
            print(f"  {r['city']} {r['date']}: actual={r['actual_int']}{r['unit']}, "
                  f"winning_bin={r['winning_bin']}")

    # MISMATCH analysis
    if results["MISMATCH"]:
        print(f"\n--- MISMATCH Details (top 10 worst) ---")
        for r in results["MISMATCH"][:10]:
            print(f"  {r['city']} {r['date']}: actual={r['actual_int']}{r['unit']}, "
                  f"winning_bin={r['winning_bin']}")

    # GO/NO-GO
    mismatch_rate = len(results["MISMATCH"]) / total if total > 0 else 0
    print(f"\nMismatch rate: {mismatch_rate:.1%}")
    if mismatch_rate > 0.05:
        print("WARNING: Mismatch rate > 5% — edge model may be systematically wrong")
    else:
        print("OK: Mismatch rate within acceptable range")

    # Save
    output = {
        "total": total,
        "match": len(results["MATCH"]),
        "off_by_one": len(results["OFF_BY_ONE"]),
        "mismatch": len(results["MISMATCH"]),
        "mismatch_rate": round(mismatch_rate, 4),
        "per_city": dict(city_stats),
        "off_by_one_details": results["OFF_BY_ONE"][:20],
        "mismatch_details": results["MISMATCH"][:20],
    }

    output_path = PROJECT_ROOT / "state" / "wu_settlement_audit.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to {output_path}")

    return output


def _parse_winning_bin(winning: str) -> tuple:
    """Parse winning_bin format: '39-40', '-999-32', '51-999'."""
    parts = winning.replace(" ", "").split("-")

    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None, None
    elif len(parts) == 3 and parts[0] == "":
        try:
            return -float(parts[1]), float(parts[2])
        except ValueError:
            return None, None
    return None, None


def _classify(actual_int: int, w_low: float, w_high: float) -> str:
    """Classify WU actual vs winning bin."""
    # Open-ended bins
    if w_low <= -998:
        if actual_int <= w_high:
            return "MATCH"
        elif actual_int <= w_high + 2:
            return "OFF_BY_ONE"
        return "MISMATCH"

    if w_high >= 998:
        if actual_int >= w_low:
            return "MATCH"
        elif actual_int >= w_low - 2:
            return "OFF_BY_ONE"
        return "MISMATCH"

    # Interior bin
    if w_low <= actual_int <= w_high:
        return "MATCH"
    elif w_low - 2 <= actual_int <= w_high + 2:
        return "OFF_BY_ONE"
    return "MISMATCH"


if __name__ == "__main__":
    run_audit()
