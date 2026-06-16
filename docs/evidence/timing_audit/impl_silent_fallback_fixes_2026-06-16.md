# Silent-Fallback Elimination — Implementation Report
**Date:** 2026-06-16  
**Branch:** live/iteration-2026-06-13 (worktree: timing-fixes)  
**Scope:** Fixes A, B, and C-test from `freshness_fallback_map_2026-06-16.md` §Question 2

---

## Fix A — `src/data/bayes_precision_fusion_capture.py` `_available_after_decision`

**Violation:** Lines 52–77. FAIL-OPEN admits a model when `available_at` is None/empty/unparseable — correct behavior — but the admit was SILENT (no log, no counter).

**Change:** 
- `_available_after_decision` now accepts an optional `model_label` keyword argument.
- Three previously-silent admit branches now emit `logger.warning(...)` with the `model_label` and the reason (`MISSING`, `EMPTY`, or `UNPARSEABLE available_at`). No change to who is admitted.
- `BayesPrecisionFusionCaptureResult` gains a new field `admitted_on_missing_availability: int = 0` (default 0 for backward compatibility). Callers can surface this in telemetry.
- `capture_bayes_precision_instruments` loop now tracks `_missing_avail_count` (incremented when `decision_utc` is set and the model's availability key is absent/None/empty) and passes `model_label=model` to `_available_after_decision`. Counter surfaces in the result.

**Behavior preserved:** WHO is admitted is unchanged — fail-open semantics identical. Only the emit of the warning and counter are new.

**Tests (extended `tests/test_bayes_precision_fusion_arrival_guard.py`):**
- `test_missing_availability_none_emits_warning` — None → WARNING with "MISSING"
- `test_missing_availability_empty_string_emits_warning` — "" → WARNING with "EMPTY"
- `test_unparseable_availability_emits_warning` — bad timestamp → WARNING with "UNPARSEABLE"
- `test_model_label_appears_in_warning` — model name in warning for traceability
- `test_valid_past_timestamp_no_warning` — valid past timestamp: NO warning (normal admit)
- `test_admitted_on_missing_availability_counter` — counter > 0 when availability absent

**Result:** 14/14 passed (8 pre-existing + 6 new). py_compile clean.

---

## Fix B — `src/observability/calibration_coverage_guard.py` season-only silent borrow

**Violation:** Lines 131, 334. The `_platt_resolution` function detects a cross-cluster season-only borrow and returns `"borrowed:<cluster>"`, but the WARNING was only emitted by `assert_calibration_coverage` at boot time. Callers using `calibration_coverage_report()` directly saw no log — the borrow was SILENT at detection time.

**Change:**
- Inside `_check_city_metric_coverage`, after `_platt_resolution` returns a `"borrowed:..."` or `"identity"` resolution, a `logger.warning(...)` is now emitted immediately at detection time — before the `CoverageGap` is appended. The message names the city, metric, season, layer, and uses the `SILENT_FALLBACK=<fallback>` label so it is grep-findable in production.
- Armed mode behavior unchanged (still raises `CalibrationCoverageError` via `assert_calibration_coverage`). The warn path is not conditional on armed/shadow.

**Behavior preserved:** WHO is admitted is unchanged. The `CoverageGap` structure and all existing severity semantics are identical. Only the at-detection-time warning is new.

**Tests (extended `tests/test_calibration_coverage_guard.py`):**
- `test_season_only_borrow_warning_fires_at_detection_non_armed` — calling `calibration_coverage_report()` (not `assert_calibration_coverage`) directly produces a WARNING containing `SILENT_FALLBACK`, `borrowed:`, and the city name.
- `test_season_only_borrow_warning_fires_for_identity_starvation` — identity-starvation also fires `SILENT_FALLBACK` at detection time.

**Result:** 16/16 passed (14 pre-existing + 2 new). py_compile clean.

---

## Fix C — Test for `evaluate_freshness_mid_run` crash fail-close (code already fixed)

**Code already fixed in:** `src/engine/cycle_runner.py` lines 608–633 (`freshness_gate mid_run evaluation FAILED` block).

**New test file:** `tests/test_freshness_gate_crash_failclosed.py`

**Tests:**
- `test_freshness_gate_crash_skips_settlement_day_mode` — `DAY0_CAPTURE` (settlement_day_dispatch_for_mode=True): crashed gate → `skipped=True`, `skip_reason="cycle_skipped_freshness_gate_unevaluable"`, `freshness_gate_error` present.
- `test_freshness_gate_crash_skips_imminent_open_capture` — `IMMINENT_OPEN_CAPTURE` (explicitly fail-closed): crashed gate → same skip semantics.
- `test_freshness_gate_crash_degrades_opening_hunt` — `OPENING_HUNT` (non-fail-closed): crashed gate → `degraded_data=True`, `freshness_gate_error` present, NOT skipped as unevaluable.

Monkeypatch pattern: `monkeypatch.setattr(cr, "evaluate_freshness_mid_run", ...)` on the module attr, mirroring `test_imminent_open_capture.py` and `test_cycle_runner_db_lock_degrade.py`.

**Result:** 3/3 passed. py_compile clean.

---

## Summary

| Fix | Files changed | Tests added | All tests |
|-----|--------------|-------------|-----------|
| A | `src/data/bayes_precision_fusion_capture.py` | 6 new in `test_bayes_precision_fusion_arrival_guard.py` | 14/14 |
| B | `src/observability/calibration_coverage_guard.py` | 2 new in `test_calibration_coverage_guard.py` | 16/16 |
| C (test only) | `tests/test_freshness_gate_crash_failclosed.py` (new) | 3 new | 3/3 |

All touches py_compile clean. No use-vs-refuse decisions changed. Behavior identical except for new log lines and counter.
