# Phase 3 → Phase 4 Testeng Learnings

Author: testeng-emma | Date: 2026-04-16 | Scope: cross-phase test-surface audit

---

## 1. Missing Relationship Invariants

**R-missing-A: observation_client → evaluator data contract is untested end-to-end.**
`tests/test_phase3_observation_closure.py` tests the provider return type and the evaluator's rejection label in isolation. No test verifies that a `Day0ObservationContext` produced by `get_current_observation` actually flows into `Day0Signal` without field-name mismatch or unit loss. The cross-module invariant: `Day0ObservationContext.high_so_far` must arrive at `Day0Signal(observed_high_so_far=...)` as the same float with the same unit semantics. Nothing currently fails if a future refactor renames the field on one side.

**R-missing-B: calibration_pairs_v2 high/low row purity is untested at the ingest seam.**
`test_schema_v2_gate_a.py:73-87` (`_insert_calibration_pairs_row`) only verifies that two rows can coexist in the v2 table. It does not test that a write path can't accidentally route a `temperature_metric=high` observation into `observation_field=low_temp` (the INV-14 cross-pairing). The MetricIdentity constructor rejects this at object construction (`test_metric_identity_spine.py:43-54`) but there is no test that the `calibration_pairs_v2` INSERT helper validates the pairing before writing.

**R-missing-C: cities_by_name → daily_obs_append station-resolution has no coverage-completeness test.**
`test_phase3_source_registry_single_truth.py:97-110` checks that `cities_by_name` contains 10 representative cities. It does not test that every city previously in `CITY_STATIONS` (45 entries) now has a WU-lane-eligible entry in `cities_by_name` with a non-None `wu_station`. A city silently dropped from the registry during Phase 3 exec-carol's rewrite would pass the 10-city spot check but disappear from the WU collection loop.

---

## 2. Load-Bearing R-Invariants for Phase 4

Phase 4 scope: GRIB → v2 ingest (`ensemble_snapshots_v2`, `historical_forecasts_v2`), `calibration_pairs_v2` population, Platt refit v2.

**R-4A (dual-track ingest purity):** For every row written to `ensemble_snapshots_v2`, the triple `(temperature_metric, physical_quantity, observation_field)` must satisfy the MetricIdentity pairing law — no cross-pairing may reach the DB. Target file: `tests/test_phase4_v2_ingest.py`.

**R-4B (training_allowed gate at ingest):** Any snapshot row written without a valid `issue_time` must have `training_allowed=0`. A test should write two rows — one with `issue_time=NULL`, one with a real timestamp — and assert `training_allowed` is set correctly by the ingest helper. Target: `tests/test_phase4_v2_ingest.py`.

**R-4C (calibration_pairs_v2 no-mix):** A `calibration_pairs_v2` INSERT helper must reject `(temperature_metric='high', observation_field='low_temp')` at the call site, not rely on the DB UNIQUE constraint to catch it (UNIQUE catches duplicates, not cross-pairings). Target: `tests/test_phase4_calibration_purity.py`.

**R-4D (Platt refit v2 family isolation):** A Platt model fitted on high-track calibration pairs must not share its `model_key` with a low-track model. Target: `tests/test_phase4_platt_v2.py`.

---

## 3. Fixture Patterns / Anti-Patterns Observed

**Anti-pattern A: `isinstance(result, dict)` fallback branches in R-F tests.**
`test_phase3_observation_closure.py:130-145` (WU provider test) originally had `if isinstance(result, dict): assert "low_so_far" in result`. This made the test pass for both old dict returns and new typed returns — a structural false-positive. The test was correct only for the new contract. Rule: never dual-gate a type-seam assertion; pick the post-implementation type and fail hard if it's wrong.

**Anti-pattern B: WU fixture timestamps without local-date verification.**
The original WU fixture used 2024 epoch values (`1713225600`) with `target_date=2026-04-16`. The date filter in `_select_local_day_samples` correctly rejected them, causing `ObservationUnavailableError` instead of the expected assertion failure. Root cause: fixtures with hardcoded epochs are invisible to timezone bugs. Rule: always derive fixture epochs from explicit local datetime + timezone, or add an inline comment with the resolved local time.

**Anti-pattern C: source-inspection tests are brittle on refactors.**
`test_phase3_observation_closure.py:481-490` (`test_evaluator_low_reject_branch_rejection_stage_label`) uses `inspect.getsource(ev_mod)` to assert a string literal is present. These tests pass when the string exists anywhere in the file, including in comments. They fail silently on dead code removal. Prefer importing and calling the function under test over string-scanning the source.

---

## 4. Phase 0b Stubs: Skip Status

Current skipped stubs in `tests/test_dual_track_law_stubs.py`:

| Test | Skip reason | Safe to un-skip in Phase 4? |
|------|------------|----------------------------|
| `test_no_high_low_mix_in_platt_or_bins` (NC-12, line 67) | "pending: enforced in Phase 7 rebuild" | **No** — Platt refit is Phase 7 |
| `test_kelly_input_carries_distributional_info` (NC-14, line 127) | "pending: enforced pre-Phase 9 activation" | **No** — Kelly price law is Phase 9 |
| `test_red_triggers_active_position_sweep` (INV-19, line 164) | "pending: enforced in risk phase before Phase 9" | **No** — risk layer is pre-Phase 9 |
| `test_load_portfolio_degrades_gracefully_on_authority_loss` (INV-20, line 230) | "pending: enforced with Phase 6 runtime split" | **No** — graceful degrade is Phase 6 |

None of the four skipped stubs are safe to un-skip in Phase 4. The Phase 4 work (v2 schema ingest, calibration_pairs_v2, Platt refit scaffolding) does not land the implementations these stubs depend on.

---

## 5. Test File Sprawl Assessment

**`tests/test_dual_track_law_stubs.py`** — should eventually be split. It currently conflates: NC-11/schema (Phase 2, already live), NC-13/DT#1 (Phase 2, already live), NC-15/INV-22 (Phase 1, already live), NC-12 (Phase 7), NC-14 (Phase 9), INV-19 (Phase 9), INV-20 (Phase 6). The live tests should be graduated into phase-specific files once their phases close; the stubs should remain here as a single forward-reference registry. No immediate split needed; flag for Phase 7 cleanup.

**`tests/test_phase3_observation_closure.py`** — scope is now correct after the Phase 6 causality tests were moved out. No further split needed.

**`tests/test_phase6_causality_status.py`** — 3 tests, correctly scoped. Will need 2-3 more tests when Phase 6 lands (nowcast path routing, N/A_CAUSAL slot → nowcast not Platt). Not a sprawl risk yet.

**`tests/test_fdr.py` + `tests/test_fdr_family_scope.py`** — these two files cover overlapping FDR territory. `test_fdr.py` tests the BH filter math; `test_fdr_family_scope.py` tests the family ID grammar. They are correctly separated by concern. No merge needed.
