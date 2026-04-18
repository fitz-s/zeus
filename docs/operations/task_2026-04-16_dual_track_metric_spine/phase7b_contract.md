# Phase 7B Contract — Naming Hygiene

**Issued**: 2026-04-18 post P7A truly closed at `a6b87bf`. Gen-Verifier mode.
**Basis**: operating contract P1-P7 + P1.1/P2.1/P3.1 amendments. Critic-beth P7A forward-log.
**Target**: ONE commit. Mechanical debt pay-down. No new features, no semantics changes.

## Deliverables (single commit)

### 1. Extract `CalibrationMetricSpec` + `METRIC_SPECS` (MINOR-2)
- NEW `src/calibration/metric_specs.py` containing the dataclass + tuple.
- Update imports in 3 scripts: `rebuild_calibration_pairs_v2.py`, `refit_platt_v2.py`, `backfill_tigge_snapshot_p_raw_v2.py`. Remove cross-script `from scripts.rebuild_calibration_pairs_v2 import ...`.

### 2. Extract `_tigge_common.py` (P6 forward-log)
- NEW `scripts/_tigge_common.py` with the 15 PURE-MECHANICAL helpers (verified parallel-identical in both extractors):
  `_ceil_to_next_6h, _local_day_bounds_utc, _overlap_seconds, _issue_utc_from_fields, _now_utc_iso, _city_slug, _file_sha256, _output_path, _get_city_config, _cross_validate_city_manifests, _load_cities_config, _parse_steps_from_filename, _parse_dates_from_dirname, _find_region_pairs, _iter_overlap_local_dates`.
- **DO NOT extract** `compute_required_max_step` / `_compute_required_max_step` / `compute_manifest_hash` / `_compute_manifest_hash` / `compute_causality` / `_compute_causality_low` — semantics-divergent (HIGH vs LOW causality has different step rounding). Leave in-place with `# DELIBERATELY_NOT_EXTRACTED: LOW variant diverges` comment at each site.
- Update `scripts/extract_tigge_mx2t6_localday_max.py` + `scripts/extract_tigge_mn2t6_localday_min.py` to `from scripts._tigge_common import ...` — delete duplicated defs in both.

### 3. Remove `remaining_member_maxes_for_day0` alias (P6 forward-log)
- Delete the shim at `src/signal/day0_window.py:76-90`.
- Update 9 call sites across 4 test files:
  - `tests/test_day0_window.py:5, 17, 39` — import + 2 call sites → `remaining_member_extrema_for_day0`, adapt return shape (dataclass → `.maxes` / `.mins`)
  - `tests/test_execution_price.py:372` — monkeypatch string-form
  - `tests/test_fdr.py:434, 577, 721` — 3 monkeypatch string-form sites
  - `tests/test_runtime_guards.py` — grep-confirm any sites (per planner 2 monkeypatch latent sites)

### 4. Register 5 scripts in `architecture/script_manifest.yaml`
- `scripts/extract_tigge_mx2t6_localday_max.py` — class `etl_writer`
- `scripts/extract_tigge_mn2t6_localday_min.py` — class `etl_writer`
- `scripts/rebuild_calibration_pairs_v2.py` — class `repair`, apply_flag `--force`, target_db `state/zeus-world.db`
- `scripts/refit_platt_v2.py` — class `repair`, apply_flag `--force`, target_db `state/zeus-world.db`
- `scripts/backfill_tigge_snapshot_p_raw_v2.py` — class `repair`, apply_flag `--force`, target_db `state/zeus-world.db`

### 5. Drop unused schema columns (MINOR-1)
- `src/state/schema/v2_schema.py`: remove the 3 P7A ADD COLUMN lines (`contract_version`, `boundary_min_value`, `unit`). They are unused by live code today; Phase 8 will re-add when consumers exist. Drop reduces surface.
- Update `tests/test_phase7a_metric_cutover.py` fixture to stop referencing `contract_version` / `boundary_min_value` / `unit` in INSERT. Keep R-BM/R-BN/R-BO structurally correct post-removal.

### 6. Replace R-AZ-2 mirror test (P7A MAJOR-3)
- `tests/test_phase5_gate_d_low_purity.py:186-215` currently uses `try/except: pass` to swallow TypeError from stale `rebuild_v2(..., stats=stats)` kwarg. Replace with real end-to-end invocation: synthetic LOW-observation fixture, LOW-snapshot fixture, call `rebuild_v2(conn, spec=low_spec, dry_run=False, force=True, n_mc=200)`, assert `temperature_metric='high'` row count is zero AND `temperature_metric='low'` row count > 0.

## Acceptance gates

1. `pytest tests/test_phase7a_metric_cutover.py tests/test_phase6_day0_split.py tests/test_metric_identity_spine.py tests/test_phase5_gate_d_low_purity.py tests/test_day0_window.py tests/test_execution_price.py tests/test_fdr.py --tb=short -q` → ALL GREEN
2. Full regression: `pytest tests/ --tb=no -q --ignore=tests/test_pnl_flow_and_audit.py` → ≤ 125 failed / ≥ 1805 passed. ZERO new failures. Topology_doctor tests **may drop** to fewer failures (script_manifest registration should unblock `test_topology_scripts_mode_covers_all_top_level_scripts`). Bonus if they do.
3. `grep -rn "remaining_member_maxes_for_day0" src/ tests/ scripts/ --include='*.py'` → zero hits
4. `grep -rn "from scripts.rebuild_calibration_pairs_v2 import" scripts/` → zero hits (METRIC_SPECS cross-script import eliminated)
5. critic-beth one-shot review PASS

## Hard constraints — DO NOT

- Modify `_tigge_common` extracted helpers semantics — mechanical extract only, diff should be `+def foo(): body` in `_tigge_common.py` + `-def foo(): body` in both extractors + import-from-common
- Change any `compute_*` / `_compute_*` variant — flagged DELIBERATELY_NOT_EXTRACTED
- Touch `validate_snapshot_contract`, `PortfolioState.authority`, `ModeMismatchError`, `CalibrationMetricSpec` internals (locked)
- Re-add `contract_version` / `boundary_min_value` columns elsewhere (P8 consumer when landed)
- `git add` / `git commit` yourself (P2.1)

## Pointers

1. This contract
2. P7A `critic_beth_phase7a_wide_review.md` (§Forward-log + §MINOR-1/MINOR-2/MAJOR-3 descriptions)
3. Operating contract P1.1/P2.1/P3.1

Phase 7B closes when critic PASS. P8 scope updates then.
