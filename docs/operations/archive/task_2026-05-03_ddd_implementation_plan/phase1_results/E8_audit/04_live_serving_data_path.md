# E8 Audit — Live serving data path exposure

Created: 2026-05-03
Authority: read-only code/SQL audit (haiku-D)

## Headline

- Live engine reads platt_models_v2:  **YES**, via `load_platt_model_v2`
- Live engine reads calibration_pairs_v2 directly: **NO** (indirectly via `get_pairs_for_bucket` on legacy table, or via `list_active_platt_models_v2` for monitoring)
- Snapshot freeze logic detected:    **YES**, at the ingest contract level, but **NO** explicit `FROZEN_AS_OF` filter in the runtime calibration read path.
- Verdict: live trading is **EXPOSED** to bulk-regen leakage if `is_active=1` and `authority='VERIFIED'` are applied to newly fitted models without a corresponding `data_version` shift or version-locked evaluation.

## All references to v2 calibration/observation tables in src/

| File:Line | Table | Query Type | Classification |
|-----------|-------|------------|----------------|
| `src/calibration/store.py:233` | `calibration_pairs_v2` | INSERT | INGEST |
| `src/calibration/store.py:502` | `platt_models_v2` | INSERT | INGEST |
| `src/calibration/store.py:540` | `platt_models_v2` | DELETE | INGEST (Cleanup before refit) |
| `src/calibration/store.py:628` | `platt_models_v2` | SELECT | **LIVE_SERVING** (via `load_platt_model_v2`) |
| `src/calibration/store.py:724` | `platt_models_v2` | SELECT | MONITORING / STATE (via `list_active_platt_models_v2`) |
| `src/contracts/world_view/observations.py:56` | `observation_instants_v2` | SELECT | **LIVE_SERVING** (via `get_latest_observation`) |
| `src/calibration/drift_detector.py:73` | `platt_models_v2` | SELECT | MONITORING |
| `src/data/observation_instants_v2_writer.py:417` | `observation_instants_v2` | INSERT | INGEST |

## Live evaluator call chain (calibrator load path)

evaluate_candidate@src/engine/evaluator.py:1844
  → get_calibrator@src/calibration/manager.py:187
    → load_platt_model_v2@src/calibration/store.py:628
      → SQL: `SELECT ... FROM platt_models_v2 WHERE temperature_metric = ? AND cluster = ? AND season = ? AND data_version = ? AND input_space = ? AND is_active = 1 AND authority = 'VERIFIED' ORDER BY fitted_at DESC LIMIT 1`

## WHERE-clause analysis

For each LIVE_SERVING query of platt_models_v2 / calibration_pairs_v2:
- **`src/calibration/store.py:628`** (load_platt_model_v2)
  - **WHERE clause**: `WHERE temperature_metric = ? AND cluster = ? AND season = ? AND data_version = ? AND input_space = ? AND is_active = 1 AND authority = 'VERIFIED' ORDER BY fitted_at DESC LIMIT 1`
  - **protection class**: **VERSION_FILTER** (via `data_version`)
  - **leakage exposure**: **MEDIUM**. While it filters by `data_version`, if the bulk-regen is performed under the *same* `data_version` string (e.g. `v1`) and marked `is_active=1`, the live engine will pull the newest fit.
- **`src/contracts/world_view/observations.py:56`** (get_latest_observation)
  - **WHERE clause**: `WHERE city = ? AND target_date = ? ORDER BY utc_timestamp DESC LIMIT 1`
  - **protection class**: **NONE**
  - **leakage exposure**: **HIGH** (reads whatever the latest ingested observation is for that city/date).

## Snapshot/freeze logic search

- `src/contracts/snapshot_ingest_contract.py:43`: `validate_snapshot_contract(payload: dict)` — Gating logic for incoming snapshots (quarantine/boundary checks).
- `src/contracts/semantic_types.py:134`: `Decision-time snapshot identity` — `snapshot_id` is passed through the pipeline but primarily used for attribution and logging, not for point-in-time database reconstruction at read-time.
- `src/calibration/store.py:210`: `snapshot_id` is stored in `calibration_pairs_v2` but is NOT used in the `load_platt_model_v2` or `get_pairs_for_bucket` filters.

## Conclusion

The live trading engine is vulnerable to bulk-refit leakage if the refit process populates `platt_models_v2` with the same `data_version` currently expected by the `MetricIdentity` (e.g., `v1`). While the engine has a `data_version` filter, it lacks a true temporal "freeze" or "snapshot" filter at the SQL level (e.g., `recorded_at <= ?`). 

**Recommended structural fix**: The engine should either (a) pin a specific `fitted_at` or `model_key` in the live configuration (hard-pinning), or (b) the bulk-refit process must use a *new* `data_version` string that is NOT yet active in the live engine's `MetricIdentity` until explicitly authorized.

DONE: /Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results/E8_audit/04_live_serving_data_path.md
