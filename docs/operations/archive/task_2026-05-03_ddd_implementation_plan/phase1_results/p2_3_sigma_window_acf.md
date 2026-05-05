# Phase 1 §2.3 — σ_window ACF Analysis

Created: 2026-05-03 (executed)
Authority: PLAN.md §2.3 + canonical reference §5.2

## Method

For each probe city, computed:
- ACF of daily directional coverage at lags 1-14
- Rolling σ over 30/60/90-day windows; report min/median/max

Probe cities span the regime spectrum: stable (Tokyo, NYC), thin (Lagos, Jakarta), intermediate.

## ACF table — does coverage variance persist?

If ACF(lag=k) < 0.2 for all k ≥ 5, white noise dominates → 30-day window sufficient.

| city | n_days | mean | σ_global | ACF(1) | ACF(2) | ACF(3) | ACF(5) | ACF(7) | ACF(14) |
|---|---|---|---|---|---|---|---|---|---|
| Tokyo | 304 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| Singapore | 304 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| Wellington | 303 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| Denver | 299 | 0.962 | 0.122 | 0.498 | 0.439 | 0.443 | 0.336 | 0.400 | 0.108 |
| NYC | 303 | 0.995 | 0.030 | 0.342 | 0.419 | 0.127 | 0.056 | 0.056 | -0.016 |
| Lagos | 280 | 0.844 | 0.221 | 0.412 | 0.302 | 0.234 | 0.155 | 0.104 | -0.026 |
| Shenzhen | 304 | 0.953 | 0.109 | 0.452 | 0.298 | 0.212 | 0.132 | 0.057 | 0.115 |
| Jakarta | 304 | 0.958 | 0.111 | 0.084 | 0.186 | 0.175 | 0.104 | 0.086 | 0.121 |
| Lucknow | 302 | 0.987 | 0.071 | -0.032 | -0.033 | -0.006 | -0.033 | -0.033 | -0.034 |
| Houston | 303 | 0.983 | 0.066 | 0.578 | 0.460 | 0.416 | 0.496 | 0.299 | 0.303 |

## Rolling σ comparison — does longer window matter?

Each cell is `min/median/max` of σ over the rolling windows of given length. For runtime σ-band, σ ≈ median value is what gets used in shortfall computation.

| city | σ_30 (min/med/max) | σ_60 (min/med/max) | σ_90 (min/med/max) |
|---|---|---|---|
| Tokyo | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 |
| Singapore | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 |
| Wellington | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 / 0.000 |
| Denver | 0.000 / 0.077 / 0.238 | 0.000 / 0.074 / 0.209 | 0.015 / 0.068 / 0.183 |
| NYC | 0.000 / 0.000 / 0.085 | 0.000 / 0.000 / 0.065 | 0.000 / 0.000 / 0.054 |
| Lagos | 0.104 / 0.176 / 0.350 | 0.126 / 0.221 / 0.317 | 0.143 / 0.203 / 0.284 |
| Shenzhen | 0.000 / 0.087 / 0.193 | 0.000 / 0.093 / 0.163 | 0.000 / 0.107 / 0.149 |
| Jakarta | 0.000 / 0.036 / 0.182 | 0.000 / 0.064 / 0.176 | 0.000 / 0.075 / 0.160 |
| Lucknow | 0.000 / 0.051 / 0.138 | 0.018 / 0.083 / 0.122 | 0.033 / 0.083 / 0.110 |
| Houston | 0.000 / 0.000 / 0.140 | 0.000 / 0.018 / 0.126 | 0.000 / 0.015 / 0.110 |

## Verdict

- Across non-constant probe cities, max |ACF(lag=5)| = 0.496
- Across non-constant probe cities, max |ACF(lag=14)| = 0.303
- **WARN**: ACF persists past lag 14 in some cities; 90-day window recommended.

**Recommended sigma_window = 90 days**

## SNR check — is σ small enough to be a useful absorber?

Typical 'shortfall we care about' ≈ 0.10. If median σ > 0.10, the σ-band swallows all anomalies (false negatives).

- ⚠ Lagos: σ_90_median = 0.203 > 0.10 — σ-band may absorb real anomalies
- ⚠ Shenzhen: σ_90_median = 0.107 > 0.10 — σ-band may absorb real anomalies

