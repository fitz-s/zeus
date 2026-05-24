# Hierarchical ENS Bias Correction — Test Results (2026-05-24)

Branch: `claude/ens-bias-hierarchical`. Runner: `.venv/bin/python -m pytest -v`.
Python 3.14.3 local (CI uses 3.13), pytest 9.0.2.

## Summary

**32 passed** (after PR pre-check + degF/legacy-TIGGE/disagreement round + double-count regression fix) — all TDD red-first (each test confirmed failing before its
implementation landed).

| File | Tests | Scope |
|---|---|---|
| `tests/test_ens_bias_model.py` | 10 | posterior shrinkage estimator + pre-MC application + train/serve guard |
| `tests/test_ens_bias_fit.py` | 8 | bucket fitter (robust mean, transfer-floor prior, min-n, paired-delta) |
| `tests/test_ens_bias_repo.py` | 14 | DB residual loader + `model_bias_ens_v2` store |

## What each test proves

### Estimator — `src/calibration/ens_bias_model.py::posterior_bias`
- `test_posterior_dominated_by_live_when_n_large` — abundant live ⇒ w→1, posterior tracks live mean.
- `test_posterior_falls_back_to_prior_when_no_live_data` — no live ⇒ posterior == TIGGE prior, sd = √V₀.
- `test_posterior_variance_never_exceeds_either_input` — V_post = (1/V₀+1/V_O)⁻¹ ≤ min(V₀,V_O) (information cannot hurt).
- `test_large_paired_transfer_delta_inflates_prior_uncertainty` — large OpenData−TIGGE paired δ ⇒ prior down-weighted, live wins more.
- `test_delta_g_group_correction_shifts_prior_mean` — group offset δ_g shifts the prior mean.
- `test_bias_sign_convention_matches_forecast_minus_actual` — bias = forecast−actual; correction subtracts it (cold ⇒ warms).

### Pre-MC application + guard
- `test_apply_bias_to_extrema_warms_cold_forecast` — `corrected = raw − bias` applied to member extrema BEFORE the 10k MC (binning/rounding are non-linear).
- `test_train_serve_guard_blocks_live_correction_without_corrected_platt` — enabling live correction while Platt was fit on `bias_corrected=0` pairs RAISES (out-of-domain inference).
- `test_train_serve_guard_allows_consistent_states` — consistent states pass.

### Fitter — `fit_bucket` / `robust_mean`
- `test_robust_mean_ignores_outliers` — trimmed mean rejects a gross outlier.
- `test_fit_bucket_live_dominates_when_abundant_and_precise` — abundant precise live ⇒ w>0.8.
- `test_fit_bucket_falls_back_to_prior_when_live_empty` — no live ⇒ prior only.
- `test_fit_bucket_drops_live_below_min_n_floor` — n<min_live_n ⇒ live dropped.
- `test_fit_bucket_more_tigge_tightens_prior` — more TIGGE samples ⇒ smaller prior sd.
- `test_fit_bucket_paired_delta_shifts_weight_to_live` — paired δ inflates prior variance.
- `test_fit_bucket_robust_to_a_few_outlier_days` — heat-event/miss outlier days do not swing the bucket bias.

### DB I/O — `src/calibration/ens_bias_repo.py`
- `test_load_bucket_residuals_mean_minus_actual` — residual = ensemble_mean − settlement (native unit).
- `test_load_bucket_residuals_freshest_snapshot_wins` — latest `available_at` per (city,date) used.
- `test_load_bucket_residuals_filters_data_version_and_lead` — wrong product + over-lead excluded.
- `test_load_bucket_residuals_season_month_filter` — `season_months` restricts by month(target_date).
- `test_model_bias_ens_v2_roundtrip` — write/read of the new `model_bias_ens_v2` table.

## Raw pytest output (HISTORICAL 21-count snapshot — superseded; current count 31)

```
collected 21 items

tests/test_ens_bias_model.py::test_posterior_dominated_by_live_when_n_large PASSED
tests/test_ens_bias_model.py::test_posterior_falls_back_to_prior_when_no_live_data PASSED
tests/test_ens_bias_model.py::test_posterior_variance_never_exceeds_either_input PASSED
tests/test_ens_bias_model.py::test_large_paired_transfer_delta_inflates_prior_uncertainty PASSED
tests/test_ens_bias_model.py::test_delta_g_group_correction_shifts_prior_mean PASSED
tests/test_ens_bias_model.py::test_bias_sign_convention_matches_forecast_minus_actual PASSED
tests/test_ens_bias_model.py::test_apply_bias_to_extrema_warms_cold_forecast PASSED
tests/test_ens_bias_model.py::test_train_serve_guard_blocks_live_correction_without_corrected_platt PASSED
tests/test_ens_bias_model.py::test_train_serve_guard_allows_consistent_states PASSED
tests/test_ens_bias_fit.py::test_robust_mean_ignores_outliers PASSED
tests/test_ens_bias_fit.py::test_fit_bucket_live_dominates_when_abundant_and_precise PASSED
tests/test_ens_bias_fit.py::test_fit_bucket_falls_back_to_prior_when_live_empty PASSED
tests/test_ens_bias_fit.py::test_fit_bucket_drops_live_below_min_n_floor PASSED
tests/test_ens_bias_fit.py::test_fit_bucket_more_tigge_tightens_prior PASSED
tests/test_ens_bias_fit.py::test_fit_bucket_paired_delta_shifts_weight_to_live PASSED
tests/test_ens_bias_fit.py::test_fit_bucket_robust_to_a_few_outlier_days PASSED
tests/test_ens_bias_repo.py::test_load_bucket_residuals_mean_minus_actual PASSED
tests/test_ens_bias_repo.py::test_load_bucket_residuals_freshest_snapshot_wins PASSED
tests/test_ens_bias_repo.py::test_load_bucket_residuals_filters_data_version_and_lead PASSED
tests/test_ens_bias_repo.py::test_load_bucket_residuals_season_month_filter PASSED
tests/test_ens_bias_repo.py::test_model_bias_ens_v2_roundtrip PASSED

============================== 21 passed in 0.81s ==============================
```

