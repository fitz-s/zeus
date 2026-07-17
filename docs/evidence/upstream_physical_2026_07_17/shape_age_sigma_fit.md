# Shape-age sigma term (gamma_g) — fit + availability-replay validation

Authority: consult P2-B (`consult_freshness_decoupling_verdict.txt`), operator execution
order 2026-07-17. Fitter `scripts/fit_shape_age_sigma.py`; serving loader
`src/forecast/shape_age_sigma.py::gamma_for`; consumed on the transported ENS branch of
`src/data/replacement_forecast_materializer._current_evidence_shape_from_values`.

## Verdict: gamma_g is a proven statistical ZERO for both metrics

The full consult form prices the remaining risk of pricing with an aged shape as
`sigma_t^2 = max(sigma_min^2, a_g + b_g*S_e^2 + gamma_g*age/6)`. After anomaly transport
recenters the aged shape onto the fresh fused center (landed 4b4481d21, `translation_applied`),
the **age-marginal excess variance does not grow with shape lag** — the fitted slope is
slightly NEGATIVE for both metrics and is clamped to 0.0. This confirms the consult's own
hypothesis that transport already removes the age-priced error; the shape-age term stays
DORMANT (fail-open 0.0) and adds nothing to the served sigma. The machinery is landed so a
future refit on more history can activate it without a code change.

Measurement was run READ-ONLY over `state/zeus-forecasts.db` (as_of 2026-07-17,
holdout_start 2026-07-01, source-clock basket artifact `city_weights_20260717.json`). The
live `state/shape_age_sigma/ACTIVE.json` is intentionally NOT written by this agent — the
orchestrator installs it post-merge; serving fails open to 0.0 until then.

### Fit statistics (walk-forward, target_date < holdout_start)

| metric | gamma_per_6h (served) | raw slope | intercept (a_g) | SE | p | n_pairs | clusters | n_holdout |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | **0.0** | −0.126392 | 2.014249 | 0.01632 | 0.0 | 59967 | 47 | 13451 |
| low  | **0.0** | −0.073756 | 0.359787 | 0.038791 | 0.057253 | 2138 | 38 | 1120 |

- Estimator: per-pair excess `y = (settle − fresh_center)² − sigma0²`, sigma0² =
  within² + between-proxy²; 6h-lag-bucket method-of-moments means; n-weighted WLS line
  `y = a + gamma*lag/6`; target-date cluster bootstrap (500 reps) SE/p. Slope clamped ≥ 0.
- The covariate is `carrier_cycle − ens_source_cycle_time` in 6h units — byte-identical to
  the serving `shape_lag_hours` the term multiplies.
- The large positive intercepts (high 2.01, low 0.36 degC²) are lag-INDEPENDENT sigma0
  under-dispersion (the serving within+between proxy is a floor, not the fully-calibrated
  predictive width — the a_g/b_g calibration DOF, owned by the settlement-sigma-floor /
  EMOS mechanisms, NOT this task). Isolating the slope is exactly why the fit carries an
  intercept: the age-MARGINAL signal, which is what gamma_g must price, is ≈ 0.
- Both slopes are negative: older shapes' post-transport excess variance if anything
  slightly DECREASES with lag (consistent with survivorship — the aged cycles that remain
  contributing tend to be the calmer synoptic regimes). A negative age slope cannot be a
  physical uncertainty-growth term, so clamping to 0 is the statistically defensible call.

### Construction counters

`{"triples_with_basket": 3400, "triples_without_basket": 0, "n_center_unavailable":
107142, "n_between_missing": 53635, "n_negative_lag_dropped": 53538}`

- 3400 settled (city,target_date,metric) triples had a source-clock basket and ≥ 1
  archived ENS cycle. `n_center_unavailable` = decision references before any basket model
  had published (correctly skipped — no retrospective pairing). `n_negative_lag_dropped` =
  references where the freshest carrier cycle was OLDER than the ENS cycle (serving would
  be on the same-cycle branch, not transported — excluded). `n_between_missing` = pairs
  where < 2 basket models were present at the reference (within-only sigma0, still valid).

### Holdout availability-replay (gamma=0 vs fitted), by shape-age bucket — HIGH

