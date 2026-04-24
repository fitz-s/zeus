# Phase 4 — High Canonical Cutover + GRIB→v2 Pipeline Birth

Packet: Dual-Track Metric Spine Refactor
Date opened: 2026-04-16
Predecessors: Phase 0 `943e74d` · Phase 0b `df12d9c` · Phase 1 `b025883` · Phase 2 `16e7385` · Phase 3 `6e5de84`
Gate opened by this phase: **Gate C** (high canonical cutover parity)

## 1. Scope (major reframe from original plan)

Phase 4 originally read as "re-canonicalize high to `mx2t6_local_calendar_day_max_v1`" — a parity diff. The scientist audit + architect preread + the newly-read `TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md` expose a deeper truth:

1. **`scripts/ingest_grib_to_snapshots.py` is a 55-line stub** — the GRIB→v2 bridge has never been built, for *either* track.
2. **High track today runs on `peak_window` semantics** in `src/contracts/ensemble_snapshot_provenance.py:25, 120` and the stub docstring. But the Phase 0 law (`zeus_dual_track_architecture.md §2.2`, `src/types/metric_identity.py:82`) already names the canonical as `local_calendar_day_max`. **These are two different physical quantities**. Phase 4 is the window cutover, not just the tag rename.
3. **`add_calibration_pair()` has no `temperature_metric` / `training_allowed` / `data_version`** parameters (scout + exec-bob + exec-carol independently confirmed). If ingest lands first and API changes second, low-track identity silently corrupts at the calibration writer seam.
4. **INV-15 is a live violation surface today** even though `v2` tables are empty — `src/calibration/store.py::add_calibration_pair` has no `training_allowed` gate, so an Open-Meteo fallback row entering calibration would silently contaminate training. User confirmed: no data pollution yet (v2 is zero rows) but the structural hole must be closed before any writer goes live.
5. **Low track is NOT a mirror of high**: the `mn2t6` remediation plan adds dynamic step horizons (west-coast day7 needs step_204), Day0 causality N/A as first-class coverage state, boundary-leakage quarantine, and per-city quarantine-rate monitoring. **High implementation must leave those seams open** so Phase 5 slots in without rewrites.

### Therefore Phase 4 is: **build the shared GRIB→v2 pipeline for the high track, with every low-specific seam pre-wired as a no-op that Phase 5 activates.**

## 2. Law anchors

