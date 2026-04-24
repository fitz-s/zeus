# Scout Dave Final Dump — Phase 4 Reconnaissance & Handoff

**Author**: scout-dave  
**Date**: 2026-04-16  
**Purpose**: Maximum-value knowledge transfer before retirement. Directory cheat sheet, hazard taxonomy, dead-code candidates, reading heuristics, Phase 5+ checklist.

---

## 1. Directory-Level Cheat Sheet

### `src/data/`
**What lives here**: Observation fetchers (WU, IEM ASOS, Open-Meteo), daily append logic, settlement sources.

**Key files**:
- `observation_client.py` — fetches current_temp, high_so_far (no low_so_far yet); providers raise on failure (fail-closed).
- `daily_obs_append.py` — appends daily highs/lows to observations table. Post-Phase-3: reads cities.json (single source of truth), no local CITY_STATIONS map. WU + HKO writers.
- `wu_daily_collector.py` — **deprecated** (Phase 3 replaced with daily_obs_append.py); still wired in src/main.py line 249; hardcoded WU API key line 24.
- `*_append.py` pattern — intentional decoupling from backfill scripts; do not consolidate with `scripts/backfill_*.py`.

**Dead code**: wu_daily_collector.py (full file; Phase 4 chore candidate).

**Fragile**: None. Daily_obs_append authority chain is solid post-Phase-3.

---

### `src/engine/`
**What lives here**: Decision loops, evaluator (observation consumer), signal routing.

**Key files**:
- `evaluator.py` — entry point for candidate evaluation. Low rejection gate at line 800: if `temperature_metric.is_low()` and `candidate.observation.get("low_so_far") is None` → reject. Phase 3 unblocked this gate (low_so_far now provided).
- `monitor_refresh.py`, `day0_signal.py`, `day0_window.py` — signal-path integrators.

**Dead code**: None detected.

**Fragile**: Observation gate at line 800. Must remain synchronized with observation_client.py output contract. Phase 5 low track will hit this gate (ensure low_so_far is populated).

---

### `src/state/`
**What lives here**: Database schema (v1 + v2), canonical write pattern, state reconciliation.

**Key files**:
- `db.py` — god-object (3864 lines); contains all DDL (legacy v1), all writers, all queries. Phase 4 does NOT split this (deferred chore).
- `schema/v2_schema.py` — new; v2 DDL lives here (post-Phase-2). Both `zeus-world.db` and `zeus_trades.db` get v2 schema.
- `canonical_write.py` — DT#1 immune system; enforces commit-before-export contract. `commit_then_export()` is the structural antibody against phantom writes.
- `chain_reconciliation.py`, `decision_chain.py` — state tracking.

**Dead code**: None. v1 schema remains in db.py for legacy table support.

**Fragile**: `canonical_write.py` is **critical**. DT#1 (death-trap ordering) is impossible by construction here. Do not refactor this file lightly. All writers must route through `commit_then_export()` or explicitly call `conn.commit()` with documented justification (e.g., `# INFO(DT#1): authoritative write, not derived export`).

---

### `src/signal/`
**What lives here**: Probability computation (P_raw, Platt calibration, market fusion).

**Key files**:
- `ensemble_signal.py` — `p_raw_vector_from_maxes()` function (byte-identical in training + live inference; 10k Monte Carlo iterations, sensor noise σ, WMO half-up rounding per member).
- `day0_signal.py`, `day0_window.py` — Day0 specifics; nowcast path for low track (Phase 6).

**Dead code**: None.

**Fragile**: `p_raw_vector_from_maxes()` is the gold-standard seam. Any change must be tested against both training (rebuild_calibration_pairs_canonical.py) and inference (live runtime). Never change this function without full rebuild + refit cycle.

---

### `src/strategy/`
**What lives here**: Edge selection, Kelly sizing, position management.

**Key files**:
- `selection_family.py` — edge ranking, FDR gates.

**Dead code**: None detected.

**Fragile**: FDR logic depends on Platt calibration quality downstream. Phase 4 ensures high track; Phase 5 ensures low track. Both must reach platt_models with clean authority chain.

---

### `src/calibration/`
**What lives here**: Calibration storage, Platt model fitting, decision groups, bin structure.

