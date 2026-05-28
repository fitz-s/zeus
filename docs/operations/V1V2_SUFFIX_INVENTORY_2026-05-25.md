# v1/v2 Suffix Inventory — Eradication Feasibility (2026-05-25)

Authority: live-DB + grep inventory (agent ae2c0e7bdd702985d), cross-checked against `architecture/db_table_ownership.yaml` (the ground-truth classifier, which already carries drop-after dates).

## Conclusion: suffix-eradication is NOT a blind rename
Most `_v2` names are **K1 DB-split canonical stores** (world.db ↔ forecasts.db), not confusing duplicates. Several "bare" names have 0 rows but live code still reads them (dead data paths). Several "pairs" are NOT migration pairs at all. Renaming wrong = production crash. The ownership YAML is the work queue.

## Classification

### A. Confirmed dead → safe to drop (per YAML drop dates)
- `historical_forecasts` (bare, world.db, 69,660 rows, no INSERT in src/, YAML legacy_archived, **drop after 2026-08-09**) — the ONLY unambiguous drop.
- `ensemble_snapshots` (bare) — already DROPPED from world.db (v1.F20); only `ensemble_snapshots_v2` (forecasts, 1.1M rows) lives. db.py reads are `_table_exists`-guarded dead paths.

### B. v2 is canonical, bare is 0-rows-but-live-read (resolve before touching)
- `calibration_pairs_v2` (forecasts, **91M rows**) canonical; bare `calibration_pairs` 0 rows yet read by drift_detector/blocked_oos/effective_sample_size → silent empty reads (dead path OR bug — needs trace).
- `platt_models_v2` (world, 1,406 rows) canonical; bare `platt_models` 0 rows, legacy save/load paths present but empty.
- `market_events_v2` (forecasts, 15,551 rows) canonical; bare `market_events` 0 rows, read by replay.py for range_labels (dead-data fallback?).

### C. BOTH live — NOT migration pairs (do NOT collapse)
- `observation_instants` (960k, OpenMeteo filler, ungated, YAML "NOT legacy") vs `observation_instants_v2` (1.87M, WU/HKO native, gated) — designed **dual-tier**.
- `settlements` (6,427, ingest write target) vs `settlements_v2` (4,789, calibration-replay authority) — split by role, both in forecasts.db.
- `MarketAnalysis` (live trading) vs `MarketAnalysisVNext` (parallel measurement) — additive, not replacement.
- `retrain_trigger.py` (full refit) vs `retrain_trigger_v2.py` (drift signal writer) — distinct responsibilities.

### D. `_v1`/`_v2` that are semantic tags, NOT duplicates (KEEP)
`data_version='v1.wu-native'` (1.8M live rows), `wu_icao_history_v1`/`hko_daily_api_v1`/`ogimet_metar_v1`, `deid_v1_`/`dgid_v1_`/`nei_v1_` hash-namespace prefixes, `blocked_oos_v1`/`hpf_v1`/`empirical_bayes_shrinkage_v1` estimator versions, `corrected_executable_cost_v1`, `uma_oo_v2`, `contract_window_v2`, `v2_city_floors.json`/`v2_nstar.json` (no v1 counterpart). These name provenance/algorithm identity — eradicating them breaks provenance or destroys algorithm-versioning.

### E. In-progress, classify before touching
- `model_bias` (165 rows, live read by evaluator/signal) vs `model_bias_ens_v2` (0 rows, not in ownership YAML, only ens_bias_repo touches it) — v2 is an unfinished feature table, NOT a replacement.

## 4 items needing runtime-trace/operator decision before any drop
1. settlements vs settlements_v2 dual-write intent.
2. replay.py bare `market_events` read (dead vs fallback).
3. drift/ess/blocked_oos reads on 0-row bare `calibration_pairs` (dead vs bug — possible real defect: should they read `_v2`?).
4. model_bias_ens_v2 classification (finish or remove).

## Recommended sequencing (zero production impact)
1. Resolve the 4 trace items.
2. Drop only YAML-confirmed-dead tables on their scheduled dates (historical_forecasts after 2026-08-09).
3. For category-B, after confirming the bare-table reads are dead: migrate readers to `_v2`, then drop bare + rename `_v2`→bare behind a schema-version bump + coordinated daemon restart.
4. Leave categories C/D entirely (not duplicates / semantic tags).
5. Item #3's "0-row calibration_pairs read" may be a live BUG worth fixing regardless of the rename.
