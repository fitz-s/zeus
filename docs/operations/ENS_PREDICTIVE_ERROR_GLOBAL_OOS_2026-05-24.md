# Universal predictive-error model — global blocked-OOS proof (2026-05-24)

PR #335 acceptance proof (operator §9). Blocked OOS (70/30 by date) per city, MAM,
real MC (n_mc=3000), degC residuals; degF cities use °F bins, degC use point bins.
Methods: raw (no correction) | F50_only (naive TIGGE-prior bias, no transport/scale/gate)
| full_transport (TIGGE prior -> Δ=F25-F50 transport -> OpenData live posterior -> scale + SNR gate).
Script: jobs/866db2ea/global_oos.py.

## Result — 48 cities, 216 held-out points

| method | Brier | LogLoss | P(actual) |
|---|---|---|---|
| raw | 1.065 | 4.834 | 0.172 |
| F50_only | 1.053 | 6.027 | 0.177 |
| **full_transport** | **0.828** | **2.155** | 0.156 |

## Verdict
- full_transport is BEST on Brier (-22% vs raw) AND LogLoss (2.155 vs 4.8/6.0) across the whole
  city set. The §9 universal acceptance gate PASSES: full >= F50-only AND full >= raw on proper
  scores. Not SF-specific; one universal parameterization (location+scale+gate+transport), zero
  per-city rules.
- F50_only (naive bias-only) is WORSE than raw on LogLoss (6.027 vs 4.834): blind bias correction
  is harmful (overconfident shifts move mass off the truth). The full model's scale + confidence
  gate + grid transport is precisely what converts correction from harmful -> beneficial.
- full_transport P(actual) slightly lower (0.156) is CORRECT: residual scale widens the predictive
  distribution, trading peak mass for calibration. Proper scores reward it; downstream Kelly
  haircut on high-uncertainty buckets is the intended use. No overconfidence.

## What this proves
1. Bias-only correction (the naive #334-mean-shift) is necessary but INSUFFICIENT and can be net
   harmful (LogLoss regression) — confirmed at scale, not just SF.
2. The universal location+scale+gate+transport model improves proper scores globally and is the
   correct fix shape.
3. The grid transport (b25 = b50 + E[Δ]) lets the 0.25 OpenData live signal be used through the
   0.5 TIGGE-trained prior safely (no naked covariate-shift), per the operator's adjudication.

## Caveats / not-yet
- n per city is modest (216 points / 48 cities); strong aggregate signal, per-city CIs wide.
- p_raw level only — Platt p_cal refit on corrected+residual-aware pairs is the downstream refit.
- The SNR gate (λ veto) is unit-tested (Chicago synthetic) but rarely triggered in these OOS
  cohorts (most buckets had confident bias); its value shows in disagreement-heavy buckets.
- Live activation (producer wiring, calibration_pairs recompute, Platt refit, enable) is the
  separate gated step AFTER operator verification — this proof is the model-layer correctness gate.