**Key files**:
- `store.py` — `add_calibration_pair()` function (lines 55–80). **Missing parameters as of Phase 4A.0 gate**: temperature_metric, training_allowed, data_version. Phase 4A.0 hotfix must expand signature.
- `platt.py` — ExtendedPlattCalibrator (sklearn LogisticRegression wrapper + bootstrap).
- `effective_sample_size.py` — decision_group building (de-duplication + correlation removal).
- `manager.py` — maturity_level → regularization mapping.

**Dead code**: None.

**Fragile**: `add_calibration_pair()` signature. Phase 4A.0 must update **all call sites** in rebuild_calibration_pairs_canonical.py + any others (grep for "add_calibration_pair" to find all).

---

### `src/contracts/`
**What lives here**: Provenance guards, type wrappers, settlement semantics.

**Key files**:
- `ensemble_snapshot_provenance.py` — `assert_data_version_allowed()`, `is_quarantined()`, `DataVersionQuarantinedError`. Quarantined prefixes: `tigge_step*`, `tigge_param167*`, `tigge_2t_instant*`. Future quarantine: `tigge_mx2t6_local_peak_window_max_v1`.
- `calibration_bins.py` — `grid_for_city()`, `validate_members_unit_plausible()`, `validate_members_vs_observation()`.
- `settlement_semantics.py` — high_temp vs low_temp observation field mapping.

**Dead code**: None.

**Fragile**: `ensemble_snapshot_provenance.py` is the write-side gate. Every future ingest script (Phase 4B, Phase 5, etc.) MUST call `assert_data_version_allowed()` before INSERT. The guard is a fail-closed design.

---

### `src/types/`
**What lives here**: Type wrappers, enums, identity objects.

**Key files**:
- `metric_identity.py` — **new file** (Phase 4). Temperature metric typed as `MetricIdentity` (not bare "high"/"low" strings). Enforces unit consistency at seams.

**Dead code**: None.

**Fragile**: This file is the anti-slop barrier. Bare string "high" / "low" / "high_temp" at system boundaries are now TypeError. Do not allow callers to pass untyped strings.

---

### `scripts/`
**What lives here**: Standalone utilities, backfills, migrations, data extracts.

**Key files**:
- `ingest_grib_to_snapshots.py` — **stub** (56 lines); Phase 4B implementation task #53. Must call `assert_data_version_allowed()` before ensemble_snapshots_v2 INSERT.
- `rebuild_calibration_pairs_canonical.py` — high-track rebuild (280 lines). Reads ensemble_snapshots (legacy v1, not v2). Will bifurcate into rebuild_calibration_pairs_low.py in Phase 5.
- `refit_platt.py` — refits platt_models from calibration_pairs. K4 authority filtering (lines 70–79); hard-deletes K3 soft-deleted rows (lines 163–169).
- `backfill_wu_daily_all.py` — **deprecated** (Phase 3 responsibility moved to daily_obs_append.py); contains byte-identical CITY_STATIONS map at line 217–330.
- `oracle_snapshot_listener.py` — **deprecated** (parallel CITY_STATIONS map); dead-code chore candidate.
- `migrate_rainstorm_full.py` — self-reports COMPLETE; called at src/main.py:249; Phase 2 marked for deletion.

**Dead code**: wu_daily_collector.py, migrate_rainstorm_full.py (Phase 2 chore not completed yet), parts of backfill_wu_daily_all.py (CITY_STATIONS duplication).

**Fragile**: ingest_grib_to_snapshots.py stub. Phase 4B must implement the real ingester with full provenance chain (data_version, members_unit, boundary_ambiguous for low track).

---

### `tests/`
**What lives here**: Unit tests, integration tests, regression suites.

**Large files** (>2000 lines, thematically coherent):
- `test_runtime_guards.py` (5307 lines) — comprehensive runtime behavior tests.
- `test_db.py` (3011 lines) — database layer tests.

**Phase 4 additions**:
- `test_phase4_ingest.py` — R-I through R-P gates (testeng-emma landed these RED).
- `test_phase4_platt_v2.py` — family isolation, authority chain, bucket_key scoping.

**Dead code**: None. Test files are load-bearing.

---

## 2. Hazard Taxonomy & 5-Minute Spotting Rules

### Category H: Authority Provenance Breaks
**Signature**: Data flows through a write without `authority` column or without explicit authority check.

