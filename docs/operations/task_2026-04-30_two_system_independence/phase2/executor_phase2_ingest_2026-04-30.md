# Phase 2 Ingest Deliverables — Executor Report

**Date:** 2026-04-30
**Branch:** claude/quizzical-bhabha-8bdc0d
**Tests:** 126 passed (44 new + 82 regression), 0 failed

---

## Files Created

- `src/data/source_health_probe.py` — §2.1 source health probe loop
- `src/calibration/drift_detector.py` — §2.2 DriftReport + compute_drift
- `src/calibration/retrain_trigger_v2.py` — §2.2 check_and_arm_refit
- `src/data/ingest_status_writer.py` — §2.5 ingest_status.json rollup
- `tests/test_source_health_probe.py` — Antibody #5 (12 tests)
- `tests/test_drift_detector_threshold.py` — Antibody #SC-6 (13 tests)
- `tests/test_ingest_provenance_contract.py` — Antibody #9 (19 tests)

## Files Modified

- `src/data/ingestion_guard.py` — ProvenanceGuard + ProvenanceViolation added at end; backward-compat apply_legacy_read_tolerance() for SC-3
- `src/data/dual_run_lock.py` — Added source_health, drift_detector, ingest_status to _KNOWN_TABLES
- `src/ingest_main.py` — 3 new tick functions + 3 scheduler.add_job entries appended END of scheduled-jobs section (no conflict with Phase 1.5 harvester_truth_writer_tick)

---

## Deliverable Summary

### A. Source health probe (§2.1)
- `probe_all_sources(timeout=10.0, _prior_state=None)` probes 7 sources: open_meteo_archive, wu_pws, hko, ogimet, ecmwf_open_data, noaa, tigge_mars (MANUAL_OPERATOR)
- `write_source_health()` writes `state/source_health.json` atomically with `written_at` top-level key
- `_source_health_probe_tick` scheduled every 10 minutes, wrapped in `acquire_lock("source_health")`
- Prior state loaded from existing file for consecutive_failures accumulation

### B. Drift detector (§2.2)
- `compute_drift(world_conn, *, city, season, metric_identity, window_days=7)` computes rolling Brier vs baseline Brier (90-day lookback BEFORE the window)
- REFIT_NOW if delta > 0.01 OR n_settlements >= 50 in window
- WATCH if 0.005 < delta <= 0.01; OK otherwise
- `check_and_arm_refit(world_conn)` writes `state/refit_armed.json` with bucket list
- `_drift_detector_tick` scheduled daily UTC 06:00, wrapped in `acquire_lock("drift_detector")`

### C. IngestionGuard ProvenanceGuard (§2.4)
- `ProvenanceGuard.validate_write(source, authority, data_version, provenance_json)` enforces all 4 fields at write time
- `authority` must be VERIFIED|UNVERIFIED|QUARANTINED; `provenance_json` must have request_url, fetched_at, parser_version
- `ProvenanceGuard.apply_legacy_read_tolerance(row)` tags absent fields as legacy_v0/UNVERIFIED at read time (SC-3)
- Existing `IngestionGuard` class untouched

### D. Ingest status rollup (§2.5)
- `write_ingest_status(world_conn)` queries observation_instants, forecasts, solar_daily, ensemble_snapshots, data_coverage
- Computes rows_last_hour, rows_last_day, holes_by_city_count per table
- Reads source_health.json and includes summary + last_quarantine_reason
- `_ingest_status_rollup_tick` scheduled every 5 minutes, wrapped in `acquire_lock("ingest_status")`
- Both K2 tick completions and dedicated rollup tick write the file

### E. Coordination with Phase 1.5
- New ticks appended AFTER `_harvester_truth_writer_tick` in ingest_main.py
- No modifications to `_harvester_cycle`, `harvester_truth_writer.py`, `harvester_pnl_resolver.py`

---

## Blockers / Notes

- Caller modules (daily_obs_append, hourly_instants_append, forecasts_append, solar_append) do NOT currently pass `ProvenanceGuard.validate_write` at write time — they predate this requirement. Per design SC-3, legacy writes continue to work; the guard is available for new callers and tested via antibody #9. Wiring existing appenders to the guard is a follow-up slice (would require tracing all write paths and adding provenance tracking to each).
- `test_settlements_physical_quantity_invariant.py` (untracked file visible in git status) not in regression scope per operator task spec.

---

## Verification

```
ZEUS_MODE=live pytest tests/test_source_health_probe.py tests/test_drift_detector_threshold.py \
  tests/test_ingest_provenance_contract.py tests/test_platt*.py tests/test_phase4_platt_v2.py \
  tests/test_load_platt_v2_data_version_filter.py tests/test_evaluator_explicit_n_mc.py \
  tests/test_runtime_n_mc_floor.py tests/test_live_safe_strategies.py \
  tests/test_trading_isolation.py tests/test_world_writer_boundary.py \
  tests/test_dual_run_lock_obeyed.py tests/test_ingest_isolation.py

126 passed, 2 warnings in 4.04s
```
