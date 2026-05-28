# FT Posterior Source Probe — 2026-05-25

**Question**: Where do full_transport posteriors come from during the offline 10k-MC
refit, where are they persisted, and is the live-wiring data dependency satisfied?

---

## 1. How the offline refit APPLIED full_transport during the 10k-MC

The 10k-MC refit ran via the **sub-worktree** at
`.claude/worktrees/ens-bias-hierarchical/.claude/worktrees/agent-a1820f998a1667eb4/`
using scripts that do NOT exist on the main branch.

**Entry point**: `run_full_refit_and_validate.sh` called:
```
run_offline_platt_refit.py --db /tmp/ens_refit/full.db --error-model full_transport_v1
run_offline_calibration_rebuild.py --db /tmp/ens_refit/full.db --error-model full_transport_v1
```

**Mechanism** (`rebuild_calibration_pairs_v2.py` in the sub-worktree, lines 200–250):

`_native_error_params_for_snapshot()` is called per-snapshot when `error_model_family='full_transport_v1'`.
It calls `fit_city_predictive_error()` from `src/calibration/ens_error_model.py` directly
(line 233) against the **isolated staging DB** (`/tmp/ens_refit/full.db`), which was seeded
from the live source tables. The DB supplies the TIGGE prior residuals + OpenData live residuals
needed by `fit_city_predictive_error`. The resulting `PredictiveErrorModel` is **cached per
`(city, season_label, metric)`** for the lifetime of the rebuild call (cache lives in RAM,
not persisted).

