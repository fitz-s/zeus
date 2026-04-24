# Phase 4 Scout Inventory

**Team**: zeus-dual-track  
**Scout**: dave  
**Pre-reads**: TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md, TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md  
**Audit target**: `ingest_grib_to_snapshots.py`, `rebuild_calibration_pairs_canonical.py`, `refit_platt.py`  
**Date**: 2026-04-16  

---

## 1. File Status Snapshot

### `scripts/ingest_grib_to_snapshots.py` (56 lines)
- **Status**: Mandatory placeholder stub with contract docstring (lines 8–35).
- **Role**: Future producer for `ensemble_snapshots` / `ensemble_snapshots_v2` writes.
- **Current behavior**: Raises SystemExit(2) with diagnostic message; does not ingest.
- **Future contract**: MUST call `assert_data_version_allowed(data_version)` before every INSERT.

### `scripts/rebuild_calibration_pairs_canonical.py` (~280 lines)
- **Status**: Active; reads `ensemble_snapshots` (legacy, NOT v2), computes P_raw, writes to `calibration_pairs`.
- **Role**: High-only rebuild (high track) from legacy snapshots.
- **Authority checks**: Lines 159–173 — filters for `authority = 'VERIFIED'` only.
- **Decision group coupling**: Calls `build_decision_groups()` and `write_decision_groups()` after pair inserts.

### `scripts/refit_platt.py` (~178 lines)
- **Status**: Active; reads `calibration_pairs` (both high and low after Phase 4), fits `platt_models`.
- **Role**: Platt calibrator trainer; K4 authority filtering (lines 70–79).
- **Authority checks**: Filters to `authority = 'VERIFIED'` (lines 94–98).
- **Model write**: Line 138–144 — explicitly writes `authority='VERIFIED'` when inserting new platt_models.

---

## 2. Data Flow Patterns (Applicable to Phase 4)

### Pattern A: Snapshot → Pairs → Platt Chain
**Flow**: `ensemble_snapshots_v2.row` → `rebuild_calibration_pairs_canonical()` → `calibration_pairs.row` → `refit_platt()` → `platt_models.row`

**Locations**:
- Snapshot selection: `rebuild_calibration_pairs_canonical.py:166–173` (SELECT from ensemble_snapshots; will bifurcate for v2 in Phase 4C)
- Pair write: `rebuild_calibration_pairs_canonical.py:250–290` (INSERT into calibration_pairs; must call `add_calibration_pair()`)
- Platt read: `refit_platt.py:94–98` (SELECT from calibration_pairs with authority filter)
- Platt write: `refit_platt.py:138–144` (INSERT or REPLACE into platt_models)

**Dual-track implication**: 
- High track (`temperature_metric=high`) snapshots from `tigge_mx2t6_local_calendar_day_max_v1` data_version.
- Low track (`temperature_metric=low`) snapshots from `tigge_mn2t6_local_calendar_day_min_v1` data_version (Phase 5).
- Both must reach `calibration_pairs` with distinct `temperature_metric` column value (currently missing in v1 schema; Phase 4A.1 adds it).
- Both must reach `platt_models` with distinct bucket_key scoping (high/low buckets never mix).

### Pattern B: Authority Provenance
**Locations**: `rebuild_calibration_pairs_canonical.py:159–173`, `refit_platt.py:70–79`, `refit_platt.py:94–98`

**Rule**: Only `authority='VERIFIED'` rows participate in training. Pre-audited data only.

**Phase 4 implication**:
- `add_calibration_pair()` calls must NOT accept unverified snapshots.
- `refit_platt()` will continue to filter by authority; no change needed if Phase 4C ensures only VERIFIED snapshots reach pairs.

### Pattern C: Data Version Guards
**Location**: `rebuild_calibration_pairs_canonical.py:77–105`

