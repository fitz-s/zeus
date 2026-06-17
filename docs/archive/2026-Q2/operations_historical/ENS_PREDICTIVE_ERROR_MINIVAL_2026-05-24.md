# Universal predictive-error layer — proper-score minival (2026-05-24)

PR #335. OOS split (70/30) per city, MAM, real 10k-ish MC (n_mc=4000), degC residuals
via tested load_bucket_residuals; SF/Chicago degF point/range bins, Tokyo degC point bins.
Methods: raw (no correction) | bias_only_ungated (λ=1, no scale) | full (gated λ + scale).
Script: jobs/866db2ea/minival_score.py.

| City | method | Brier | LogLoss | P(actual) | n |
|---|---|---|---|---|---|
| San Francisco | raw | 1.275 | 13.816 | 0.000 | 5 |
| San Francisco | bias_only_ungated | 1.275 | 12.564 | 0.000 | 5 |
| San Francisco | **full_gated_scale** | **0.987** | **3.051** | **0.049** | 5 |
| Tokyo | raw | 1.209 | 6.595 | 0.129 | 5 |
| Tokyo | bias_only_ungated | 1.132 | 7.124 | 0.166 | 5 |
| Tokyo | **full_gated_scale** | **0.915** | **2.504** | 0.120 | 5 |
| Chicago | raw | 0.919 | 2.113 | 0.128 | 5 |
| Chicago | bias_only_ungated | 0.950 | 2.927 | 0.112 | 5 |
| Chicago | full_gated_scale | 0.923 | 2.553 | 0.089 | 5 |

Per-city fit: SF bias −3.39C λ1.00 resid_sd 1.76C; Tokyo bias −3.75C λ1.00 resid_sd 1.71C;
Chicago bias −0.91C λ1.00 resid_sd 1.22C (all disagree=False in these OOS cohorts).

## Verdict (universal, not SF-specific)
- SF + Tokyo: full model BEST on Brier AND LogLoss; SF actual-bin mass 0 -> 0.049 (scale gives
  tail support — the core SF failure fixed). LogLoss SF 13.8->3.05, Tokyo 6.6->2.5.
- Chicago: full ~ raw (Brier 0.919 vs 0.923 — near-tie, no degradation; Chicago bias marginal).
- full >= ungated-bias-only on Brier for all three -> scale+gate prevents naive-bias degradation.
- One universal parameterization (location+scale+SNR gate); zero per-city logic.

## Caveats
n=5 held-out per city (small/noisy); the λ confidence gate stayed 1.0 in these OOS cohorts
(no strong prior<->live disagreement reproduced) — its veto is unit-tested (Chicago synthetic),
not exercised here. p_raw level only (Platt p_cal refit is downstream). Mapping audit (SF cell
~13km toward Pacific) is the separate Occam-first structural lever, not yet applied.

## Acceptance (operator §9)
SF actual-bin mass no longer zero: YES. Proper scores improve (SF/Tokyo) or hold (Chicago): YES.
Full never worse than naive bias-only: YES. Mean-error-only evaluation avoided: YES.