- `docs/authority/zeus_current_architecture.md` §13 (metric identity mandatory), §14 (runtime-only fallback doctrine), §15 (daily low causality), §16 (DT#1 commit ordering).
- `docs/authority/zeus_dual_track_architecture.md` §2 (MetricIdentity spine), §4 (World DB v2 outline), §5 (causality law), §8 (forbidden moves).
- `architecture/invariants.yaml`: INV-14..INV-22.
- `architecture/negative_constraints.yaml`: NC-11..NC-15.
- `src/data/AGENTS.md`, `src/engine/AGENTS.md`, `src/state/AGENTS.md`, `src/strategy/AGENTS.md` (scoped).
- `scripts/AGENTS.md` (if present — scripts-layer scoped rules).
- **External plans (read once per phase entry)**:
  - `/Users/leofitz/.openclaw/workspace-venus/51 source data/TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md` (user-pointed, low track truth)
  - `/Users/leofitz/.openclaw/workspace-venus/51 source data/TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md` (shared architecture — both tracks on local calendar day)

## 3. Main-thread decisions (ratified)

| Q | Decision | Rationale |
|---|---|---|
| Q1 | `data_version` canonical = `tigge_mx2t6_local_calendar_day_max_v1` (high) + `tigge_mn2t6_local_calendar_day_min_v1` (low, Phase 5). **Update `provenance.py:24-25, 120` + stub docstring** to match Phase 0 authority. **Old `peak_window` tags go into the QUARANTINED set so they cannot re-enter.** | Phase 0 law + remediation plan both supersede peak_window. |
| Q2 | Parity thresholds: median `|Δp_raw|` ≤ 0.005 · p99 ≤ 0.02 · Brier regression ≤ 2% · `|ΔA|+|ΔB|` ≤ 0.10 per bucket. | Critic proposed; defensible. |
| Q3 | Add `members_unit TEXT NOT NULL DEFAULT 'degC'` + `members_precision REAL` to `ensemble_snapshots_v2`. | Zero rows = free migration. Structural Kelvin/°C defense per pre-mortem. |
| Q4 | Parity verification is **Gate C**, not backlog. `scripts/parity_diff_v2_vs_legacy.py` produces `parity_diff.md` that must meet Q2 thresholds. | Prevents "parity" becoming a dead-letter promise. |
| Q5 | Executor enumerates dead-code drops; bundle ≤3 safe drops; any beyond goes to chore. | Pattern from Phase 2 (dropped 3, kept `model_skill`). |
| Q6 | INV-15 hotfix (fallback source gate in calibration write path) lands as a **separate precursor commit** before Phase 4A.1. Source whitelist + `training_allowed=False` for non-whitelisted sources. User noted no data pollution yet — structural fix only. | Cleaner audit trail; smaller blast radius. |

## 4. Sub-phases

Phase 4 decomposes into 5 sub-phases (each potentially its own commit).

### 4A — Foundation commits (land before any writer work)

Blocking: every other Phase 4 step depends on 4A.

- **4A.0 (precursor)**: INV-15 hotfix. Add source whitelist to `add_calibration_pair` write path. Non-whitelisted source → `training_allowed=False`. Whitelist initially: `["tigge", "ecmwf_ens"]` (canonical) + document fallback sources (`openmeteo_hourly`, etc.) that are fail-closed. Standalone commit with its own R-J test.
- **4A.1**: data_version tag cutover. Update `src/contracts/ensemble_snapshot_provenance.py:24-25, 120` docstring + `scripts/ingest_grib_to_snapshots.py:19-20` docstring. Add `tigge_mx2t6_local_peak_window_max_v1` and `tigge_param167_*` to `QUARANTINED_DATA_VERSIONS` so the old window semantics cannot re-enter. Commit with R-P quarantine test.
- **4A.2**: schema migration. `ALTER TABLE ensemble_snapshots_v2 ADD COLUMN members_unit TEXT NOT NULL DEFAULT 'degC'` + `ADD COLUMN members_precision REAL`. Idempotent via `apply_v2_schema()`. Because `ensemble_snapshots_v2` is zero rows, no backfill needed. Update `tests/test_schema_v2_gate_a.py` with the new columns.
- **4A.3**: `src/calibration/store.py::add_calibration_pair` signature change. Required keyword args: `metric_identity: MetricIdentity`, `training_allowed: bool`, `data_version: str`. No defaults. Old callers raise TypeError until migrated. Keep legacy `calibration_pairs` write path for now (Phase 7 cutover deletes it); add new `add_calibration_pair_v2` that writes to `calibration_pairs_v2` routed by `metric_identity.temperature_metric`.
- **4A.4**: `src/calibration/store.py::save_platt_model` similar change. Adds `metric_identity`, writes `platt_models_v2` when the new function is used.

### 4B — `scripts/ingest_grib_to_snapshots.py` implementation (task #53)

Blocking: 4A complete.

- Dynamic step horizon per city (reads `cities.json` for timezone, computes `required_max_step = ceil_to_next_6h(local_day_end_utc - issue_utc)`). West-coast day7 may require `step_204`.
- Causality framework (even if high rarely triggers): the `causality` field in output JSON is always written. `status="OK"` for high. The infrastructure is here so Phase 5 slots in.
- Boundary-leakage framework: all GRIB bucket classifications (`outside` / `inner` / `boundary`) computed and emitted. For high, `boundary_ambiguous` is rare but always computed. For low (Phase 5), this decides quarantine.
- Manifest hash per snapshot: content-addressed `provenance_json`.
- Writes to `ensemble_snapshots_v2` with ALL fields: `temperature_metric`, `physical_quantity`, `observation_field`, `data_version`, `training_allowed`, `causality_status`, `boundary_ambiguous`, `ambiguous_member_count`, `manifest_hash`, `provenance_json`, `members_unit` (new from 4A.2).
- Calls `assert_data_version_allowed` before every INSERT (per provenance.py contract).
- Uses `commit_then_export` from `src/state/canonical_write.py` (Phase 2 helper) so DT#1 ordering is structural.
- Initial run: **high only, mx2t6 archive, 420 GRIB files, 2024-01-01..2025-09-24**.

### 4C — `scripts/rebuild_calibration_pairs_v2.py`

Blocking: 4B complete (needs `ensemble_snapshots_v2` rows).

- Reads `ensemble_snapshots_v2` WHERE `temperature_metric='high'` AND `training_allowed=true` AND `causality_status='OK'`.
- Reads `observations.high_temp` as ground truth (42504 rows available).
- Produces `calibration_pairs_v2` rows via `add_calibration_pair(metric_identity=HIGH_LOCALDAY_MAX, training_allowed=True, data_version=...)`.
- Preserves `decision_group_id` independence invariant (Phase 2 law).
- Honors bin grid per city/market.

### 4D — `scripts/refit_platt_v2.py`

Blocking: 4C complete.

- Reads `calibration_pairs_v2` WHERE `temperature_metric='high'`.
- Fits per bucket `(temperature_metric, cluster, season, data_version, input_space)` — NO city/target_date columns (Phase 2 critic finding on semantic pollution).
- Writes to `platt_models_v2` via `save_platt_model(metric_identity=HIGH_LOCALDAY_MAX, ...)`.
- Does not touch legacy `platt_models`.

### 4E — Gate C parity verification

Blocking: 4D complete.

- New script: `scripts/parity_diff_v2_vs_legacy.py`.
- For each bucket that exists in both legacy and v2:
  - Compute `p_raw_v2` and `p_raw_legacy` on a held-out audit sample
  - Median `|Δp_raw|`, p99, Brier regression, `|ΔA|+|ΔB|`
- Output: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/parity_diff.md`.
- Gate C PASSES when:
  - median `|Δp_raw|` ≤ 0.005
  - p99 `|Δp_raw|` ≤ 0.02
  - Brier regression ≤ 2%
  - `|ΔA|+|ΔB|` ≤ 0.10 per bucket
- Failure at any threshold → ITERATE (not commit).

## 5. Relationship invariants (R-I through R-P)

Written as failing tests BEFORE executor implementation.

- **R-I**: `add_calibration_pair()` called without `metric_identity` raises TypeError.
- **R-J**: INV-15 hotfix — a source not in whitelist (`openmeteo_hourly` etc.) forces `training_allowed=False` in the written row. Test with a fake fallback row.
- **R-K**: `ensemble_snapshots_v2.members_unit` is NOT NULL. Attempt to INSERT without it → IntegrityError.
- **R-L**: `ingest_grib_to_snapshots` writes rows with all 7 Phase 2 provenance fields populated + `members_unit='degC'`. No field silently defaults.
- **R-M**: `calibration_pairs_v2` rows written by `rebuild_calibration_pairs_v2.py` have `temperature_metric='high'`, `training_allowed=true`, `observation_field='high_temp'`, `data_version='tigge_mx2t6_local_calendar_day_max_v1'`. None defaulted.
- **R-N**: `platt_models_v2` rows have no `city` or `target_date` columns (Phase 2 pollution fix reminder). `UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active)` enforced.
- **R-O**: `members_json` unit is degC (no Kelvin silent drift). Stress test: insert members with implausible values (>200 or <-100) → schema or guard rejects.
- **R-P**: `assert_data_version_allowed('tigge_mx2t6_local_peak_window_max_v1')` raises `DataVersionQuarantinedError` (the old high tag is now refused).

## 6. Out-of-scope (explicit non-goals)

- mn2t6 / low track ingest (Phase 5 reuses this pipeline with `metric='low'`).
- Day0 low nowcast signal (`Day0LowNowcastSignal` lands in Phase 6).
- Live writer cutover from legacy to v2 for runtime (stays legacy until Phase 7 activation gate).
- `kelly_size()` executable-price law (Phase 9 pre-activation).
- RED force-exit sweep (Phase 9).
- Scripts-tier CITY_STATIONS in `backfill_wu_daily_all.py` + `oracle_snapshot_listener.py` (Phase C chore).
- `wu_daily_collector.py` retirement (Phase C after Phase 3 operator-stable).
- Test file splits.
- `src/state/db.py` god-object split (Phase 2 architect recommendation; chore after Phase 4).

## 7. Sequencing within Phase 4

```
4A.0 (INV-15 hotfix, standalone commit)
  ↓