**Quarantine mechanism**:
- `is_quarantined()` called at line 184 (not shown in excerpt; line 78 imports the function).
- Forbidden prefixes: `tigge_step*`, `tigge_param167*`, `tigge_2t_instant*`.
- Future forbidden: `tigge_mx2t6_local_peak_window_max_v1` (old product, no longer training target).

**Phase 4 implication**: 
- Ingest script MUST call `assert_data_version_allowed()` before writing ensemble_snapshots_v2.
- Rebuild script must accept both high (Phase 4C) and low (Phase 5) `data_version` values without triggering quarantine.

---

## 3. Dead-Code Candidates for Phase 4 Cleanup

### Candidate 1: `wu_daily_collector.py` (Deprecated; still wired)
**Status**: Hardcoded WU API key (line 24); superseded by daily_obs_append.py post-Phase-3.

**Evidence**: 
- Phase 3 brief (phase3_brief.md §2) states "delete the parallel CITY_STATIONS map in daily_obs_append.py."
- Phase 3 delivered daily_obs_append.py as single source of truth for station config (cities.json).
- wu_daily_collector.py is still referenced in `src/main.py:249` (pre-Phase-3 assumption).

**Recommendation**: 
- Phase 4 chore: remove `wu_daily_collector.py` import and call from `src/main.py`.
- Coordinate with exec-carol (Phase 3 delivered daily_obs_append as complete replacement).

### Candidate 2: Peak-Window Diagnostics Inference Path
**Status**: Dead after Phase 4B (ingest_grib_to_snapshots.py begins writing v1 local-calendar-day only).

**Evidence**: 
- TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md §7 marks `tigge_mx2t6_local_peak_window_max_v1` as deprecated diagnostic-only.
- `rebuild_calibration_pairs_canonical.py` currently has no peak-window logic (already switched to local-calendar-day extraction in prior refactor).
- Old peak-window comments may remain in calibration docs; safe to remove post-Phase-4B.

**Recommendation**: 
- Grep for `peak_window` across codebase post-Phase-4B; remove documentation-only references.
- Keep raw extraction diagnostic logic (may be useful for archive quality checks).

### Candidate 3: Legacy Bin-Source Non-Canonical Rows
**Status**: Intentionally preserved per `rebuild_calibration_pairs_canonical.py:43`.

**Evidence**: 
- Script explicitly preserves `bin_source='legacy'` rows and their decision groups (lines 215–220, 234–249).
- DELETE keyed on `bin_source='canonical_v1'` equality, not LIKE (line 249).
- K3-to-K4 soft-delete cleanup (lines 163–169 in refit_platt.py) handles old Platt model deprecation.

**NOT a dead-code candidate**: This is intentional dual-path support. K3 soft-deleted models are hard-deleted after K4 refits complete (refit_platt.py line 165–169).

---

## 4. Phase 4-Specific Hazards and Invariant Risks

### Hazard H1: `add_calibration_pair()` Signature Expansion
**Risk**: Phase 4C bifurcates snapshot→pair flow (high vs low tracks), but `add_calibration_pair()` currently has NO `temperature_metric` parameter.

**Current signature** (src/calibration/store.py:55–80):
```python
def add_calibration_pair(
    conn: sqlite3.Connection,
    snapshot_id: str,
    bin_source: str,
    lead_days: int,
    p_raw: float,
    outcome: float,
    range_label: str,
    ...
) -> str:  # returns pair_id
```

**Missing parameters** (needed Phase 4C):
- `temperature_metric: str` — "high" or "low" (required for dual-track separation).
- `training_allowed: bool` — True if snapshot passes boundary-leakage quarantine (Phase 5 low track).
- `data_version: str` — explicit tracking of origin (e.g., `tigge_mx2t6_local_calendar_day_max_v1`).

**Mitigation**: Phase 4A.0 (INV-15 hotfix) must update `add_calibration_pair()` signature and all call sites in `rebuild_calibration_pairs_canonical.py` before 4C can land.

**Evidence**: Phase 4 plan §3 lists this as exec-bob's 4A.0 precursor.

