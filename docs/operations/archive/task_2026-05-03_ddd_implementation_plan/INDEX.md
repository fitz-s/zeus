# DDD Implementation Plan — Archive Index

**Purpose:** Data Density Discount (DDD) design, calibration, and implementation for forecast skill v2. Complete design cycle from phase1 math analysis through phase2 implementation and rollout.

**Status:** Archived 2026-05-05. Referenced by `src/oracle/data_density_discount.py`, `src/calibration/store.py`, and architecture manifests.

## Top-Level (Design & Execution Plans)

- **PLAN.md** — DDD v2 overall design scope and gate conditions
- **RERUN_PLAN.md** — Phase 1 calibration rerun protocol
- **RERUN_PLAN_v2.md** — Phase 2 forward-fixes (F1 corrections to Platt fit) with authority basis
- **phase2_implementation_log.md** — Execution log from phase 2 rollout
- **review.md** — Design review 1
- **review2.md** — Design review 2 (verdict stage)

## Phase 1 Results (Calibration & Analysis)

### Core Math Outputs
- **MATH_REALITY_OPTIMUM_ANALYSIS.md** — Theoretical optimum for DDD curve shape
- **PHASE1_V2_FINAL_SUMMARY.md** — Final summary with v1 vs v2 comparison
- **V1_VS_V2_REPLAY_SYNTHESIS.md** — Replay analysis comparing old vs new curve
- **v1_vs_v2_replay.md** — Raw replay evidence

### Component Analysis (p2_* files)
- **p2_1_hard_floor_calibration.md** — Hard floor determination per city
- **p2_1_FINAL_v2_per_city_floors.md** — Finalized per-city floor values
- **p2_1_CONCLUSION.md** — Hard floor verdict
- **p2_1b_floor_sensitivity.md** — Sensitivity analysis for floor values
- **p2_1c_sigma_aware_floor.md** — Sigma-aware floor refinement
- **p2_2_k_validation.md** — Kelly criterion validation
- **p2_2_CONCLUSION.md** — Kelly verdict
- **p2_3_sigma_window_acf.md** — ACF analysis for peak window
- **p2_3_CONCLUSION.md** — Peak window verdict
- **p2_4_curve_breakpoints.md** — Curve breakpoint analysis (v1)
- **p2_4_v2_curve_breakpoints.md** — Curve breakpoint analysis (v2)
- **p2_4_CONCLUSION.md** — Curve verdict
- **p2_5_small_sample_floor.md** — Small-sample floor adjustment
- **p2_6_peak_window_radius.md** — Peak window radius tuning
- **p2_rerun_v2_h1_fix.md** — H1 metric fix from v2 rerun

### Crosscutting Issues
- **DST_CLUSTER_INVESTIGATION.md** — DST handling in seasonal clustering

## E8 Audit (Pre-Live Verification)

Evidence for data pipeline, calibration provenance, and live serving integrity:

- **01_calibration_pairs_provenance.md** — Training pair origin audit
- **02_platt_calibrator_fits.md** — Platt fit coefficient audit
- **03_observation_provenance.md** — Observation data source audit
- **04_live_serving_data_path.md** — Live data path validation
- **05_pipeline_causality.md** — Data flow causality check
- **06_intrinsic_time_field_integrity.md** — Time field semantics
- **07_reload_idempotency_evidence.md** — Reload safety
- **08_null_city_floor_behavior.md** — Null city fallback behavior
- **09_data_participation_funnel.md** — Data inclusion/exclusion funnel
- **10_paris_resync_plan.md** — Paris re-inclusion plan
- **11_paris_resync_log.md** — Paris re-inclusion execution log
- **E8_AUDIT_SYNTHESIS.md** — Overall audit verdict

---

**Note:** Scratchpad files (debate, intermediate analysis, discarded branches) consolidated here for traceability. Active truth lives in `src/oracle/data_density_discount.py`, `src/calibration/store.py`, and `architecture/calibration.yaml`.