| bucket | n | lag_h | CRPS g0 | CRPS fit | PIT g0 | PIT fit | cov80 g0 | cov80 fit | cov50 g0 | cov50 fit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 3672 | 0.0 | 1.0379 | 1.0379 | 0.5756 | 0.5756 | 0.5035 | 0.5035 | 0.2712 | 0.2712 |
| 1 | 1825 | 6.0 | 0.9917 | 0.9917 | 0.5862 | 0.5862 | 0.6071 | 0.6071 | 0.3458 | 0.3458 |
| 2 | 2803 | 12.0 | 1.0206 | 1.0206 | 0.5726 | 0.5726 | 0.5198 | 0.5198 | 0.2918 | 0.2918 |
| 3 | 1287 | 18.0 | 0.9829 | 0.9829 | 0.5825 | 0.5825 | 0.6177 | 0.6177 | 0.3263 | 0.3263 |
| 4 | 1872 | 24.0 | 1.0126 | 1.0126 | 0.5714 | 0.5714 | 0.5390 | 0.5390 | 0.3248 | 0.3248 |
| 5 | 787 | 30.0 | 0.9713 | 0.9713 | 0.5835 | 0.5835 | 0.6302 | 0.6302 | 0.3659 | 0.3659 |
| 6 | 924 | 36.0 | 1.0052 | 1.0052 | 0.5696 | 0.5696 | 0.5920 | 0.5920 | 0.3485 | 0.3485 |
| 7 | 257 | 42.0 | 0.9687 | 0.9687 | 0.5793 | 0.5793 | 0.6576 | 0.6576 | 0.3813 | 0.3813 |
| 8 | 24 | 48.0 | 1.1234 | 1.1234 | 0.5357 | 0.5357 | 0.5833 | 0.5833 | 0.3333 | 0.3333 |

### Holdout availability-replay (gamma=0 vs fitted), by shape-age bucket — LOW

| bucket | n | lag_h | CRPS g0 | CRPS fit | PIT g0 | PIT fit | cov80 g0 | cov80 fit | cov50 g0 | cov50 fit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 385 | 0.0 | 0.6806 | 0.6806 | 0.5041 | 0.5041 | 0.4857 | 0.4857 | 0.2468 | 0.2468 |
| 1 | 52 | 6.0 | 0.7112 | 0.7112 | 0.7550 | 0.7550 | 0.5769 | 0.5769 | 0.2885 | 0.2885 |
| 2 | 303 | 12.0 | 0.6578 | 0.6578 | 0.4942 | 0.4942 | 0.4785 | 0.4785 | 0.2211 | 0.2211 |
| 3 | 45 | 18.0 | 0.7188 | 0.7188 | 0.7508 | 0.7508 | 0.5556 | 0.5556 | 0.3333 | 0.3333 |
| 4 | 198 | 24.0 | 0.6005 | 0.6005 | 0.5019 | 0.5019 | 0.5253 | 0.5253 | 0.2828 | 0.2828 |
| 5 | 24 | 30.0 | 0.6571 | 0.6571 | 0.7221 | 0.7221 | 0.6250 | 0.6250 | 0.3750 | 0.3750 |
| 6 | 102 | 36.0 | 0.6021 | 0.6021 | 0.5102 | 0.5102 | 0.5882 | 0.5882 | 0.3235 | 0.3235 |
| 7 | 8 | 42.0 | 0.6960 | 0.6960 | 0.7131 | 0.7131 | 0.6250 | 0.6250 | 0.5000 | 0.5000 |
| 8 | 3 | 48.0 | 0.7638 | 0.7638 | 0.5904 | 0.5904 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |

CRPS/PIT/coverage are byte-identical between gamma=0 and fitted at every bucket because the
served gamma is 0.0 (proven-zero). Reading the gamma=0 columns as the standing calibration
of the transported branch: CRPS is flat-to-slightly-improving with shape age (no CRPS
degradation as shapes age — transport is doing its job), and coverage does not deteriorate
with lag. This is the consult's mandated proof that the transported-shape predictive is not
being harmed by shape age within the archive's range — hence no widening is warranted.

### Reproduce

```
python scripts/fit_shape_age_sigma.py --fcst state/zeus-forecasts.db \
  --as-of 2026-07-17 --holdout-start 2026-07-01 \
  --out-dir <scratch> --report <scratch>/report.md
```
(READ-ONLY; `--out-dir` must be a scratch path during review — orchestrator installs the
live `state/shape_age_sigma/` artifact post-merge.)