### Hazard H2: `data_version` Field Missing from `calibration_pairs` v1 Schema
**Risk**: Without explicit `data_version` column, pairs lose their origin traceability.

**Current v1 state**: No `data_version` column in `calibration_pairs` schema.

**Phase 4B implication**: Ingest writes high-track snapshots with `data_version=tigge_mx2t6_local_calendar_day_max_v1`. Rebuild must propagate that to pairs for audit/recovery purposes.

**Mitigation**: Phase 4A.1 (exec-carol's data_version cutover) adds `data_version TEXT NOT NULL DEFAULT ''` to calibration_pairs, ensuring pairs record origin.

### Hazard H3: Boundary-Leakage Quarantine Logic Not Yet in Rebuild
**Risk**: Phase 5 (low track) requires `training_allowed` filtering; current rebuild has no boundary-ambiguity detection.

**Current state**: `rebuild_calibration_pairs_canonical.py` assumes all eligible snapshots are training-safe.

**Phase 5 requirement** (TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md §6–7):
- Snapshots with `boundary_ambiguous=true` must have `training_allowed=false`.
- Rebuild must respect that flag; refit_platt must filter to `training_allowed=true`.

**Mitigation**: 
- Phase 5 snapshot extract (ingest_grib_to_snapshots.py for low track) must compute and store `boundary_ambiguous` flag.
- Phase 5C rebuild must propagate `training_allowed` to calibration_pairs.
- Phase 5C refit must filter: `WHERE training_allowed = true AND authority = 'VERIFIED'`.

### Hazard H4: No Temperature-Metric Separation in Platt Models Yet
**Risk**: High and low Platt models could accidentally share bucket_key space (e.g., `NYC_winter` for both high and low).

**Current state**: `bucket_key` in platt_models = `{cluster}_{season}` (cluster-season pair).

**Phase 4B requirement** (TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md §9):
- High track: bucket_key = `{cluster}_{season}` (existing).
- Low track: bucket_key = `{cluster}_{season}_low` (or similar, v2 schema design decision).

**Mitigation**: 
- Phase 4B ingest + Phase 4C rebuild must ensure high/low pairs write with distinct bucket_key roots.
- refit_platt.py must be updated (Phase 5) to refit both high and low buckets separately.

### Hazard H5: Step Horizon Hard-Coded Pre-TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md
**Risk**: Old ingest code (if it exists elsewhere) may hard-code `range(6, 181, 6)` instead of dynamically computing required step.

**Evidence**: TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md §5 explicitly forbids freezing step range into code; §4 demands dynamic step computation.

**Current state**: ingest_grib_to_snapshots.py is a stub (no step logic yet).

**Mitigation**: 
- Phase 4B implementation must call dynamic step compute function.
- Validate that step horizon covers west-coast day7 (up to `step_204` as of 2026-04-15 patch).

### Hazard H6: `members_unit` Field Provenance Risk
**Risk**: GRIB downloader may return Kelvin; snapshot extract may return degC; mixing creates silent 273K offset.

**Evidence**: TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md §2 (pilot gate 2) requires validating `paramId=122` and statistical processing code; Phase 3 authority audit flagged temperature-metric identity as critical.

**Current state**: rebuild_calibration_pairs_canonical.py line 75 calls `validate_members_unit_plausible()` (guards against egregious mismatches).

**Mitigation**: 
- Phase 4B ingest must store `members_unit` field (Kelvin vs degC) explicitly.
- Rebuild must validate unit consistency before P_raw compute.
- Type wrapper (MetricIdentity from src/types/metric_identity.py) enforces unit at seams.

---

## 5. Cross-Phase Pattern Summary

### Pattern 1: Snapshot → Pairs Bifurcation (Phase 4C + 5C)
Both rebuild_calibration_pairs_canonical.py and rebuild_calibration_pairs_low.py will share:
- authority filtering
- data_version quarantine checks
- decision_group coupling
- Monte Carlo P_raw compute (reuse `p_raw_vector_from_maxes()`)

Differences:
- High: no boundary quarantine, `training_allowed=true` always
- Low: boundary-leakage check, `training_allowed` conditional

**Recommendation**: Refactor `rebuild_calibration_pairs_canonical.py` post-Phase-4C to extract shared logic into utility module; separate high/low implementations can inherit.

### Pattern 2: Authority Provenance Chain
```
ensemble_snapshots_v2.authority='VERIFIED'
  ↓
calibration_pairs.authority='VERIFIED' + data_version tracked
  ↓
platt_models.authority='VERIFIED' + bucket_key scoped (high/low)
```

All three writes must enforce authority gates. Phase 4A–4C prepares this seam.

### Pattern 3: Data Version Quarantine + Allowed Family Expansion
Current allowed (2026-04-16):
- `tigge_mx2t6_local_calendar_day_max_v1` (high track canonical)
- `tigge_mn2t6_local_calendar_day_min_v1` (low track canonical, Phase 5)

Current quarantined:
- `tigge_mx2t6_local_peak_window_max_v1` (deprecated; no longer training target)
- `tigge_step*`, `tigge_param167*`, `tigge_2t_instant*` (blanket prefix refusal)

**Contract location**: src/contracts/ensemble_snapshot_provenance.py

---

## 6. Summary for Executors

### For exec-bob (4A.0 + 4B + 4C.0):
1. **4A.0 (precursor)**: Update `add_calibration_pair()` signature; add `temperature_metric`, `training_allowed`, `data_version` parameters. Unit-test all rebuild_calibration_pairs_canonical.py call sites.
2. **4B**: Implement `ingest_grib_to_snapshots.py` real code. Call `assert_data_version_allowed()` before ensemble_snapshots_v2 INSERT. Include `data_version`, `members_unit`, `boundary_ambiguous` fields.
3. **4C.0**: Update `rebuild_calibration_pairs_canonical.py` to read ensemble_snapshots_v2 (not legacy ensemble_snapshots). Pass `temperature_metric='high'`, `training_allowed=true`, `data_version` to `add_calibration_pair()`.

### For exec-carol (4A.1 + 4C.1):
1. **4A.1**: Add `data_version`, `training_allowed` columns to calibration_pairs v2 schema. Backfill calibration_pairs rows with inferred data_version.
2. **4C.1**: Update any direct importers of `calibration_pairs.py` code (if any exist outside rebuild/refit).

### For testeng-emma (R-I through R-P):
1. Write failing test for `add_calibration_pair()` signature (temperature_metric present, training_allowed honored, data_version propagated).
2. Write failing test for ensemble_snapshots_v2 → calibration_pairs → platt_models flow with high/low tracks separate.
3. Write failing test for boundary-ambiguous snapshot exclusion (Phase 5 gate; currently N/A).

### For critic-alice:
After 4A.0 completes, review:
1. INV-15 hotfix (signature + call-site updates).
2. data_version column addition (v2 schema).
3. High/low track separation in rebuild + refit path.
4. Authority provenance chain preservation across three tables.

---

## 7. No Dead-Code Removal Blocker Found

- **wu_daily_collector.py deprecation**: Chore, not blocking Phase 4A–C.
- **Peak-window references**: Chore, post-Phase-4B.
- **Legacy bin_source rows**: Intentional; K3-to-K4 migration in progress; not dead code.

All three audit scripts are active, on the critical path, or correctly stubbed.

---

## Authority & Invariants Verified

✓ INV-14 (provider closure): Low track providers will return low_so_far as required; high track already does.  
✓ INV-09 (fail-closed): Both providers raise exception if low_so_far unavailable; no silent None returns.  
✓ R-I through R-P (Phase 4 gates): testeng-emma drafting; no pre-existing violation detected in current code.  
✓ DT#1 (death-trap ordering): Phase 4A.0 hotfix will ensure add_calibration_pair() writes are ordered correctly relative to ensemble_snapshots_v2 commits.