4A.1 → 4A.2 → 4A.3 → 4A.4 (Foundation, one bundled commit)
  ↓
4B (ingest_grib_to_snapshots.py implementation)
  ↓
4C (rebuild_calibration_pairs_v2.py)
  ↓
4D (refit_platt_v2.py)
  ↓
4E (parity_diff + Gate C verification)
```

Total: 5 commits (minimum). Each sub-phase has critic review before next begins.

## 8. Team assignments

- **scout-dave**: pre-read both TIGGE plans (`TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md`, `TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md`) + audit current `ingest_grib_to_snapshots.py`, `rebuild_calibration_pairs_canonical.py`, `refit_platt.py` implementations; deliver pattern inventory + dead-code candidates for 4A.
- **testeng-emma**: draft R-I through R-P as failing tests (8 R-letters). Scope each to a test file matching the sub-phase (`test_phase4_foundation.py`, `test_phase4_ingest.py`, `test_phase4_rebuild.py`, `test_phase4_platt.py`, `test_phase4_parity_gate.py`). Must read both TIGGE plans before drafting R-L / R-M.
- **exec-bob**: owns 4A.0 (hotfix), 4A.3 (`add_calibration_pair` signature), 4A.4 (`save_platt_model` signature), 4B (`ingest_grib_to_snapshots.py` implementation).
- **exec-carol**: owns 4A.1 (data_version tag cutover), 4A.2 (schema migration), 4C (`rebuild_calibration_pairs_v2.py`), 4D (`refit_platt_v2.py`).
- **critic-alice**: wide review after each sub-phase. L0 authority re-check per compact protocol. Watch for scope drift into Phase 5/6 territory.
- Cross-validation via a2a at every sub-phase boundary (exec-bob ⇄ exec-carol) before critic invoked.

## 9. Pre-mortem (critic's answer, accepted)

Most likely silent failure in 2 weeks: **Kelvin/°C mixup in `members_json`**. ECMWF delivers GRIB in Kelvin; Zeus expects degC. If ingest silently stores the raw Kelvin values, every downstream Platt evaluation would be biased by +273. `ensemble_snapshots_v2.members_unit` + R-O test is the structural antibody.

## 10. Dependencies + exit gates

- **Gate A** (Phase 2) confirmed open.
- **Gate B** (Phase 3) confirmed open.
- **Gate C** (this phase) opens on parity_diff.md under thresholds.
- Phase 5 cannot open until Gate C passes.

## 11. Evidence layout

- `phase4_evidence/phase4_plan.md` (this file)
- `phase4_evidence/phase4_architect_preread.md` (critic-alice, already landed)
- `phase4_evidence/phase3_to_phase4_*_learnings.md` × 4 (value-extracted from teammates)
- `phase4_evidence/phase4_critic_verdicts.md` (populated after each critic round)
- `phase4_evidence/parity_diff.md` (produced by 4E)

## 12. Main-thread rules for this phase

- Teammates read the full phase4_plan.md first, then the two TIGGE plans, then their sub-phase brief.
- Every sub-phase commit goes through critic before main-thread commits to branch.
- If a critic verdict adds new R-invariants, testeng-emma un-skips or creates them before next sub-phase starts.
- Compact protocol (broadcast earlier): applies throughout. Any teammate at 2+ compacts gets replaced.
- Main-thread does NOT re-read the TIGGE plans; teammates do. Main-thread's context stays reserved for cross-sub-phase synthesis + scope rulings.
