# full_transport → live p_raw wiring trace (2026-05-25)

# Created: 2026-05-25
# Authority basis: direct code trace of src/engine/monitor_refresh.py + src/calibration/ens_error_model.py + ens_bias_repo.py against the live worktree; cross-checked vs ENS_REFIT_MATH_ROI + ship-mechanics docs.

## Confirmed: live p_raw does NOT apply full_transport (the #1 ship blocker)
- `p_raw_vector_with_error_model` — the function that applies the PredictiveErrorModel (bias `b`, λ SNR-gate, residual sd) — is defined at `src/calibration/ens_error_model.py:113` and has **ZERO callers in `src/`** (only scripts/tests reference it). Verified by grep across `src/`.
- The live p_raw generation site is `src/engine/monitor_refresh.py:453` (fresh-ENS adapter branch) and `:471` (`ens.p_raw_vector(...)` branch). Both call the **plain** `p_raw_vector_from_maxes` / `ens.p_raw_vector` with no error model.
- Therefore live serves **uncorrected** p_raw. full_transport exists only in the offline refit. This makes the train/serve domain-identity invariant currently **violated** if ft Platt were promoted: `p_raw_serve (uncorrected) ≠ p_raw_train (ft-corrected)` → Platt calibration guarantee void.

## Wiring site + shape
At `monitor_refresh.py:453`/`:471`, the wiring is: load a `PredictiveErrorModel` for the bucket `(city, temperature_metric, season[, cycle])`, and when present + flag-on, call `p_raw_vector_with_error_model(error_model=..., member_extrema, city, semantics, all_bins, n_mc=...)` instead of the plain path; else fall back to plain (byte-identical legacy). The downstream calibrator selection (`_monitor_calibrator_for_ens_result`, `:493`) must then resolve the `emf=full_transport_v1` Platt model for the same bucket so train/serve match.

## Data dependency — the open link
The error-model params are produced by `fit_city_predictive_error` / `fit_predictive_error_bucket` (`ens_error_model.py:142,175`) and the store is `model_bias_ens_v2` (`src/calibration/ens_bias_repo.py:28`). But:
- `model_bias_ens_v2` does **not exist** in live `state/zeus-world.db`, live `state/zeus-forecasts.db`, **nor** the refit staging DB `/private/tmp/ens_refit/full.db` (table absent).
- The refit DB's legacy `model_bias` table is **0 rows**.
So the full_transport posteriors the offline MC applied are not in any discoverable persisted table. **For live wiring, these must be fit + persisted into a live-readable `model_bias_ens_v2` (or equivalent) first.**

> STATUS of this link: **MISSING** (probe `FT_POSTERIOR_SOURCE_PROBE_2026-05-25.md`). The 10k-MC refit applied full_transport using posteriors computed **in RAM** (`_native_error_params_for_snapshot` → `fit_city_predictive_error`, on-the-fly per `(city,season,metric)`) inside a **throwaway sub-worktree whose scripts are not on main** (`run_offline_platt_refit.py --error-model full_transport_v1`). The posteriors were used to shift/widen training p_raws and then **discarded** — persisted nowhere. `model_bias_ens_v2` is absent in world.db, 0 rows in forecasts.db, absent in staging. The only writer (`onboard_cities.py:994`) has a **latent bug** (`.posterior.bias` vs dataclass `.bias_c`) so it silently writes nothing. `read_bias_model` (`ens_bias_repo.py:217`) has zero production callers. `rebuild_calibration_pairs.py` on main has no `--error-model-family` flag. **Schema gap**: `model_bias_ens_v2` stores only `posterior_bias_c`/`posterior_sd_c`; reconstructing PredictiveErrorModel via `predictive_error_from_posterior` also needs `residual_sd_c` + `heterogeneity_var_c2` (absent).

### Required work before live wiring is even possible (from the probe)
1. Schema migration: add `residual_sd_c`, `heterogeneity_var_c2` (optionally precomputed `effective_bias_c`, `total_residual_sd_c`, `correction_strength`) to `model_bias_ens_v2`.
2. Fix `onboard_cities._run_fit_ens_bias_v2` (`.posterior.bias` → `.bias_c`) and extend `write_bias_model` to pass the new columns.
3. Add a standalone producer (runs after TIGGE/OpenData updates) that fits + persists the per-bucket posteriors — i.e. port the sub-worktree's offline logic onto main.
4. Promote `full_transport_v1` Platt from `full.db` → live (forecasts.db pairs + world.db platt), selective/ECE-gated, copy-first.
5. Wire `monitor_refresh.py:453` to load the stored model + call `p_raw_vector_with_error_model`, fail-closed to plain when no row exists.

## Calibration gate (context for Platt-vs-p_raw-direct)
`monitor_refresh.py:493-519`: `cal, cal_level = _monitor_calibrator_for_ens_result(...)`; if `cal is not None and len(all_bins) > 1` → Platt vector calibrate; elif `cal is not None` → scalar; (else branch below 519 handles uncalibrated). Whether the live edge/FDR selection blocks uncalibrated (`cal is None`/level≥4) entries before trading — i.e. whether ECE-gated p_raw-direct is tradeable today — is a separate gate downstream in signal/decision (not resolved in this trace; flagged in ship-mechanics §4.10).

## Next-step scope for #64 (wiring)
1. Fit + persist full_transport posteriors per `(city, metric, season)` into a live-readable `model_bias_ens_v2` (pending posterior-source probe).
2. Wire `monitor_refresh.py:453/471` to load the PredictiveErrorModel and branch to `p_raw_vector_with_error_model` under a flag (default OFF = byte-identical).
3. Promote ft `calibration_pairs_v2` (forecasts.db) + ft `platt_models_v2` (world.db) — selective/ECE-gated, on a copy first.
4. Fix the sentinel reader gate (`promote_platt.py:226`, confirmed bug — see SENTINEL_MISMATCH_PROBE) so promotion recognizes the complete refit.
5. Explicit pin of the 44 shipping cohorts; **carve out HK HIGH** (pending HK provenance probe — may be a data-fix instead).
6. Restart → fresh traces → §4.3 bin comparison.