## Scope of this PR (model layer only — NOT yet wired live)

Built + tested: the empirical-Bayes estimator, the bucket fitter, the residual
loader, the `model_bias_ens_v2` store, the pre-MC application, and the train/serve
guard. **Deliberately excluded** (separate gated PR): the full
`calibration_pairs_v2` recompute (~1.5M snapshots × 10k MC), the Platt refit, the
live evaluator wiring, and flipping `bias_correction_enabled`. Those touch live
money + heavy compute and must follow review of this foundation + the documented
activation order (recompute corrected pairs → refit Platt → enable → invariant tests).

## Pre-merge TODO (review items)
- Register new files: `src/calibration/ens_bias_model.py`, `ens_bias_repo.py` in
  `architecture/source_rationale.yaml`; the 3 test files in `architecture/test_topology.yaml`.
- Decide `model_bias_ens_v2` table ownership (world vs forecasts) in `db_table_ownership.yaml`.
- Tune `V_TRANSFER_DEFAULT` (0.25 = (0.5°C)²) against validated equivalence per cohort.


## Blocker fixes (PR #334 pre-check, 2026-05-24)
- B1 unit: `load_bucket_residuals` now normalizes members + settlement to canonical degC via
  `members_unit` (degF city test added) — fixes the 1.8x mis-scale hazard.
- B2 filters: authority='VERIFIED' (snapshot+settlement), contributor_policy
  ('full_contributor_only' = contributes=1 + not boundary-ambiguous + training_allowed +
  causality OK | 'all_for_diagnostic'). Targets the LIVE contributing residual population
  (~-1.1degC), not all snapshots (~-1.9degC).
- B3 read-safety: `read_bias_model` requires exact `live_data_version` (no latest-row fallback).
- B4 `fit_bucket` min_live_n default 5 -> 20 (city-season live tier).
- B5 schema lineage: month, live/prior source_id + data_version, bias_unit, n_paired,
  paired_delta_c, v0_c2, vo_c2, contributor_policy, training_cutoff.
- B6 LOW metric residual test.
- B7 leakage: `settled_before` training cutoff (target_date strictly before).
Still open (documented, not in this PR): wider train/serve guard keyed on bias_model_key/family
(needs Platt model_key plumbing); residual tail metrics (p90/p95) for Kelly haircut; manifest
registration + table ownership; calibration_pairs recompute + Platt refit + live wiring.


## Round 2 additions (2026-05-24, second pre-check)
- `_to_c` now parses degF/degC/fahrenheit/celsius (real members_unit is 'degF'/'degC', not
  'F'/'C') and RAISES on unknown units; fixture uses 'degF'. (Earlier first-char check silently
  failed on 'degF' — a 1.8x correctness bug; an MC "bullseye" earlier was that bug's artifact.)
- `contributor_policy='legacy_tigge_null_passthrough'` so the TIGGE prior (legacy NULL
  contributes) loads; `full_contributor_only` still excludes NULL (live OpenData).
- `model_bias_ens_v2` PK month NOT NULL DEFAULT 0 (season-level=0); read coerces month None->0.
- Estimator now flags prior<->live conflict: `disagreement_high` (|live-prior|>2*sqrt(V0+V_O))
  + `heterogeneity_var` (excess between-source variance) — the Chicago case (prior cold, live
  neutral) must become LOW-confidence, not a confident point shift. `sd` stays the strict
  precision-combine; downstream adds heterogeneity_var for Kelly haircut / gating.
- Loader skips rows with NULL members_unit when normalizing to degC.
Total: 31 tests (model 10, fit 7, repo 14).

## Honest MC diff (unit-fixed) — SF 2026-05-22, actual 68F (bin 68-69)
prior-only correction: members 57.8 -> 63.7F; argmax bin 57-58 -> 63-64F; P(actual)=0.000.
The +6F warm shift is directionally correct but SF's ~+10F extreme gap exceeds a seasonal-mean
correction -> P(actual bin) stays 0 (PARTIAL correction; matches OOS ~4C residual + tail caveat).
The mechanism (pre-MC temperature correction shifts p_raw warmer) is validated; SF extreme tails
need the disagreement/tail-variance gating + (separately) Platt p_cal refit.

## Still open before MERGE (not in this PR)
manifest registration (source_rationale/test_topology); table ownership (db_table_ownership);
settlement-known-time leakage cutoff; downstream gate that scales correction by confidence /
applies Kelly haircut when disagreement_high; calibration_pairs recompute + Platt refit + live wiring.


## Doc-list note (Copilot #334)
The per-test enumerations above are illustrative, not exhaustive; the authoritative
list is the test files themselves (model 10, fit 8, repo 14 = 32). New since the
first draft: test_posterior_flags_disagreement_and_widens_sd,
test_var_of_mean_does_not_double_count_transfer_for_single_sample, and the repo
degF/contributor/leakage/LOW/read-safety/legacy-passthrough tests. Bug fixed:
_var_of_mean no longer double-counts V_TRANSFER for a single-sample prior.
