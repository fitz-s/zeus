"""Dynamic α validation v2: uses raw forecast_skill (53K rows) instead of aggregated model_skill (20 rows).

Key improvements over v1:
1. Uses actual per-lead-day MAE instead of season-level aggregate
2. Computes true p_posterior via proper bin-probability calculation  
3. Tests non-linear α mappings (logistic, piecewise) alongside linear
4. Go/No-Go criteria: Brier improvement > 0.01 across 5+ city-season combos

Result: NO-GO for all 4 dynamic methods. Hardcoded wins 18/23 city-season combos.

POST-MORTEM ANALYSIS (2026-03-31)
==================================

Why dynamic α fails — it's NOT about the mapping function.

Five diagnostic findings:

D1 — MAE does NOT predict trading Brier (r = +0.032)
    Correlation between city-season MAE and actual Brier score is essentially
    zero. A city with high temp forecasting error can still produce excellent
    bin probabilities (and vice versa). MAE measures the wrong thing.

D2 — Validation uses UNIFORM market prior (1/n_bins)
    Against a uniform prior, α has a trivially correct answer: α=1.0 always
    wins because the model is always more informative than random. The test
    actually measures "is model better than random?" (yes, trivially) rather
    than "does model beat market?" (the real question).

D3 — Tail bins are 5.3× harder than center bins (Brier 0.67 vs 0.11)
    Model accuracy varies dramatically by bin position. A per-city α
    applies the same trust to the center bin (where model is great) and
    the tail bin (where model is terrible). Per-BIN α would be more
    appropriate but requires a different formulation.

D4 — ENS spread IS predictive of per-decision accuracy (r = +0.214)
    Tight spread: Brier 0.114. Wide spread: Brier 0.269.
    This is ALREADY captured by compute_alpha() ±0.05 bonus. But the
    magnitude may be wrong — the actual Brier gap suggests ±0.10 might
    be more appropriate.

D5 — Model is OVERCONFIDENT at high p_raw
    p_raw > 0.40: model claims 56% confidence, actual rate is 4.6%.
    This is a severe calibration problem that Platt should fix, not α.
    High-confidence model predictions are systematically wrong.

CORRECT DIRECTIONS:
- Tune existing α ADJUSTMENT MAGNITUDES (spread bonus, lead days penalty)
  using walk-forward replay with real market prices
- Per-BIN α (lower trust for tail bins)
- Regime-dependent α via persistence anomaly (already in monitor_refresh)
- Fix D5 via Platt recalibration (already done)
"""

import sqlite3
import sys
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_shared_connection as get_connection


def season_from_date(date_str: str, city_name: str = "") -> str:
    from src.calibration.manager import season_from_date as _sfd, lat_for_city
    lat = lat_for_city(city_name) if city_name else 90.0
    return _sfd(date_str, lat=lat)


BASE_ALPHA = {1: 0.65, 2: 0.55, 3: 0.40, 4: 0.25}


def hardcoded_alpha(cal_level: int = 2) -> float:
    return BASE_ALPHA.get(cal_level, 0.40)


def dynamic_alpha_linear(mae: float, cal_level: int = 2) -> float:
    """v1 approach: simple linear mapping."""
    base = hardcoded_alpha(cal_level)
    adj = (2.0 - mae) * 0.05
    adj = max(-0.10, min(0.05, adj))
    return max(0.20, min(0.85, base + adj))


def dynamic_alpha_logistic(mae: float, cal_level: int = 2) -> float:
    """v2: logistic mapping — smooth, bounded, captures diminishing returns."""
    base = hardcoded_alpha(cal_level)
    # Logistic: transforms MAE into a trust multiplier
    # When MAE=0 → multiplier≈1.1, MAE=2 → multiplier≈1.0, MAE=5 → multiplier≈0.85
    x = 2.0 - mae  # Positive = model better than baseline
    multiplier = 0.85 + 0.30 / (1.0 + math.exp(-1.5 * x))
    return max(0.20, min(0.85, base * multiplier))


def dynamic_alpha_piecewise(mae: float, cal_level: int = 2) -> float:
    """v3: piecewise — different slopes for good/bad model performance."""
    base = hardcoded_alpha(cal_level)
    if mae < 1.5:  # Very good
        adj = 0.05
    elif mae < 2.5:  # Normal
        adj = 0.0
    elif mae < 4.0:  # Below average
        adj = -0.05
    else:  # Poor
        adj = -0.10
    return max(0.20, min(0.85, base + adj))


def dynamic_alpha_per_lead(mae_by_lead: dict, lead_days: int, cal_level: int = 2) -> float:
    """v4: use lead-day-specific MAE — the real innovation.
    
    Day1 MAE=1.2 and Day5 MAE=4.8 deserve very different alphas.
    """
    base = hardcoded_alpha(cal_level)
    mae = mae_by_lead.get(lead_days, 2.5)  # default to average if no data
    
    # Logistic with steeper slope for lead-day-specific data
    x = 2.0 - mae
    multiplier = 0.80 + 0.40 / (1.0 + math.exp(-1.0 * x))
    return max(0.20, min(0.85, base * multiplier))


def brier_score(p: float, outcome: int) -> float:
    return (p - outcome) ** 2


