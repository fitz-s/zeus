# sigma_tau_calibration -- Plan

Date: 2026-07-28
Branch: `claude/sigma-tau-calib`
Status: active

## Problem

Live posteriors are under-dispersed lead-dependently on the CURRENT-EVIDENCE
(Day0 `_current_shape is not None`) path of the replacement materializer. An
OOS bakeoff (M0..M4 walk-forward, `/private/tmp/.../calib_curves/bakeoff.py`)
selected M2 -- per-`(unit_family, metric)` `k(tau)` times per-city variance
shrinkage -- as the best form that stays inside current law (no city bias
term, no market-price anchor, no historical floor on the current-evidence
shape). `tau = lead_target_h` = hours from `computed_at` to the END of
`target_date` UTC (`target_date + 1 day 00:00 UTC`).

The current-evidence site (`replacement_forecast_materializer.py`, comment
"Historical k/w/floors would change a decision-time-only shape") hardcodes
`(1.0, 0.0, 0.0)` -- neutral by construction, never fitted. This replaces that
hardcode with a lookup into a NEW walk-forward-fitted artifact
(`state/sigma_tau_calibration.json`, written only by
`scripts/fit_sigma_tau_calibration.py`), following the exact precedent of the
existing `state/sigma_scale_fit.json` / `_replacement_sigma_scale_lookup` /
`_effective_unit_sigma_scale` machinery (materializer.py:1243-1370), which
already governs the HISTORICAL (non-Day0) path and stays untouched.

## Model (M2, evidence basis -- not re-derived here)

Per `(unit_family in {C,F}, metric in {high,low})` group:
- `z = settled_c - mu`, tau buckets `[0,6),[6,12),[12,24),[24,36),[36,48),
  [48,72),[72,inf)`.
- `k(tau) = sqrt(mean((z/sig)^2))` per bucket, Normal MLE. Bucket `n<60` =>
  UNFITTED, inherits the family/metric-global pooled `k` (train-only,
  never validation rows). Whole group `n<60` total => group refused,
  `k=1.0` everywhere (fail-closed, same law as `fit_sigma_scale.py`
  `MIN_CELLS`).
- Per-city variance correction, cities with `n>=30` (pooled across tau
  within the group): `c_raw = sqrt(mean((z/(k(tau)*sig))^2))`, shrunk
  `c_shrunk^2 = (n_c*c_raw^2 + n0) / (n_c + n0)`, `n0=100` (shrinks toward 1,
  never toward 0 -- unlike bakeoff.py's log-shrink, this is the mission's
  explicit form and is what ships).
- Served `k_eff(unit, metric, tau, city) = k(tau) * c_shrunk(city)`; `w` and
  `floor_steps` stay exactly `0.0` (k-only artifact for this path; the
  uniform-mixture/absolute-floor terms are a DIFFERENT calibration surface
  and are out of scope here).

`center`/`mu` is never touched (RAW law). No gates, no caps.

## Deliverables

1. `scripts/fit_sigma_tau_calibration.py` -- walk-forward fitter, READ-ONLY on
   `state/zeus-forecasts.db` (`mode=ro`), writes the artifact to a path given
   on the command line (operator places it; never a default under `state/`).
   `--validate CUTOFF` prints the OOS mean-log-lik ladder (`k=1` vs fitted)
   and coverage@68.3 per family/metric, fit strictly before cutoff / validated
   strictly at/after it.