**Data versions used** (sub-worktree `rebuild_calibration_pairs_v2.py:162–177`):
- `high` live: `ecmwf_opendata_mx2t3_local_calendar_day_max_v1`
- `high` prior: `tigge_mx2t6_local_calendar_day_max_v1`
- `low` live: `ecmwf_opendata_mn2t3_local_calendar_day_min_v1`
- `low` prior: `tigge_mn2t6_local_calendar_day_min_v1`
- `min_live_n = 5` (below the #334 default of 20)

The `PredictiveErrorModel` fields `effective_bias_c` and `total_residual_sd_c` are extracted,
converted to native unit (×1.8 for degF cities), and injected into `p_raw_vector_from_maxes`
via `extra_member_sigma` and a pre-MC member shift. The resulting corrected `p_raw` is then
written to `calibration_pairs_v2` with `error_model_family='full_transport_v1'`.

**There is no `--error-model-family` flag on main-branch `rebuild_calibration_pairs_v2.py`.**
The sub-worktree added this flag and the associated plumbing. The main-branch script
(`scripts/rebuild_calibration_pairs_v2.py:111, 1239`) uses plain `p_raw_vector_from_maxes`
with no error model.

---

## 2. Where the fitted full_transport posteriors are PERSISTED

**Short answer: NOWHERE.**

The posteriors are computed on-the-fly from the source residuals and cached **in-RAM** for
the duration of the rebuild call. They are NOT written to any table.

Verified by scanning every DB in `/private/tmp/ens_refit/`:

| DB | `model_bias_ens_v2` table exists? | Row count |
|----|-----------------------------------|-----------|
| `full.db` | NO (`model_bias` only) | — |
| `sf.db`, `sf_offA.db`, `sf_offB.db`, `sf_on.db`, `sf_timing.db` | NO | — |
| `subset.db` | NO | — |

Live DBs:

| DB | `model_bias_ens_v2` table exists? | Row count |
|----|-----------------------------------|-----------|
| `state/zeus-forecasts.db` | YES (schema present) | **0** |
| `state/zeus-world.db` | NO (`model_bias` only) | — |

The `model_bias_ens_v2` schema (`ens_bias_repo.py:28–53`) is defined and `init_ens_bias_schema()`
creates the table in `zeus-forecasts.db`, but no producer has ever run a write against it in
the live system.

`onboard_cities.py` (`scripts/onboard_cities.py:994–1068`) is the only production code that
calls `write_bias_model()`. It calls `fit_city_predictive_error()` and tries to access
`model.posterior.bias` — but `fit_city_predictive_error` returns a `PredictiveErrorModel`
dataclass (fields: `bias_c`, `bias_sd_c`, …), which has no `.posterior` attribute. This would
raise `AttributeError` at runtime, causing the `except (ValueError, RuntimeError)` block to
silently skip every bucket. The table remains empty. This is a latent bug, not a known gap.

**What calibration_pairs_v2 in full.db DOES contain** (verified by SQL):
- `error_model_family='full_transport_v1'`: 17,557,206 rows
- `error_model_family='none'`: 36,863,670 rows
- `platt_models_v2` with `error_model_family='full_transport_v1'`: 160 rows (is_active=1, authority=VERIFIED)

These Platt models are the output of the 10k-MC refit and DO encode the full_transport
correction — but only because the pairs that trained them were already corrected p_raws
(the posteriors were used to transform the training data, not stored separately).

---

## 3. Who calls fit_city_predictive_error / writes model_bias_ens_v2 today

**fit_city_predictive_error callers** (production code only, confirmed by grep):
- `scripts/onboard_cities.py:998` — called during city onboarding, but broken at runtime
  (`.posterior` attribute error, see above). No live invocations have succeeded.
- Sub-worktree `rebuild_calibration_pairs_v2.py:233` — called during offline staging
  rebuild only; not on the main branch; not a live production path.

**write_bias_model callers**:
- `scripts/onboard_cities.py:1029` — the only caller, broken at runtime as described above.

**model_bias_ens_v2 is offline-only and empty in all live systems.**

`ens_bias_repo.py` has no wiring to any live path beyond the `onboard_cities` write site.
`read_bias_model()` is defined but has zero callers in production code. Monitor_refresh
(`src/engine/monitor_refresh.py:453`) uses plain `p_raw_vector_from_maxes` with no error
model connection.

---

## 4. What must be persisted + loaded for live wiring

For `monitor_refresh` to build a `PredictiveErrorModel` per `(city, metric, season)`:

**Required table** (already schema-present in `zeus-forecasts.db`, zero rows):

```
model_bias_ens_v2 (city, season, metric, live_data_version, ...)
  posterior_bias_c  REAL  — bias_c from PredictiveErrorModel
  posterior_sd_c    REAL  — bias_sd_c from PredictiveErrorModel
```

**Additionally required** — columns NOT currently in `model_bias_ens_v2` schema:

| Column | Needed for |
|--------|-----------|
| `residual_sd_c` | `PredictiveErrorModel.residual_sd_c` → `total_residual_sd_c` |
| `heterogeneity_var_c2` | `PredictiveErrorModel.heterogeneity_var_c2` |
| `correction_strength` | λ = `PredictiveErrorModel.correction_strength` |
| `effective_bias_c` | precomputed `λ·bias_c` |
| `total_residual_sd_c` | precomputed MC spread |
| `disagreement_high` | flag |

The current schema (`ens_bias_repo.py:28–53`) stores only `posterior_bias_c` and
`posterior_sd_c`. The live path would need to reconstruct the full `PredictiveErrorModel`
from these, which requires also knowing `heterogeneity_var_c2` (needed to compute
`correction_strength` and `total_residual_sd_c`). The schema is incomplete for the live
read path.

**Required producer**: A standalone `fit_ens_bias_v2.py` (or repaired `onboard_cities`
path) that:
1. Calls `fit_city_predictive_error()` per `(city, metric, season)`
2. Writes ALL fields needed for `predictive_error_from_posterior()` reconstruction —
   at minimum: `posterior_bias_c`, `posterior_sd_c`, `residual_sd_c`,
   `heterogeneity_var_c2` (not currently in schema)
3. Targets `zeus-forecasts.db` via `init_ens_bias_schema` + `write_bias_model`
4. Runs on an operator-approved schedule (after each TIGGE/OpenData update)

**monitor_refresh wiring** requires:
1. Load row from `model_bias_ens_v2` by `(city, metric, season, live_data_version)`
2. Reconstruct `PosteriorBias` and call `predictive_error_from_posterior(posterior, residual_sd_c)`
   — or store the pre-computed `effective_bias_c` + `total_residual_sd_c` directly
3. Replace `p_raw_vector_from_maxes` call at `monitor_refresh.py:453` with
   `p_raw_vector_with_error_model(member_extrema, error_model, ...)`

---

## VERDICT

**MISSING — data dependency NOT satisfied.**

The live-wiring has three compounding blockers:

1. **model_bias_ens_v2 is empty in zeus-forecasts.db** (0 rows). The table exists but no
   producer has ever successfully written to it. The only write site (`onboard_cities.py`)
   has a latent AttributeError that silently skips every bucket.

2. **model_bias_ens_v2 schema is incomplete** for the live read path: it stores
   `posterior_bias_c` and `posterior_sd_c` but lacks `residual_sd_c` and
   `heterogeneity_var_c2` — which are required inputs to `predictive_error_from_posterior()`
   and cannot be recovered from what is stored.

3. **No full_transport Platt models exist in any live DB.** The `platt_models_v2`
   `error_model_family='full_transport_v1'` rows are in the staging DB (`/tmp/ens_refit/full.db`)
   only. They have never been promoted to `zeus-forecasts.db` or `zeus-world.db`.

The offline 10k-MC correctness (full.db, 160 full_transport Platt models, 17.6M corrected pairs)
is intact. But everything needed to reproduce the correction at live inference time —
the per-(city,metric,season) PredictiveErrorModel parameters, the corrected Platt models,
and the monitor_refresh call path — remains absent from the live system.

Before live wiring can proceed: (a) schema migration to add missing columns, (b) a producer
script that correctly writes all PredictiveErrorModel fields, (c) Platt promotion from full.db
to live, and (d) monitor_refresh wiring with a fail-closed fallback when no row exists.
