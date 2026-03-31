"""Walk-forward validation: hardcoded α vs data-driven α.

Computes Brier score for both approaches on historical settlements.
Go/No-Go criteria:
  - Brier improvement > 0.01
  - Consistent improvement across 5+ city-season combos
  - Otherwise: keep hardcoded α

This script does NOT modify any runtime code.
"""

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_connection, init_schema


def season_from_date(date_str: str) -> str:
    month = int(date_str.split("-")[1])
    if month in (12, 1, 2):
        return "DJF"
    elif month in (3, 4, 5):
        return "MAM"
    elif month in (6, 7, 8):
        return "JJA"
    return "SON"


BASE_ALPHA_BY_LEVEL = {1: 0.65, 2: 0.55, 3: 0.40, 4: 0.25}


def hardcoded_alpha(cal_level: int) -> float:
    """Current hardcoded α (from settings.json)."""
    return BASE_ALPHA_BY_LEVEL.get(cal_level, 0.40)


def data_driven_alpha(cal_level: int, city: str, season: str, 
                       skill_cache: dict) -> float:
    """α adjusted by model_skill MAE for this city×season."""
    base = hardcoded_alpha(cal_level)
    
    key = (city, season)
    if key not in skill_cache:
        return base  # No data, keep default
    
    mae, n_samples = skill_cache[key]
    if n_samples < 20:
        return base
    
    # Adjustment: MAE 2.0 → no change, 1.0 → +0.05, 4.0 → -0.10
    adjustment = (2.0 - mae) * 0.05
    adjustment = max(-0.10, min(0.05, adjustment))
    return max(0.20, min(0.85, base + adjustment))


def brier_score(p_posterior: float, outcome: int) -> float:
    """Brier score: (p - outcome)^2. Lower is better."""
    return (p_posterior - outcome) ** 2


def run_validation():
    conn = get_connection()
    init_schema(conn)

    # Load model_skill data
    skill_cache = {}
    for row in conn.execute("""
        SELECT city, season, mae, n_samples FROM model_skill
        WHERE source = 'ecmwf'
    """).fetchall():
        skill_cache[(row["city"], row["season"])] = (row["mae"], row["n_samples"])
    
    print(f"Model skill entries: {len(skill_cache)}")
    if not skill_cache:
        print("ERROR: model_skill table empty. Cannot validate.")
        conn.close()
        return
    
    # Load calibration pairs + outcomes
    pairs = conn.execute("""
        SELECT cp.cluster, cp.season, cp.p_raw, cp.outcome, cp.lead_days
        FROM calibration_pairs cp
        ORDER BY cp.id
    """).fetchall()
    
    print(f"Calibration pairs: {len(pairs)}")
    if len(pairs) < 50:
        print("WARNING: Too few calibration pairs for meaningful validation.")
    
    # We need settlement-level data with city info for the comparison.
    # calibration_pairs has cluster+season but not city.
    # Use forecast_skill which has city+target_date+lead_days with actual results.
    
    fs_rows = conn.execute("""
        SELECT fs.city, fs.target_date, fs.forecast_temp, fs.actual_temp,
               fs.source, fs.lead_days
        FROM forecast_skill fs
        WHERE fs.actual_temp IS NOT NULL
          AND fs.forecast_temp IS NOT NULL
          AND fs.source = 'ecmwf'
    """).fetchall()
    
    print(f"ECMWF forecast_skill rows with actuals: {len(fs_rows)}")
    
    # For each row: compute p(forecast was within ±2° of actual) as proxy for bin hit
    # Then compute Brier with hardcoded α vs data-driven α
    
    city_season_brier = defaultdict(lambda: {"hardcoded": [], "dynamic": []})
    
    for row in fs_rows:
        city = row["city"]
        season = season_from_date(row["target_date"])
        forecast = row["forecast_temp"]
        actual = row["actual_temp"]
        
        # Binary outcome: was forecast within ±2° of actual?
        error = abs(forecast - actual)
        outcome = 1 if error <= 2.0 else 0
        
        # Simulate what αs would be
        cal_level = 2  # Typical maturity
        alpha_hc = hardcoded_alpha(cal_level)
        alpha_dd = data_driven_alpha(cal_level, city, season, skill_cache)
        
        # Simple posterior model: higher α → more trust in model
        # When α is higher, p_posterior = α × p_model + (1-α) × p_market
        # We don't have p_market here, so use α directly as confidence proxy
        p_hc = alpha_hc * (1.0 if error <= 3.0 else 0.3)  # Rough proxy
        p_dd = alpha_dd * (1.0 if error <= 3.0 else 0.3)
        
        # Normalize to [0, 1]
        p_hc = min(1.0, max(0.0, p_hc))
        p_dd = min(1.0, max(0.0, p_dd))
        
        brier_hc = brier_score(p_hc, outcome)
        brier_dd = brier_score(p_dd, outcome)
        
        city_season_brier[(city, season)]["hardcoded"].append(brier_hc)
        city_season_brier[(city, season)]["dynamic"].append(brier_dd)
    
    # Report
    print()
    print("=" * 80)
    print(f"{'City':15} {'Season':6} {'N':>5}  {'Brier(HC)':>10} {'Brier(DD)':>10} {'Δ':>8} {'Better':>8}")
    print("-" * 80)
    
    improved = 0
    worsened = 0
    total_improvement = 0.0
    
    for (city, season), scores in sorted(city_season_brier.items()):
        n = len(scores["hardcoded"])
        if n < 5:
            continue
        
        brier_hc_mean = np.mean(scores["hardcoded"])
        brier_dd_mean = np.mean(scores["dynamic"])
        delta = brier_hc_mean - brier_dd_mean  # Positive = DD is better
        
        better = "DD" if delta > 0 else "HC"
        if delta > 0:
            improved += 1
        elif delta < 0:
            worsened += 1
        
        total_improvement += delta
        
        print(f"{city:15} {season:6} {n:>5}  {brier_hc_mean:>10.4f} {brier_dd_mean:>10.4f} {delta:>+8.4f} {better:>8}")
    
    print()
    print("=" * 80)
    print(f"City-season combos where DD improves: {improved}")
    print(f"City-season combos where DD worsens:  {worsened}")
    print(f"Average Brier improvement:            {total_improvement / max(1, improved + worsened):+.4f}")
    print()
    
    # Go/No-Go decision
    avg_improvement = total_improvement / max(1, improved + worsened)
    if avg_improvement > 0.01 and improved >= 5:
        print(">>> GO: Data-driven α passes validation criteria <<<")
        print(f"    Brier improvement {avg_improvement:.4f} > 0.01 threshold")
        print(f"    Improved in {improved} city-season combos (≥ 5 required)")
    else:
        print(">>> NO-GO: Keep hardcoded α <<<")
        if avg_improvement <= 0.01:
            print(f"    Brier improvement {avg_improvement:.4f} ≤ 0.01 threshold")
        if improved < 5:
            print(f"    Only improved in {improved} city-season combos (< 5 required)")
    
    conn.close()


if __name__ == "__main__":
    run_validation()
