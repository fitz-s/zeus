# Staleness degrade ladder — boundary derivation (2026-07-17)

Operator go 2026-07-17 ("除了需要累积的内容都执行 使用数学和统计就能证明一切"). Every
boundary below is DERIVED from settled live history, not guessed. Source: live
`state/zeus-forecasts.db` (read-only), live fusion product
`openmeteo_ecmwf_ifs9_bayes_fusion_v1`, `runtime_layer='live'`,
`training_allowed=0`, joined to `settlement_outcomes` with `authority='VERIFIED'`.
The only backtest is settlement-source accuracy, strictly walk-forward.

Reproduce: `scratchpad/derive_ladder.py` (lag-keyed error), `derive_age.py`
(age-window, survivorship note), `derive_variance.py` (variance increment),
plus the signed-drift query below. `scripts/fit_posterior_age_inflation.py`
emits the served artifact.

## Method

Whole-posterior center error = predictive mean (q-weighted bin center, °C) minus
the settled value (F settlements converted to °C). Holding the settled TARGET
fixed and varying the source cycle isolates the causal staleness penalty (a
paired estimator immune to the survivorship deflation of a served-window model):
for a cycle at cycle-lag L behind the target's freshest cycle,
`incr(L) = err(lag L) − err(freshest)`. n = 1479 settled targets (1461
multi-cycle). All p-values are two-sided on the paired mean (normal approx).

## Fresh serving age (the GREEN ceiling basis)

`computed_at − source_cycle_time` at first materialization, healthy live
operation: **p50 = 7.19h, p90 = 9.41h, p99 = 15.75h** (n≈12k). A posterior of age
≤ ~16h is therefore plausibly the CURRENT freshly-served cycle, not a stale one.
`GREEN ≤ 18h` covers the fresh-serving p99 (15.75h) with ~2h margin so a normal
late-materialization tail is never misclassified stale. `age = fresh_floor + L`
is the physical map from cycle-lag to the age the admission gate observes.

## Paired center-error penalty by cycle-lag (MAE)

| lag_h | n    | mean_incr °C | se     | t     | p        | mean |μ−settle| |
|------:|-----:|-------------:|-------:|------:|---------:|---------------:|
| 0     | 1479 | 0.0000       | —      | —     | —        | 0.866 |
| 6     | 1179 | 0.0687       | 0.0132 | 5.20  | 2.0e-07  | 0.878 |
| 12    | 1165 | 0.1610       | 0.0188 | 8.56  | <1e-15   | 0.968 |
| 18    | 1114 | 0.2773       | 0.0238 | 11.65 | <1e-15   | 1.063 |
| 24    | 1235 | 0.3007       | 0.0241 | 12.47 | <1e-15   | 1.133 |
| 30    | 1006 | 0.3841       | 0.0280 | 13.71 | <1e-15   | 1.161 |

Monotone, every band from lag-6 onward highly significant. Freshness is worth
real °C, uniformly — consistent with the previous_runs paired slopes (+0.10–0.17
°C / 6h) already in the plan doc.

## Center-error VARIANCE increment (the AMBER inflation, degC²)

Paired `err² − err_fresh²`, per metric. The AMBER band [18,24)h maps to
cycle-lag ≈ [10.8, 16.8)h → the lag-12 bucket.

| metric | lag_h | n    | v_incr degC² | p (paired) |
|--------|------:|-----:|-------------:|-----------:|
| high   | 6     | 1023 | 0.1525       | 4.2e-04    |
| high   | 12    | 1009 | **0.3620**   | 6.3e-08    |
| high   | 18    | 970  | 0.7279       | <1e-15     |
| low    | 6     | 156  | 0.2241       | 0.10       |
| low    | 12    | 156  | **0.2436**   | 0.060      |
| low    | 18    | 144  | 0.3215       | 0.049      |

**Fitted AMBER inflation (served artifact, as_of 2026-07-17, band 18h):
high v = 0.362 degC² (n=1009), low v = 0.244 degC² (n=156).** Base predictive
variance p50 = 1.86 degC² (predictive_sigma p50 1.364°C), so the AMBER increment
is 13–19% of the base — a modest, well-estimated widening a symmetric sigma can
honestly carry. `low` is thinner and marginally significant; the fit is monotone
(cummax) and fail-open, so a thin/absent low fit degrades to no inflation, never
a spurious one.

## Why AMBER/RED = 24h (signed drift — the honesty limit)

Variance inflation can price the SPREAD component of aged error but NOT a
systematic center BIAS. Signed drift (aged mean − freshest mean, °C):

| lag_h | mean signed drift | |bias| | spread sd |
|------:|------------------:|-------:|----------:|
| 6     | −0.124            | 0.124  | 0.533     |
| 12    | −0.318            | 0.318  | 0.747     |
| 18    | −0.466            | 0.466  | 0.928     |
| 24    | −0.449            | 0.449  | 0.998     |

Through the AMBER band (lag 6–12, age 18–24h) spread DOMINATES bias (ratio
0.23–0.43): the fitted variance widening honestly prices most of the error. At
RED onset (lag ≥18, age ≥24h) |bias| ≥ 0.47°C becomes a large, growing fraction
of the spread and is UNCORRECTABLE by symmetric inflation — widening a biased
belief still centers it wrong. Combined with only ~6h left to the 30h EXPIRED
wall, entry stops (RED) rather than pretending inflation prices it.

## Derived ladder (matches the consult 18/24 sketch, independently confirmed)

- **GREEN ≤ 18h** — full trading, unchanged. Covers fresh-serving p99 15.75h + margin.
- **AMBER (18h, 24h]** — trading continues; predictive sigma widened by the fitted
  age-band variance (high 0.362 / low 0.244 degC²). Spread-dominated → honest.
- **RED (24h, 30h) OR newer-live-cycle-detected-not-active** — no new entries for
  the family, resting makers cancel; held-position monitor/exit stay fully active.
  Bias becomes uncorrectable; ~6h buffer to the wall (operator ENTRY isolation).
- **EXPIRED ≥ 30h** — existing fail-closed law (`replacement_source_cycle_max_age_hours`),
  UNCHANGED. The ladder never weakens this gate.

## Deviations from the consult sketch

1. **Two-provider relaxation to "1 provider + ENS" on AMBER: NOT implemented.**
   The consult listed it, but relaxing the source-clock completeness gate is a
   gate WEAKENING, and no settlement-accuracy proof that a 1-provider AMBER
   posterior settles as well as a 2-provider one was produced here (that requires
   its own walk-forward provider-count-vs-settlement backtest). Per "never weaken
   a gate" + "使用数学和统计就能证明一切", it is DEFERRED to a dedicated derivation.
   AMBER keeps the existing provider-completeness requirement; only the fitted
   sigma inflation and the RED entry-isolation ship.
2. **AMBER sigma inflation applies to the admission directional sigma**
   (`forecast_predictive_sigma_c`, the Milan-24C direction-law gate) as
   `sqrt(sigma² + v)`, not by recomputing the materialized q-vector — minimal
   machinery, no posterior-identity-hash mutation. The q-vector center/bins remain
   the materialized belief; RED handles the regime where that belief is too stale
   to trade at all.
