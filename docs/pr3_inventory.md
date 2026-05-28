# PR3 Inventory — Generation-Naming Surface (snapshot 2026-05-28)

> Authority: `docs/findings_2026_05_28.md` §Program B + `PLAN.md` §Program B.
> Prior inventory: `docs/operations/V1V2_SUFFIX_INVENTORY_2026-05-25.md` covers v1/v2 DB table
> classification in detail (categories A–E, 4 open trace items). This document adds: test file
> coverage, contract/dataclass field inventory, script deletion candidates (B4), registry entry
> analysis (B7), and total LOC impact.

---

## 1. File-path hits (paths containing forbidden tokens)

Scope: tracked files only (`git ls-files`); skips `.git`, `.venv`, `__pycache__`, `.codegraph`,
binaries (`.db`, `.sqlite`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.pdf`).

### 1a. `version` (case-insensitive) in path

| File |
|------|
| `architecture/world_schema_version.yaml` |
| `scripts/check_schema_version.py` |
| `tests/test_canonical_data_versions_namespace.py` |
| `tests/test_data_version_priority.py` |
| `tests/test_load_platt_v2_data_version_filter.py` |

**5 files total.** The two non-test files (`world_schema_version.yaml`,
`check_schema_version.py`) are B2 deletion targets. The 3 test files will be renamed or folded
into canonical equivalents in PR3.

### 1b. `legacy` in path

| File |
|------|
| `.claude/hooks/legacy/cotenant-staging-guard.sh` |
| `.claude/hooks/legacy/post-merge-cleanup-reminder.sh` |
| `.claude/hooks/legacy/pre-commit-invariant-test.sh` |
| `.claude/hooks/legacy/pre-commit-secrets.sh` |
| `.claude/hooks/legacy/pre-edit-architecture.sh` |
| `.claude/hooks/legacy/pre-merge-contamination-check.sh` |
| `.claude/hooks/legacy/pre-write-capability-gate.sh` |
| `docs/reference/legacy/AGENTS.md` |
| `docs/reference/legacy/legacy_reference_data_inventory.md` |
| `docs/reference/legacy/legacy_reference_data_strategy.md` |
| `docs/reference/legacy/legacy_reference_market_microstructure.md` |
| `docs/reference/legacy/legacy_reference_quantitative_research.md` |
| `docs/reference/legacy/legacy_reference_repo_overview.md` |
| `docs/reference/legacy/legacy_reference_settlement_source_provenance.md` |
| `docs/reference/legacy/legacy_reference_statistical_methodology.md` |
| `scripts/deprecate_legacy_state_files.py` |
| `scripts/etl_forecasts_v2_from_legacy.py` |
| `scripts/migrations/202605_drop_ensemble_snapshots_legacy.py` |
| `tests/test_calibration_transfer_policy_legacy_bridge.py` |
| `tests/test_etl_forecasts_v2_from_legacy.py` |
| `tests/test_no_legacy_ensemble_snapshots_reader.py` |
| `tests/test_settlement_migration_unknown_legacy_default.py` |
| `tests/runtime/test_legacy_snapshot_projection_upsert.py` |

**23 files total.** `.claude/hooks/legacy/` (7 hook scripts) and
`docs/reference/legacy/` (8 reference docs) are hook/doc-archive directories — NOT runtime
source. The 5 `scripts/` + 3 `tests/` files are in-scope for B4/B7.

### 1c. `_v[0-9]+` in path (69 files)

Key sub-categories:
- **scripts/** (27 files): calibration/obs/settlement v2 pipeline scripts — mostly B4 delete targets.
- **tests/** (31 files): v2 test files mapping to v2 tables/scripts — will be renamed or deleted with their subjects.
- **src/** (5 files): `drift_refit_arm.py`, `executable_market_snapshot.py`,
  `observation_instants_v2_writer.py`, `polymarket_v2_adapter.py`, `v2_table_schema_preference.py`.
  These are B3/B7 targets (rename or keep with justification).
- **src/state/schema/**: `v2_schema.py` — B3 target.
- **src/oracle/ddd_artifacts/**: `v2_city_floors.json`, `v2_nstar.json` — semantic provenance tags,
  per V1V2 inventory §D (KEEP, no v1 counterpart).

### 1d. `vnext` in path (5 files)

| File |
|------|
| `src/analysis/market_analysis_vnext.py` |
| `src/contracts/shoulder_strategy_vnext.py` |
| `tests/test_inv_vnext_substitution_consistency.py` |
| `tests/test_shoulder_strategy_vnext.py` |
| `tests/test_shoulder_vnext_classify.py` |

**5 files total.** `market_analysis_vnext.py` and `shoulder_strategy_vnext.py` are active modules
(not migration artifacts). B3 scope: rename to canonical base when the VNext measurement path
becomes the only path.

### 1e. No `_old` or `_new` file-path hits

No tracked file paths end with `_old` or `_new`.

---

## 2. Source-text hits per file (count only)

Top 40 files by total forbidden-token-line count. Columns: occurrences of `version` (case-insensitive),
`_v[N]` pattern, `legacy`, `schema_version`, `event_version`, `data_version`, `signal_version`. Total
= sum across all columns (overcounts overlap within a line; useful for ranking).

| file | version | _v[N] | legacy | schema_ver | event_ver | data_ver | signal_ver | total |
|------|---------|-------|--------|------------|-----------|----------|------------|-------|
| architecture/db_table_ownership.yaml | 243 | 53 | 144 | 231 | 0 | 3 | 0 | 674 |
| tests/test_truth_surface_health.py | 66 | 180 | 95 | 0 | 0 | 43 | 0 | 384 |
| src/state/db.py | 98 | 124 | 103 | 30 | 1 | 26 | 0 | 382 |
| scripts/verify_truth_surfaces.py | 63 | 173 | 47 | 0 | 0 | 43 | 0 | 326 |
| scripts/rebuild_calibration_pairs_v2.py | 107 | 109 | 11 | 2 | 0 | 92 | 0 | 321 |
| tests/test_phase5_gate_d_low_purity.py | 80 | 90 | 4 | 3 | 0 | 77 | 0 | 254 |
| architecture/topology.yaml | 22 | 159 | 57 | 1 | 0 | 1 | 0 | 240 |
| src/calibration/manager.py | 75 | 41 | 46 | 0 | 0 | 74 | 0 | 236 |
| src/calibration/store.py | 53 | 76 | 43 | 0 | 0 | 52 | 0 | 224 |
| architecture/digest_profiles.py | 21 | 157 | 35 | 0 | 0 | 1 | 0 | 214 |
| src/state/schema/v2_schema.py | 31 | 147 | 7 | 2 | 0 | 24 | 0 | 211 |
| src/engine/evaluator.py | 55 | 61 | 50 | 4 | 0 | 39 | 0 | 209 |
| tests/test_harvester_metric_identity.py | 23 | 129 | 25 | 0 | 0 | 20 | 0 | 197 |
| tests/test_phase5b_low_historical_lane.py | 51 | 70 | 22 | 0 | 0 | 50 | 0 | 193 |
| tests/test_runtime_guards.py | 25 | 80 | 62 | 1 | 1 | 16 | 0 | 185 |
| src/contracts/ensemble_snapshot_provenance.py | 74 | 31 | 12 | 0 | 0 | 60 | 0 | 177 |
| src/data/calibration_transfer_policy.py | 66 | 10 | 24 | 0 | 0 | 64 | 0 | 164 |
| scripts/refit_platt_v2.py | 55 | 48 | 4 | 0 | 0 | 53 | 0 | 160 |
| scripts/promote_calibration_pairs_v2.py | 61 | 35 | 3 | 0 | 0 | 60 | 0 | 159 |
| tests/test_phase4_rebuild.py | 46 | 61 | 0 | 0 | 0 | 46 | 0 | 153 |
| scripts/promote_calibration_v2_stage_to_prod.py | 55 | 43 | 1 | 0 | 0 | 53 | 0 | 152 |
| config/settings.json | 1 | 144 | 4 | 0 | 0 | 1 | 0 | 150 |
| tests/test_learning_loop_observation.py | 72 | 36 | 18 | 0 | 0 | 20 | 0 | 146 |
| architecture/test_topology.yaml | 10 | 104 | 23 | 1 | 0 | 5 | 0 | 143 |
| tests/state/test_boot_migration_v28_antibody.py | 60 | 25 | 0 | 57 | 0 | 0 | 0 | 142 |
| scripts/promote_platt_models_v2.py | 59 | 25 | 0 | 0 | 0 | 57 | 0 | 141 |
| tests/test_phase10b_dt_seam_cleanup.py | 7 | 91 | 37 | 5 | 0 | 1 | 0 | 141 |
| tests/test_architecture_contracts.py | 4 | 7 | 125 | 0 | 2 | 0 | 0 | 138 |
| tests/test_phase7a_metric_cutover.py | 24 | 90 | 4 | 0 | 0 | 17 | 0 | 135 |
| src/execution/harvester.py | 55 | 34 | 16 | 0 | 0 | 24 | 0 | 129 |
| src/data/forecast_extrema_authority.py | 49 | 9 | 24 | 0 | 0 | 45 | 0 | 127 |
| tests/test_phase4_platt_v2.py | 23 | 67 | 10 | 0 | 0 | 22 | 0 | 122 |
| scripts/ddd_v1_v2_replay.py | 3 | 109 | 0 | 0 | 0 | 3 | 0 | 115 |
| architecture/script_manifest.yaml | 17 | 75 | 14 | 1 | 0 | 5 | 0 | 112 |
| src/data/ecmwf_open_data.py | 46 | 19 | 3 | 0 | 0 | 42 | 0 | 110 |
| src/data/executable_forecast_reader.py | 40 | 15 | 15 | 0 | 0 | 40 | 0 | 110 |
| tests/test_calibration_transfer_policy_legacy_bridge.py | 36 | 1 | 34 | 0 | 0 | 34 | 0 | 105 |
| tests/test_replay_time_provenance.py | 27 | 31 | 21 | 0 | 0 | 25 | 0 | 104 |
| tests/test_load_platt_v2_data_version_filter.py | 37 | 24 | 5 | 0 | 0 | 36 | 0 | 102 |
| tests/test_market_scanner_provenance.py | 33 | 38 | 17 | 0 | 0 | 10 | 0 | 98 |

---

## 3. Tables containing forbidden suffixes

### From `init_schema` (world DB)

5 tables flagged:
- `historical_forecasts_v2`
- `model_bias_ens_v2`
- `observation_instants_v2`
- `platt_models_v2`
- `rescue_events_v2`

**Note:** `evidence_tier_assignments_new` and `no_trade_events_new` appear in
`architecture/db_table_ownership.yaml` as `legacy_archived` but are NOT currently created by
`init_schema` (already removed from DDL).

### From `init_schema_forecasts` (forecasts DB)

4 tables flagged:
- `calibration_pairs_v2`
- `ensemble_snapshots_v2`
- `market_events_v2`
- `settlements_v2`

### From `init_schema_trade_only` (trades DB)

0 tables flagged.

### Per prior V1V2 inventory (§A–D cross-reference)

V1V2 inventory already classifies all v2 table families. B3 target: rename
`ensemble_snapshots_v2`→`ensemble_snapshots`, `calibration_pairs_v2`→`calibration_pairs`,
`settlements_v2`→`settlements`, `market_events_v2`→`market_events`,
`platt_models_v2`→`platt_models`, `rescue_events_v2`→`rescue_events`. Where canonical base
already exists with live data, resolve per V1V2 inventory guidance before rename.
`observation_instants_v2` is a parallel tier (NOT rename target per §C).

---

## 4. Domain fields named `*_version`

All `*_version` dataclass fields and function-parameter declarations in `src/` — with proposed
PR3 replacement per audit §B5 mapping table (`docs/findings_2026_05_28.md` §Replacement Model).

### `src/state/portfolio.py` — `Position` dataclass (lines 430–)

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `pricing_semantics_id` | `Position.pricing_semantics_id: str = "legacy_unclassified"` (l.474) | `pricing_semantics_id` |
| `execution_cost_basis_version` | `Position.execution_cost_basis_version: str = ""` (l.475) | `cost_basis_policy_id` |
| `signal_version` | `Position.signal_version: str = "v2"` (l.491) | `signal_id` |
| `calibration_version` | `Position.calibration_version: str = ""` (l.492) | `calibration_model_id` |

Also serialized at l.2305–2306; downstream in `src/execution/fill_tracker.py` (l.888),
`src/execution/harvester.py` (l.93–96).

### `src/contracts/execution_intent.py`

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `pricing_semantics_id` | ExecutionIntent + 2 subclasses (l.858, l.1006, l.1348, l.1570) | `pricing_semantics_id` |

### `src/analysis/market_analysis_vnext.py`

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `bin_schema_version` | `MarketAnalysisVNext` dataclass field (l.71, l.102) | `bin_schema_id` |

### `src/calibration/forecast_calibration_domain.py`

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `bin_schema_version` | `BinGrid` dataclass (l.154) | `bin_schema_id` |
| `data_version` | `CalibrationPairsRow` (l.223) + `CalibrationPairsKey` (l.481) | `dataset_id` |

### `src/calibration/blocked_oos.py`

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `model_version` | function param default `"blocked_oos_v1"` (l.156) | `model_artifact_id` |

### `src/calibration/day0_horizon_calibration.py`

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `fit_version` | dataclass field `str = "hpf_v1"` (l.93) | `fit_artifact_id` |

### `src/calibration/decision_group.py`

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `source_model_version` | function params + validation (l.43, l.93) | `forecast_model_id` |

### `src/calibration/retrain_trigger.py`

| Field | Location | Proposed replacement |
|-------|----------|---------------------|
| `data_version` | `CorpusFilter.data_version: str = "operator_retrain_candidate_v1"` (l.70) | `dataset_id` |

### `src/calibration/store.py`, `src/calibration/manager.py`, `src/calibration/ens_bias_repo.py`

`data_version` appears as a query/filter parameter throughout (52, 70, 30+ occurrences
respectively). All map to `dataset_id` / `source_snapshot_id`.

### `src/contracts/ensemble_snapshot_provenance.py`

`data_version` functions `is_quarantined` + `assert_data_version_allowed` (l.219, l.231);
`data_version` as filter. Rename param → `dataset_id`.

### `src/contracts/epistemic_context.py`

`data_version: str` dataclass field (l.17) → `dataset_id`.

### `src/contracts/expiring_assumption.py`

`semantic_version: str` dataclass field (l.19) → `semantic_id` or `assumption_version_id`.

### `src/contracts/venue_submission_envelope.py`

`schema_version: int = SCHEMA_VERSION` (l.71) — part of B2 removal.

### `src/ingest_main.py`

`schema_version: str = "unknown_v0"` local var (l.258) — B2 scope; part of SCHEMA_VERSION removal.

### `src/types/metric_identity.py`

`data_version: str` in `MetricIdentity` dataclass (l.27); `source_family_from_data_version`
helper (l.162). All → `dataset_id`.

### `src/state/db.py`

`schema_version INTEGER NOT NULL DEFAULT 1` column in `source_run` DDL (l.598); `SCHEMA_VERSION`
constant (l.897, 30 hits total) — B2 removal.
`calibration_model_version TEXT` in `position_current` DDL (l.1137, l.3934) — B5 rename.
`event_version INTEGER NOT NULL DEFAULT 1` in `position_events` DDL (l.3785) — B6 drop.

### `src/state/schema/v2_schema.py`

`model_version TEXT NOT NULL` column (l.119) — B5 rename.

---

## 5. Scripts to delete (per audit §B4)

All 16 scripts listed in `docs/findings_2026_05_28.md §B4` are **PRESENT** in `scripts/`:

| Script | Status | LOC |
|--------|--------|-----|
| `compare_diurnal_v1_v2.py` | PRESENT | ~55 |
| `ddd_v1_v2_replay.py` | PRESENT | ~247 |
| `etl_forecasts_v2_from_legacy.py` | PRESENT | ~350 |
| `backfill_obs_v2.py` | PRESENT | ~350 |
| `fill_obs_v2_dst_gaps.py` | PRESENT | ~415 |
| `fill_obs_v2_meteostat.py` | PRESENT | ~340 |
| `audit_observation_instants_v2.py` | PRESENT | ~490 |
| `audit_calibration_pairs_v2_null_groups.py` | PRESENT | ~310 |
| `migrate_calibration_pairs_v2_not_null.py` | PRESENT | ~210 |
| `rollback_calibration_pairs_v2_not_null.py` | PRESENT | ~110 |
| `promote_calibration_pairs_v2.py` | PRESENT | ~740 |
| `promote_calibration_v2_stage_to_prod.py` | PRESENT | ~710 |
| `promote_platt_models_v2.py` | PRESENT | ~625 |
| `refit_platt_v2.py` | PRESENT | ~740 |
| `rollback_settlements_v2_era_provenance.py` | PRESENT | ~215 |
| `backfill_settlements_v2_era_provenance.py` | PRESENT | ~390 |

**Total B4 delete: 16 files, 8141 LOC.**

Behavior that must survive in canonical-named scripts before deletion:
- `refit_platt_v2.py` → `refit_platt.py`
- `promote_calibration_pairs_v2.py` + `promote_calibration_v2_stage_to_prod.py` → `promote_calibration.py`
- `promote_platt_models_v2.py` → `promote_platt.py`
- `audit_observation_instants_v2.py` → `audit_observation_instants.py`

---

## 6. Registry entries to delete

From `architecture/db_table_ownership.yaml`:

### 6a. Entries with `schema_class: legacy_archived`

104 total occurrences. Most are canonical runtime tables that have been misclassified in the
ownership YAML. The B7 action is NOT to delete all legacy_archived entries — it is to:
1. Replace `legacy_archived` with `archive` for genuinely retained tables.
2. Delete entries for tables already dropped from DDL.
3. Reclassify active runtime tables to their correct schema_class.

### 6b. Names ending in `_new` or `_old` (listed)

| Table name | In live DDL? | B7 action |
|------------|-------------|-----------|
| `evidence_tier_assignments_new` | NO (not in init_schema) | Delete registry entry |
| `no_trade_events_new` | NO (not in init_schema) | Delete registry entry |

**0 entries end in `_old`.**

### 6c. Names ending in `_v[N]` (20 entries in registry)

Overlap with B3 rename targets. After B3 renames, these registry entries need updating to
canonical names. Key entries:

| Table name | B3 action | Registry action |
|------------|-----------|----------------|
| `calibration_pairs_v2` | rename → `calibration_pairs` | Update name to `calibration_pairs` |
| `ensemble_snapshots_v2` | rename → `ensemble_snapshots` | Update name |
| `settlements_v2` | rename → `settlements` | Update name |
| `market_events_v2` | rename → `market_events` | Update name |
| `platt_models_v2` | rename → `platt_models` | Update name |
| `rescue_events_v2` | rename → `rescue_events` | Update name |
| `observation_instants_v2` | KEEP (parallel tier, §C per V1V2 inventory) | Keep with clarified schema_class |
| `historical_forecasts_v2` | 0 live rows; V1V2 §A candidate | Delete entry after drop |
| `model_bias_ens_v2` | unfinished feature table (V1V2 §E) | Classify before acting |

---

## 7. Estimated LOC impact

| Bundle | Action | LOC |
|--------|--------|-----|
| B2 — `SCHEMA_VERSION` → fingerprint | Delete `check_schema_version.py` (64) + `world_schema_version.yaml` (18) + edit 30 lines in `db.py` | ~112 edits, 82 deleted |
| B3 — `_v2`/`_v1`/`vnext` table renames | ~2625 reference-line edits across src/ + tests/ + scripts/ | ~2625 edits |
| B4 — script/module deletes | 16 files deleted | 8141 deleted |
| B5 — domain field renames | ~2450 line-edits across src/ + tests/ | ~2450 edits |
| B6 — `event_version` removal | ~58 lines deleted/edited | ~58 |
| B7 — registry/docs cleanup | ~200 lines in `architecture/db_table_ownership.yaml` | ~200 edits |

**Total: ~8200 LOC deleted, ~5300 lines edited.**
**Grand total PR3 diff: ~13500 lines changed (delete + edit combined).**

> Note: B3 edits are mechanical renames (2 characters per occurrence `_v2`→``). Many test files
> may be deleted wholesale when their v2 subject is dropped (reduces edit count). Actual PR3
> diff will be smaller if test files for deleted scripts are also deleted rather than renamed.
