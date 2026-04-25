# Lifecycle: created=2026-03-30; last_reviewed=2026-04-25; last_reused=2026-04-25
# Purpose: Diagnostic report for ECMWF bias behavior against high-track settlement evidence.
# Reuse: Diagnostic only; do not treat output as training authority.
"""ECMWF warm bias investigation.

Questions:
1. How does bias vary by season? (DJF vs MAM vs JJA vs SON)
2. How does it affect P_raw? (simulate corrected vs uncorrected)
3. Should we apply correction now or wait for TIGGE?
"""

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection as get_connection


def investigate():
    conn = get_connection()
    results = {}

    # 1. Seasonal bias breakdown for ECMWF
    print("=== ECMWF BIAS BY SEASON ===")
    print(f"{'City':15s} {'DJF':>8s} {'MAM':>8s} {'JJA':>8s} {'SON':>8s}")
    print("-" * 50)

    seasonal = {}
    for r in conn.execute("""
        SELECT city, season, bias, mae, n_samples
        FROM model_bias
        WHERE source = 'ecmwf'
        ORDER BY city, season
    """):
        key = f"{r['city']}_{r['season']}"
        seasonal[key] = {"bias": r["bias"], "mae": r["mae"], "n": r["n_samples"]}

    cities_seen = set()
    for key, v in sorted(seasonal.items()):
        city = key.rsplit("_", 1)[0]
        cities_seen.add(city)

    for city in sorted(cities_seen):
        row = f"{city:15s}"
        for season in ["DJF", "MAM", "JJA", "SON"]:
            k = f"{city}_{season}"
            if k in seasonal:
                row += f" {seasonal[k]['bias']:+7.2f}"
            else:
                row += "     n/a"
        print(row)

    results["seasonal_bias"] = seasonal

    # 2. Cross-model comparison: is ECMWF consistently worse?
    print("\n=== MODEL RANKING BY MAE (all seasons pooled) ===")
    for r in conn.execute("""
        SELECT source, AVG(mae) as avg_mae, AVG(bias) as avg_bias,
               SUM(n_samples) as total_n
        FROM model_bias
        GROUP BY source
        ORDER BY avg_mae ASC
    """):
        print(f"  {r['source']:12s} MAE={r['avg_mae']:.2f} bias={r['avg_bias']:+.2f} n={r['total_n']:,}")

    # 3. Is warm bias consistent across lead days?
    print("\n=== ECMWF BIAS BY LEAD DAY ===")
    for r in conn.execute("""
        SELECT lead_days, AVG(error) as avg_bias, AVG(ABS(error)) as mae, COUNT(*) as n
        FROM forecast_skill
        WHERE source = 'ecmwf'
        GROUP BY lead_days
        ORDER BY lead_days
    """):
        print(f"  Lead {r['lead_days']:2d}: bias={r['avg_bias']:+.2f} MAE={r['mae']:.2f} n={r['n']:,}")

    # 4. Bias correction impact simulation
    print("\n=== BIAS CORRECTION SIMULATION ===")
    # For settlements with ENS snapshots, compare P_raw accuracy
    # with and without bias correction
    settlements_with_ens = conn.execute("""
        SELECT s.city, s.target_date, s.winning_bin, s.settlement_value,
               e.members_json, e.p_raw_json, e.spread
        FROM settlements s
        INNER JOIN ensemble_snapshots e
            ON s.city = e.city
           AND s.target_date = e.target_date
           AND COALESCE(e.temperature_metric, 'high') = 'high'
        WHERE e.members_json IS NOT NULL AND s.winning_bin IS NOT NULL
          AND s.temperature_metric = 'high'
        LIMIT 20
    """).fetchall()

    if not settlements_with_ens:
        print("  No settlements with ENS data for simulation")
        results["simulation"] = "no_data"
    else:
        correct_raw = 0
        correct_corrected = 0
        total = 0

        for r in settlements_with_ens:
            members = np.array(json.loads(r["members_json"]))
            winning = r["winning_bin"]
            city = r["city"]

            # Get ECMWF bias for this city (average across seasons)
            bias_row = conn.execute("""
                SELECT AVG(bias) as avg_bias FROM model_bias
                WHERE city = ? AND source = 'ecmwf'
            """, (city,)).fetchone()

            if bias_row is None or bias_row["avg_bias"] is None:
                continue

            ecmwf_bias = bias_row["avg_bias"]
            discount = 0.7  # Spec §3.6: ensemble mean bias ≈ 70% of deterministic

            # Raw: round member maxes
            raw_ints = np.round(members).astype(int)
            # Corrected: subtract discounted bias, then round
            corrected = members - ecmwf_bias * discount
            corrected_ints = np.round(corrected).astype(int)

            # Parse winning bin
            parts = winning.split("-")
            try:
                if len(parts) == 2:
                    w_low, w_high = float(parts[0]), float(parts[1])
                elif len(parts) == 3 and parts[0] == "":
                    w_low, w_high = -float(parts[1]), float(parts[2])
                else:
                    continue
            except ValueError:
                continue

            # Count members in winning bin
            if w_low <= -998:
                raw_in = np.sum(raw_ints <= w_high)
                cor_in = np.sum(corrected_ints <= w_high)
            elif w_high >= 998:
                raw_in = np.sum(raw_ints >= w_low)
                cor_in = np.sum(corrected_ints >= w_low)
            else:
                raw_in = np.sum((raw_ints >= w_low) & (raw_ints <= w_high))
                cor_in = np.sum((corrected_ints >= w_low) & (corrected_ints <= w_high))

            # "Correct" = more members in winning bin (higher P_raw for winner)
            if cor_in > raw_in:
                correct_corrected += 1
            elif raw_in > cor_in:
                correct_raw += 1
            total += 1

        print(f"  Settlements tested: {total}")
        print(f"  Bias correction improved P_raw: {correct_corrected}/{total}")
        print(f"  Raw P_raw was better: {correct_raw}/{total}")
        print(f"  Tied: {total - correct_corrected - correct_raw}/{total}")

        results["simulation"] = {
            "total": total,
            "corrected_better": correct_corrected,
            "raw_better": correct_raw,
            "tied": total - correct_corrected - correct_raw,
        }

        if correct_corrected > correct_raw and total >= 10:
            print("\n  RECOMMENDATION: Apply bias correction to EnsembleSignal")
            results["recommendation"] = "APPLY_NOW"
        elif total < 10:
            print("\n  RECOMMENDATION: Wait for more data (TIGGE)")
            results["recommendation"] = "WAIT_FOR_TIGGE"
        else:
            print("\n  RECOMMENDATION: Bias correction is ambiguous. Wait for TIGGE.")
            results["recommendation"] = "WAIT_FOR_TIGGE"

    conn.close()

    output_path = PROJECT_ROOT / "state" / "ecmwf_bias_investigation.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved to {output_path}")
    return results


if __name__ == "__main__":
    investigate()