**5-min spotting**:
1. Grep the file for INSERT / UPDATE / REPLACE.
2. Check if the target table has `authority` column (PRAGMA table_info).
3. Check if the source data has a documented `authority` value.
4. If INSERT happens without authority context → H hazard.

**Examples**: Phase 2 C1 (model_skill DROP); Phase 3 observation_client (low_so_far missing); Phase 4 add_calibration_pair (no temperature_metric).

---

### Category M: Semantic Data Contamination
**Signature**: Two distinct physical quantities (mx2t6 vs mn2t6, high vs low, Kelvin vs degC) are accidentally mixed in one table/field.

**5-min spotting**:
1. Look for plural buckets in one table (e.g., calibration_pairs has high + low rows before v2 schema split).
2. Check if there's a discriminant column (temperature_metric, unit, physical_quantity).
3. If rows from different physical quantities share a key (e.g., bucket_key) → M hazard.

**Examples**: Phase 2 M3 (platt_models_v2 city/target_date pollution); Phase 4 H4 (bucket_key high/low scoping).

---

### Category C: Internal Commits Defeat Rollback
**Signature**: A function calls `conn.commit()` internally, breaking the `commit_then_export()` contract.

**5-min spotting**:
1. Grep the function for `conn.commit()` or `conn.rollback()`.
2. Check if the function is called from `commit_then_export()` or other high-level orchestrators.
3. If internal commit exists → C hazard (defer fix until transaction boundary is clear).

**Examples**: Phase 2 C2 (store_artifact pre-commits); Phase 3 fixed (7 standalone callers audited).

---

### Category INV: Invariant Enforcement
**Common INVs**:
- **INV-09** (fail-closed): Providers raise exception if required field unavailable (never silently None).
- **INV-14** (typed identity): Temperature metric is MetricIdentity, not string.
- **INV-15** (parameter completeness): add_calibration_pair() includes temperature_metric, training_allowed, data_version.

**Spotting rule**: Look for function signatures that are "incomplete" for dual-track (missing a discriminant field) or for provenance (missing authority/data_version).

---

## 3. Dead-Code & Simplification Candidates (Phase 5–7 Chore Bundle)

### Safe to Bundle (Low Interdependency)
1. **wu_daily_collector.py** (full file removal) — Phase 3 completed daily_obs_append.py replacement.
2. **migrate_rainstorm_full.py** (delete + remove src/main.py:249 call) — Phase 2 marked complete but never cleaned up.
3. **Peak-window references in docs** (post-Phase-4B) — diagnostic-only, no live code path.
4. **K3 soft-deleted platt_models rows** (already hard-deleted by refit_platt.py line 165–169; chore is documentation cleanup).

### Requires Careful Sequencing (Cross-File Dependencies)
1. **CITY_STATIONS duplication** (backfill_wu_daily_all.py:217–330 + oracle_snapshot_listener.py) — Phase 3 moved truth to cities.json. Safe to delete both after Phase 5 completes (no more backfill runs needed). **DO NOT delete until Phase 5 confirms no active backfill jobs.**
2. **db.py god-object split** (deferred from Phase 2) — candidate modules: `schema/v2_schema.py` (done), `canonical_write.py` (done), `writers/*.py`, `queries/*.py`. **Chore-only; do not block Phase 4–7 work.**
3. **test file splits** (test_runtime_guards.py, test_db.py >2000 lines) — thematically coherent; split only if test runtime becomes a bottleneck. **Not urgent.**

### Defer to Phase 7 (Low-Priority, No Blocking)
- Duplicate calibration_decision_group DDL (db.py:294–307 vs db.py:841–857); chore cleanup, not load-bearing.
- Orphan utility candidates (scripts/parse_change_log.py, scripts/venus_autonomy_gate.py) read memory/ (outside repo); defer until memory structure is audited.

---

## 4. Reading Heuristics for Long Files

### Grep Pattern 1: Find Invariant Enforcement Sites
```bash
grep -n "INV-\|require_provenance\|assert_data_version\|authority.*VERIFIED" <file>
```
**Use when**: You need to verify an invariant is being checked. Example: grep `evaluator.py` for "INV-09" → find low rejection gate.

### Grep Pattern 2: Locate Writer Chain for Table X
```bash
grep -n "INSERT\|UPDATE\|DELETE" src/state/db.py src/calibration/store.py scripts/*.py | grep -i "<table_name>"
```
**Use when**: You need to find all writes to a table. Example: find all ensemble_snapshots writers → grep `INSERT INTO ensemble_snapshots`.

