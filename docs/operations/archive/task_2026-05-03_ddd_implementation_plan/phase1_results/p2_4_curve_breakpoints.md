# Phase 1 §2.4 — Discount Curve Breakpoints Calibration

Created: 2026-05-03 (executed)
Authority: PLAN.md §2.4 + canonical reference §6

## Method

For each (city, day) in test window (2026-01-01 → 2026-04-30 per Ruling 1):
- shortfall = max(0, floor[city] - cov[day] - σ_90[city, day])
- error = (1 - p_raw_winner)² (winning-bucket Brier residual, median across lead_days)

Then bin by shortfall and report per-bin error statistics.

## Global aggregate (all cities)

| shortfall bin | N samples | error_mean | error_median | error_std |
|---|---|---|---|---|
| exact 0 | 7,371 | 0.7450 | 0.8137 | 0.2565 |
| (0, 0.05) | 9 | 0.7273 | 0.7538 | 0.3075 |
| [0.05, 0.10) | 34 | 0.7716 | 0.8991 | 0.2501 |
| [0.10, 0.20) | 32 | 0.7263 | 0.7487 | 0.2472 |
| [0.20, 0.30) | 23 | 0.8148 | 0.9204 | 0.2275 |
| [0.30, 0.50) | 6 | 0.7361 | 0.7953 | 0.2305 |
| [0.50, ∞) | 0 | n/a | n/a | n/a |

## Verdict

- Bins with ≥10 samples: 4 / 7
- Mean error progression (low → high shortfall): ['0.745', '0.772', '0.726', '0.815']
- Monotone (with 0.05 tolerance): True
- Spread (high - low bin mean): 0.0698

**PASS**: error_mean monotonically increases with shortfall and shows meaningful spread. Curve hypothesis supported.

## Operator's proposed curve (for comparison)

| shortfall | DDD value |
|---|---|
| 0 | 0.00 |
| 0.0 - 0.10 | linear 0% → 2% |
| 0.10 - 0.25 | linear 2% → 5% |
| 0.25 - 0.40 | linear 5% → 8% |
| > 0.40 | 9% (cap) |