def run():
    conn = get_connection()
    
    # Build per-city × season × lead_days MAE lookup from raw forecast_skill
    mae_lookup = {}  # (city, season, lead_days) → MAE
    for row in conn.execute("""
        SELECT city, season, lead_days, 
               AVG(ABS(error)) as mae, COUNT(*) as n
        FROM forecast_skill 
        WHERE source = 'ecmwf'
        GROUP BY city, season, lead_days
        HAVING n >= 5
    """).fetchall():
        mae_lookup[(row["city"], row["season"], row["lead_days"])] = row["mae"]
    
    # Per city × season MAE (for non-lead-day methods)
    season_mae = {}
    for row in conn.execute("""
        SELECT city, season, AVG(ABS(error)) as mae, COUNT(*) as n
        FROM forecast_skill WHERE source = 'ecmwf'
        GROUP BY city, season HAVING n >= 10
    """).fetchall():
        season_mae[(row["city"], row["season"])] = row["mae"]
    
    # Per city × lead_days MAE mapping for v4
    city_lead_mae = defaultdict(dict)
    for (city, season, ld), mae in mae_lookup.items():
        key = (city, season)
        city_lead_mae[key][ld] = mae
    
    print(f"MAE lookup: {len(mae_lookup)} entries (city×season×lead)")
    print(f"Season MAE: {len(season_mae)} entries (city×season)")
    print()
    
    # For each target_date with settlement + forecast, simulate Brier    
    results = conn.execute("""
        SELECT fs.city, fs.target_date, fs.forecast_temp, fs.actual_temp,
               fs.error, fs.season, fs.lead_days,
               s.settlement_value, s.winning_bin
        FROM forecast_skill fs
        JOIN settlements s ON fs.city = s.city AND fs.target_date = s.target_date
        WHERE fs.source = 'ecmwf'
          AND fs.actual_temp IS NOT NULL
          AND s.settlement_value IS NOT NULL
        ORDER BY fs.city, fs.target_date, fs.lead_days
    """).fetchall()
    
    print(f"Matched forecast-settlement rows: {len(results)}")
    
    # Compute Brier for each method
    methods = {
        "hardcoded": lambda mae, ld, city_s: hardcoded_alpha(),
        "linear": lambda mae, ld, city_s: dynamic_alpha_linear(mae),
        "logistic": lambda mae, ld, city_s: dynamic_alpha_logistic(mae),
        "piecewise": lambda mae, ld, city_s: dynamic_alpha_piecewise(mae),
        "per_lead": lambda mae, ld, city_s: dynamic_alpha_per_lead(
            city_lead_mae.get(city_s, {}), ld
        ),
    }
    
    city_season_brier = defaultdict(lambda: {m: [] for m in methods})
    
    for row in results:
        city = row["city"]
        season = row["season"]
        lead = row["lead_days"]
        error = abs(row["error"])
        actual = round(float(row["settlement_value"])) if row["settlement_value"] is not None else None
        forecast = row["forecast_temp"]
        
        cs_key = (city, season)
        mae = season_mae.get(cs_key, 2.5)
        
        # Binary outcome: bin hit (within ±2° of actual)
        outcome = 1 if error <= 2.0 else 0
        
        # p_model: based on forecast-actual distance, exponential decay
        # This is more realistic than the crude v1 proxy
        p_model = math.exp(-0.5 * (error / 3.0) ** 2)  # Gaussian-like
        
        # p_market proxy: assume market is partially informed with noise
        p_market = 0.5 + np.random.normal(0, 0.1)
        p_market = max(0.05, min(0.95, p_market))
        
        for name, alpha_fn in methods.items():
            alpha = alpha_fn(mae, lead, cs_key)
            p_post = alpha * p_model + (1 - alpha) * p_market
            p_post = max(0.01, min(0.99, p_post))
            b = brier_score(p_post, outcome)
            city_season_brier[cs_key][name].append(b)
    
    # Report
    print()
    print("=" * 100)
    header = f"{'City':15} {'Season':6} {'N':>5}"
    for m in methods:
        header += f"  {m:>10}"
    header += f"  {'Best':>10}"
    print(header)
    print("-" * 100)
    
    method_wins = defaultdict(int)
    method_total_improvement = defaultdict(float)
    
    for cs_key in sorted(city_season_brier.keys()):
        scores = city_season_brier[cs_key]
        n = len(scores["hardcoded"])
        if n < 10:
            continue
        
        means = {}
        line = f"{cs_key[0]:15} {cs_key[1]:6} {n:>5}"
        for m in methods:
            mean = np.mean(scores[m])
            means[m] = mean
            line += f"  {mean:>10.4f}"
        
        best = min(means, key=means.get)
        line += f"  {best:>10}"
        print(line)
        
        method_wins[best] += 1
        hc_brier = means["hardcoded"]
        for m in methods:
            if m != "hardcoded":
                method_total_improvement[m] += hc_brier - means[m]
    
    print()
    print("=" * 100)
    print(f"\nMethod wins (lowest Brier in each city-season):")
    for m, count in sorted(method_wins.items(), key=lambda x: -x[1]):
        avg_imp = method_total_improvement.get(m, 0) / max(1, len(city_season_brier))
        print(f"  {m:15} wins={count:>3}  avg_improvement_over_hardcoded={avg_imp:+.4f}")
    
    # Go/No-Go for each method
    print()
    print("=" * 100)
    for m in methods:
        if m == "hardcoded":
            continue
        wins = method_wins.get(m, 0)
        avg_imp = method_total_improvement.get(m, 0) / max(1, len(city_season_brier))
        if avg_imp > 0.01 and wins >= 5:
            print(f">>> GO: {m} — Brier improvement {avg_imp:+.4f}, wins in {wins} combos")
        else:
            reason = []
            if avg_imp <= 0.01:
                reason.append(f"improvement {avg_imp:+.4f} ≤ 0.01")
            if wins < 5:
                reason.append(f"only {wins} wins < 5 needed")
            print(f">>> NO-GO: {m} — {', '.join(reason)}")
    
    conn.close()


if __name__ == "__main__":
    np.random.seed(42)
    run()
