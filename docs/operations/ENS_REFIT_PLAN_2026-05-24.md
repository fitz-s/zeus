# ENS full_transport_v1 Calibration REFIT — Plan / ARCH_PLAN_EVIDENCE

Created: 2026-05-24
Authority basis: operator task "Zeus 10k-MC full_transport calibration REFIT" 2026-05-24.
Predictive-error model merged in #336 (`src/calibration/ens_error_model.py`).

## Goal
Rebuild `calibration_pairs_v2` applying the universal predictive-error model
(location + scale + SNR-gate + 0.5→0.25 transport), refit Platt on the
corrected pairs, validate OOS. Flag-gated; OFF = byte-identical to current main.
NO live DB writes, NO daemon restart, NO `bias_correction_enabled` flip.

## Scope of edits (scripts + schema; reversible additive)
- `src/state/schema/v2_schema.py`: idempotent ADD COLUMN `error_model_family TEXT
  NOT NULL DEFAULT 'none'` on `calibration_pairs_v2` + `platt_models_v2`
  (mirror cycle/source_id/horizon_profile pattern). Default 'none' = legacy
  byte-identical. NOT added to UNIQUE (SQLite cannot ALTER UNIQUE; one family
  per rebuild scope; destructive delete is keyed on bin_source).
- `src/calibration/store.py`: thread `error_model_family` through
  `add_calibration_pair_v2` (graceful degrade if column absent) and concat into
  `save_platt_model_v2` model_key.
- `scripts/rebuild_calibration_pairs_v2.py` + `_rebuild_calibration_pairs_v2_parallel.py`:
  `--error-model full_transport_v1` CLI flag. When set, fit a bucket
  `PredictiveErrorModel` per (city, season, metric) via
  `fit_city_predictive_error`, convert effective_bias_c / total_residual_sd_c to
  members' native unit (`city.settlement_unit`: ×1.8 for degF), subtract bias
  pre-MC and widen the MC draw by extra sigma. OFF → unchanged code path.
- `scripts/refit_platt_v2.py`: `--error-model` flag; stamp family into model_key;
  filter pairs by family. (refit_platt_v2 is the v2 path; refit_platt.py is the
  legacy `calibration_pairs`→`platt_models` path and is NOT used.)
- `src/calibration/ens_bias_model.py::assert_bias_state_consistent`: extend with
  `live_error_model_family` / `active_platt_error_model_family` params so live
  correction also requires the active Platt's family to match.

## Reversibility
TRUTH_REWRITE class is for the *output rows*, which are written ONLY to an
isolated staging DB (`--db <iso.db>`); the live `state/zeus-forecasts.db` and
`state/zeus-world.db` are read READ-ONLY. The script + schema edits are additive
(new column default 'none', new opt-in flag). Flag-OFF run is byte-identical to
current main (proven in STEP 2). No promotion, no merge, no activation in scope.

## Isolation
Lean staging DB seeded with `ensemble_snapshots_v2` (+ members_unit),
`observations`, `settlements_v2` copied from the live forecasts DB, plus the v2
schema write targets. All rebuild reads + error-model fit + Platt refit run
against this single isolated DB.
