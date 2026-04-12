# Pre-TIGGE repair packet summary

This packet is the **next concrete layer after `zeus_data_improvement_foundation_plus`**.

It is built for the current reality:

- `data-improve` has already added the additive truth substrate and governance cleanup.
- TIGGE is still downloading, so the most valuable work **right now** is non-TIGGE cutover.
- The uploaded DB snapshot still shows split probability truth, missing grouped calibration truth, and missing new additive tables.

## What is included

### 1. DB migration
- `migrations/2026_04_11_pre_tigge_cutover.sql`

### 2. Directly runnable scripts
- `scripts/repair_shared_db.py`
- `scripts/reconcile_probability_truth.py`
- `scripts/backfill_calibration_decision_groups.py`
- `scripts/build_forecast_error_profiles.py`
- `scripts/materialize_day0_residual_features.py`
- `scripts/run_blocked_eval.py`
- `scripts/audit_pre_tigge_readiness.py`

### 3. Helper modules and canonicalization notes
- `src/strategy/market_analysis_family_scan.py` — adapt into the repo as a sibling helper to `MarketAnalysis.find_edges()`.
- `src/state/portfolio_loader_policy.py` — adapt into the repo as an explicit DB-vs-fallback policy helper.
- `src/signal/day0_residual_features.py` — adapt into the repo for point-in-time Day0 features.
- `src/execution/calibration_group_writer.py` — historical helper only; current mainline canonicalizes this through `src/calibration/effective_sample_size.py`.
- Do not reintroduce packet-local `src/state/probability_trace_writer.py` or `src/calibration/blocked_oos_eval.py`; current mainline owns those surfaces through `src/state/db.py` and `src/calibration/blocked_oos.py`.

### 4. Concrete patch fragments
- `patches/01_evaluator_probability_and_family_cutover.md`
- `patches/02_market_analysis_full_family_scan.md`
- `patches/03_harvester_learning_context.md`
- `patches/04_portfolio_partial_stale_guard.md`
- `patches/05_day0_residual_materialization.md`

### 5. Tests
- Adapt packet test intent into the existing repo tests:
  - probability trace tests stay in `tests/test_db.py` / runtime guard tests
  - full-family selection tests stay in `tests/test_fdr.py`
  - Day0 feature tests stay in `tests/test_day0_signal.py`

### 6. Audits from the uploaded DB snapshot
- `artifacts/current_db_audit.csv`
- `artifacts/probability_truth_gap.csv`
- `artifacts/calibration_group_audit.csv`
- `artifacts/calibration_group_anomalies.csv`
- `artifacts/day0_feature_seed_audit.csv`
- `artifacts/forecast_error_profile_seed_sample.csv`
- `artifacts/missing_new_tables.json`

## Validation performed inside this packet

- `pytest tests -q` → 7 passed
- `py_compile` over `src/`, `scripts/`, `tests/` → passed
- dry-run materialization on a copy of the uploaded DB snapshot:
  - `probability_trace_fact` backfilled: 43 rows
  - `calibration_decision_group` backfilled: 11 rows
  - `forecast_error_profile` built: 950 rows
  - `day0_residual_fact` materialized for `target_date >= 2026-04-01`: 7,920 rows
  - `model_eval_run`: 1 row
  - `model_eval_point`: 22,781 rows

## Apply-first recommendation

1. run migration
2. run `repair_shared_db.py`
3. patch evaluator / market_analysis / harvester / portfolio / day0_residual
4. shadow the runtime cutover
5. keep TIGGE expansion as the next packet, not this one
