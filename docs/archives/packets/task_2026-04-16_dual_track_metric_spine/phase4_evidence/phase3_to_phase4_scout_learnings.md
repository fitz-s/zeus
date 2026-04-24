# Phase 3→4 Scout Learnings — Observation & Calibration Pipeline

## 1. Phase 3 Scope Exclusions (Not Flagged, But Load-Bearing for Phase 4)

**Dead code path still imported:**
- `src/main.py:46-50` and `:84-94` — both `wu_daily_collector.collect_daily_highs()` AND `daily_obs_append.daily_tick()` are registered daemon entry points. The comment says wu_daily_collector is deprecated, but it's still wired. Phase 4 should unregister `wu_daily_collector` once Phase 3 `daily_obs_append` is production-verified (currently running dual-write for safety).
- `wu_daily_collector.py:24` — hardcoded WU API key literal (removed from `daily_obs_append.py`). File is dead code pending Phase C deletion but key is still a secret leak if repo is ever public.

**Duplication across backfill lanes (Phase C work):**
- `daily_obs_append.py:198-250` (`_fetch_wu_icao_daily_highs_lows`) is byte-for-byte identical to `scripts/backfill_wu_daily_all.py:217-330`. Same signature, same logic. Phase 4 can assume they remain in sync until Phase C refactor extracts `wu_icao_client.py`.
- Same for HKO: `daily_obs_append.py:341-390` vs `scripts/backfill_hko_daily.py:145-280`.

## 2. Phase 4 Hazards (GRIB→v2 calibration ingest, task #53)

**Critical seam: `rebuild_calibration_pairs_canonical.py` + `src/calibration/store.py`**

- `store.py:55-80` — `add_calibration_pair()` signature has NO `temperature_metric` parameter. All Phase 3 code paths write only to legacy `calibration_pairs(bucket_key=...)` with implicit high-only semantics.
- Phase 4 must add `temperature_metric` to the signature BEFORE calling from `rebuild_calibration_pairs_canonical.py`. The rebuild script is currently single-track; Phase 4's GRIB ingest will produce dual-track snapshots. If the write path isn't ready, Phase 4 will force-write high rows with low physical quantities (silent semantic corruption).
- **Action:** Phase 4 executor must update `add_calibration_pair()` signature to accept `temperature_metric: str`, validate it against the snapshot being processed, and route to `calibration_pairs_v2` once available. Current script queries only legacy `calibration_pairs` (line 201, 205, 252).

**Orchestration anti-pattern:**
- `rebuild_calibration_pairs_canonical.py:201-205` — reads from `ensemble_snapshots` (legacy), NOT `ensemble_snapshots_v2`. Phase 4 schema work may create v2 tables, but the rebuild script will still be reading legacy. This is correct for Phase 4A (high-track only on old snapshots), but Phase 4B+ must bifurcate the rebuild into two paths (high from legacy snapshots, low from v2 snapshots) with separate decision groups.

**Savepoint isolation pattern:**
- `store.py` uses no transaction isolation; callers own savepoint logic (cf. `daily_obs_append.py` S1-2 pattern). Phase 4's dual-track ingest must maintain this pattern — wrap the dual-metric pair writes in ONE savepoint so both succeed or both roll back. If high commits before low, reconciliation breaks.

## 3. Cross-Phase Patterns (Applicable to Phase 5–7)

**Error authority pattern:**
- Phase 3 uses explicit exception types (`ObservationUnavailableError`, `MissingCalibrationError`) as first-class truth. Phase 4's GRIB ingest should use `DataVersionQuarantinedError` (already defined in `src/contracts/ensemble_snapshot_provenance.py:22`). Phase 5+ (low historical lane) should define `LowCausalityIneligibleError` for non-OK causality_status slots.

**Fail-closed vs. silent degrade:**
- Phase 3 observation layer: raises on missing low_so_far (fail-closed). Phase 4 calibration: should raise on missing or mismatched `temperature_metric` on dual writes, not silently default to high. Pattern reusable for Phase 6 (Day0 split).

**Availability-first truth (INV-09):**
- Every data ingest (observations, forecasts, calibration) must record explicit `availability_fact` rows, not silence gaps. Phase 4's GRIB ingest should call `record_written()` / `record_failed()` (cf. `data_coverage.py`) for every snapshot batch, not just on error. This is load-bearing for Phase 6's gap analysis.

## 4. Dead Code & Phase 4 Simplification Candidates

**Candidate for Phase 4 cleanup:**
- `scripts/generate_calibration_pairs.py` — the legacy harvester-driven calibration writer. `rebuild_calibration_pairs_canonical.py` is its replacement. Once Phase 4's canonical rebuild is production-verified (all high-track pairs migrated), `generate_calibration_pairs.py` can be deleted. Current lines: ~400. Blocker: must verify no live code depends on its output format.

**Dual-write cleanup window:**
- Phase 3 runs both `wu_daily_collector` and `daily_obs_append` to same table for safety. Once Phase 3 is production-stable (1–2 weeks operator sign-off), Phase 4 can disable `wu_daily_collector` in `src/main.py` entry points. File deletion is Phase C.

## 5. Forward Risks for Phase 5–7

**Phase 5 (low historical lane):**
- Every calibration-pairs query in the codebase currently assumes single-track. `src/calibration/manager.py` (season_from_date, etc.) and `src/signal/ensemble_signal.py` (p_raw_vector_from_maxes) will need metric-aware branching. Phase 5's low-track rebuild must not call the high-track `add_calibration_pair()` directly — will need dual-track overload or parameter injection.

**Phase 6 (Day0 low split):**
- The nowcast path (low_so_far + current_temp + hours_remaining) is not yet in the evaluator. Evaluator.py:800 rejects low decisions cleanly for now. Phase 6 must implement the nowcast decision path without touching the high-track Day0Signal. Parallel implementation, not refactor.

**Phase 7+ (Platt fit-tuning):**
- `platt_models` table is currently single-track. Phase 4's v2 schema must add `temperature_metric` key. Every Platt fit in Phase 7 must segregate high and low training data at the cluster/season level. Current fitting code (cf. `src/calibration/platt.py`, `src/calibration/calibration_builder.py`) assumes one family per (season, cluster). Will need dual-family families.

---

**Top 3 findings for Phase 4 exec-bob:**

1. **`add_calibration_pair()` must be dual-metric-aware** before Phase 4 GRIB ingest writes — no `temperature_metric` parameter today; will corrupt low rows if added after-the-fact.

2. **Dual-write safety window closing** — Phase 3's dual wu_daily_collector/daily_obs_append can disable one lane once confirmed stable (operator sign-off).

3. **Savepoint isolation pattern is correct** — reuse daily_obs_append's S1-2 pattern for Phase 4's dual-metric pair writes. Both high + low must succeed/fail as unit.