### Grep Pattern 3: Find Authority Origin
```bash
grep -n "authority\s*=\|VERIFIED\|UNVERIFIED" <file>
```
**Use when**: You need to trace where `authority` is assigned. Example: grep `rebuild_calibration_pairs_canonical.py` → find line 159 WHERE authority = 'VERIFIED'.

### Grep Pattern 4: Find Dead References
```bash
grep -rn "<function_or_file>" . --include="*.py" | wc -l
```
**Use when**: You suspect a function is dead. Example: grep "wu_daily_collector" across codebase → if only src/main.py calls it, it's a dead import target.

### Read Heuristic: Progressive Slice
**For files >500 lines**:
1. Read header docstring + imports (lines 1–50).
2. Grep for "def " to find function count + names.
3. Read functions that match your search pattern (INV sites, writers, authority).
4. Skip helper utilities unless they're in the critical path.

**Example**: rebuild_calibration_pairs_canonical.py — read docstring, then grep "def " → find `_fetch_eligible_snapshots()`, `_delete_canonical_slice()`, `_write_pairs()`. Read those three; skip `RebuildStats` unless you need statistics.

---

## 5. Phase 5–7 Reconnaissance Checklist (Next Scout)

### Phase 5 (mn2t6 ingest + low-track rebuild)
- [ ] **Grep for boundary_ambiguous**: How is it computed? Where is it stored (ensemble_snapshots_v2 column?)?
- [ ] **Grep for training_allowed**: Does rebuild_calibration_pairs_low.py filter on this? Does refit_platt.py respect it?
- [ ] **Find the causality status enum**: Where is `N/A_CAUSAL_DAY_ALREADY_STARTED` defined? How does Day0 low path consume it?
- [ ] **Check ingest_grib_to_snapshots.py real implementation**: Does it call `assert_data_version_allowed()` twice (once for mx2t6, once for mn2t6)?
- [ ] **Verify low_so_far is in observation_client.py**: Does it return low_so_far for all 4 providers (WU, IEM ASOS, Open-Meteo, fallback)?
- [ ] **Platt bucket scoping**: Are high/low models now stored with distinct bucket_keys (e.g., `NYC_winter` vs `NYC_winter_low`)?

### Phase 6 (Day0 nowcast + live low scoring)
- [ ] **Grep for causality_status**: Where is it used in Day0Signal constructor?
- [ ] **Find nowcast logic**: Does day0_signal.py or day0_window.py have a "if N/A_CAUSAL_DAY_ALREADY_STARTED → nowcast path" branch?
- [ ] **Check low_so_far propagation**: Does Day0Signal consume low_so_far from observation_client?
- [ ] **Verify market-fusion logic**: Are high and low P_posterior computed separately?

### Phase 7 (Simplification + Chore Bundle)
- [ ] **Audit db.py split readiness**: Are `queries/*.py` and `writers/*.py` patterns clear?
- [ ] **Check CITY_STATIONS cleanup**: Can backfill_wu_daily_all.py and oracle_snapshot_listener.py be deleted post-Phase-5?
- [ ] **Verify wu_daily_collector.py removal**: Is it still called from src/main.py, or did Phase 5 clean it up?
- [ ] **Test file split candidates**: Are test_runtime_guards.py or test_db.py causing CI slowdowns? If so, split by subsystem (e.g., test_db_schema.py, test_db_writers.py).

---

## 6. Final Observations

**What's Working**: Authority provenance chain (Phase 2–3 fixed); data_version quarantine gates (Phase 4 in place); fail-closed observation paths (Phase 3 unblocked low gate).

**What's Fragile**: add_calibration_pair() signature (Phase 4A.0 hotfix required); platt_models bucket_key scoping (Phase 4B+4C design decision); boundary-leakage quarantine (Phase 5 requirement, non-trivial to audit).

**What's Dead**: wu_daily_collector.py, migrate_rainstorm_full.py, CITY_STATIONS duplicates in backfill/oracle scripts.

**What's Load-Bearing**: canonical_write.py (DT#1 immune system), p_raw_vector_from_maxes() (training/inference seam), ensemble_snapshot_provenance.py (write-side gate), MetricIdentity type (high/low separation).

Handoff complete.