2. `src/data/replacement_forecast_materializer.py` -- new
   `_sigma_tau_calibration_lookup` / `_effective_sigma_tau_scale` cache
   functions (same fail-soft shape as `_replacement_sigma_scale_lookup` /
   `_effective_unit_sigma_scale`), a `_lead_target_h` / `_tau_bucket_label`
   pair for the tau computation, and the current-shape site now calls the new
   lookup keyed by `(unit_family, metric, tau_bucket, city)` instead of the
   hardcoded tuple. FAIL-CLOSED TO TODAY: artifact absent / unparseable /
   family+metric group unfitted / bucket unfitted with no valid group global
   `k` => exactly `(1.0, 0.0, 0.0)`, never raises. The historical
   (`_current_shape is None`) branch is untouched -- still
   `_effective_unit_sigma_scale`. Provenance gets `sigma_tau_artifact_hash`
   (identity of the artifact file actually read) alongside the existing
   `sigma_scale_k_applied` stamp (which fires only when the applied k != 1.0
   -- unchanged trigger, now also covers the tau path's k).
3. Tests: loader unit tests (absent/malformed/fitted/unfitted-bucket
   inherits-global/city-shrinkage), a serving-equivalence test proving the
   historical path is byte-identical and the current-shape path with no
   artifact present is byte-identical to today, and a fitter smoke test on a
   synthetic sqlite fixture (never the live DB).
4. Registry: `architecture/test_topology.yaml` entries for the new test
   files, following the `test_trust_policy` + `test_metadata` + `categories`
   pattern from commit `9b038e7e9`.

## Acceptance

- Existing materializer test suite (`tests/test_replacement_forecast_materializer.py`,
  `tests/test_replacement_sigma_scale_f_family.py`, `tests/forecast/test_sigma_authority.py`)
  passes unchanged.
- New loader/serving/fitter tests pass.
- `--validate` against the live DB (cutoff 2026-07-21) produces a real OOS
  log-lik improvement over `k=1` for at least the C/high group (the group
  with the clearest evidence-basis signal).
- `git diff --check`, `py_compile`.

### `--validate 2026-07-21` results (live DB, read-only, 2026-07-28)

```
n_train=12220  n_val=7556
C/high: n_train=9578 n_val=5122 global_k=1.205819 oos_mean_loglik k=1:-2.46040 fitted:-2.27363 delta:+0.18676  coverage@68.3 k=1:0.5920 fitted:0.5935
C/low:  n_train=1180 n_val=848  global_k=0.877829 oos_mean_loglik k=1:-1.69417 fitted:-1.89301 delta:-0.19884  coverage@68.3 k=1:0.5542 fitted:0.5248
F/high: n_train=1244 n_val=1359 global_k=0.971954 oos_mean_loglik k=1:-1.54162 fitted:-1.54996 delta:-0.00834  coverage@68.3 k=1:0.7116 fitted:0.6600
F/low:  n_train=218  n_val=227  global_k=1.028415 oos_mean_loglik k=1:-1.98954 fitted:-1.77861 delta:+0.21093  coverage@68.3 k=1:0.3612 fitted:0.4097
```

C/high (the group with by far the most data and the clearest evidence-basis signal) improves OOS
mean log-lik by +0.187 nats. F/low also improves (+0.211, small n, noisy). C/low and F/high are
flat-to-slightly-negative on THIS particular walk-forward split -- both are exactly the "wide CIs"
low-sample groups the evidence basis already flagged, not a defect in the fitter; they still pass
`MIN_GROUP_N=60` so they are licensed (`fitted=True`), just noisier. Full production fit (no
cutoff, `since=2026-07-11..2026-07-27`, `n_final=19776`): global_k C/high=1.082, C/low=0.969,
F/high=0.856, F/low=0.907 -- matches the cited evidence ranges (C/high~1.07-1.18, F/high~0.80-0.97
shrink, C/low & F/low~1) to within normal walk-forward-split noise.

## Work record

- 2026-07-28: read bakeoff.py and fit_inputs.py per the mission's evidence basis. Reproducing
  bakeoff.py's own `fit_k_by_tau` (per-observation `sqrt(mean((z/sig)^2))`) verbatim against the
  live DB gave `global_k~1.7` for C/high with NO rising-with-tau trend -- it does not match the
  cited evidence ("C,high k~1.07-1.18 rising with tau; F,high k~0.80-0.97 shrink"). Cross-checked
  fit_inputs.py's OTHER estimator (`table3_k_fit`: `std(z,ddof=1)/sqrt(mean(sig^2))`, the classical
  ensemble spread-skill ratio) against the same live rows and it reproduced the cited ranges to 3
  sig figs for all four groups (including the F/high SHRINK direction). Root cause: ~1.5% of
  C/high rows have near-zero `predictive_sigma_c`, which the per-observation ratio squares and
  averages (blowing up the mean), while the aggregate std/rms ratio is robust to that. Shipped
  `_spread_skill_k` (std/rms), not bakeoff's per-observation MLE ratio -- flagged prominently in
  both the fitter docstring and PLAN.md so a future reader doesn't "fix" it back.
- Neutralization site located by the exact quoted string at
  `src/data/replacement_forecast_materializer.py:4436` (base HEAD had it near line 4304; this
  worktree already carried +~130 lines from prior commits). `_SIGMA_SCALE_FIT_PATH` cache pattern
  read at lines 1243-1382 (untouched); new functions inserted immediately after
  `_replacement_city_candidate_lookup` (was line 1382).
- Fresh worktree was missing `config/settings.json` (gitignored) -- copied from the main checkout
  so tests could import `src.config`; this is a config file, not a `state/` DB.
- `src/state/db_writer_lock.py` allowlist required one addition for the fitter's own
  `sqlite3.connect(...?mode=ro)` call, following the EXACT precedent comment format
  `fit_sigma_scale.py` already uses in that list.
- Registry: `test_sigma_tau_calibration_serving_equivalence.py` (the safety-property antibody)
  registered in `test_trust_policy` / `categories.core_law_antibody` / `test_metadata`, matching
  the `test_replacement_fused_q_shape.py` precedent exactly. The pure-function loader test
  (`test_sigma_tau_calibration_lookup.py`) and the fitter smoke test
  (`test_fit_sigma_tau_calibration_fitter.py`) were left UNREGISTERED, matching the precedent of
  their closest sibling (`test_replacement_sigma_scale_f_family.py`, also unregistered).
  `topology_doctor.py --tests` issue count: baseline (main checkout, same commit) 511, worktree
  513 (+2, both benign `test_topology_missing` warnings for the two unregistered files); zero
  change in any `error:` category.
- Full regression: every test file importing `replacement_forecast_materializer` (42 files, 376
  cases) passes except 10 pre-existing failures verified identical on the unmodified main checkout
  at the same base commit (5 schema-migration tests expecting a `trade_authority_status` column
  that is already present in the base schema, plus 5 unrelated AST/source-scan antibodies).
- `--validate 2026-07-21` against the live DB: see Acceptance section below for numbers.
