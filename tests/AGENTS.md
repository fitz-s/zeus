# tests AGENTS

## WHY this zone matters

Tests defend kernel law and delivery guarantees. Zeus has 68 test files and extensive cross-module invariant tests — these are among the strongest architectural assets in the repo. Breaking a test to "make it pass" is an architectural violation, not a convenience fix.

## File registry

### Architecture-critical tests (break these = code is wrong)

| File | What it tests | Invariant |
|------|--------------|-----------|
| `test_lifecycle.py` | 9-state FSM transitions, `LEGAL_LIFECYCLE_FOLDS` | INV-01, INV-08 |
| `test_architecture_contracts.py` | Cross-module invariant enforcement | Multiple |
| `test_cross_module_invariants.py` | Cross-layer boundary contracts | INV-12 |
| `test_cross_module_relationships.py` | Module dependency rules | Zone boundaries |
| `test_reality_contracts.py` | External assumption contracts | INV-11 |
| `test_provenance_enforcement.py` | Constant registration in cascades | INV-13 |
| `test_no_bare_float_seams.py` | No bare floats at cross-layer seams | INV-12 |
| `test_live_safety_invariants.py` | Pre-live safety gates | Safety |
| `test_truth_surface_health.py` | Truth surface consistency | INV-03 |
| `test_structural_linter.py` | Structural code quality | Code health |

### Signal/probability tests

| File | What it tests |
|------|--------------|
| `test_ensemble_signal.py` | Monte Carlo signal generation (51 members → P_raw) |
| `test_day0_signal.py` | Day-0 observation replaces forecast |
| `test_day0_window.py` | Day-0 window timing |
| `test_day0_exit_gate.py` | Day-0 exit gate logic |
| `test_day0_runtime_observation_context.py` | Day-0 runtime observation context |
| `test_diurnal.py` | Diurnal cycle adjustments |
| `test_model_agreement.py` | Inter-model agreement scoring |
| `test_forecast_uncertainty.py` | Bootstrap σ sources for CI |

### Calibration tests

| File | What it tests |
|------|--------------|
| `test_platt.py` | Extended Platt calibration correctness |
| `test_calibration_manager.py` | Calibration lifecycle, maturity gates |
| `test_calibration_quality.py` | Calibration quality assessment |
| `test_calibration_unification.py` | Calibration unification logic |
| `test_drift.py` | Calibration drift detection |

### Strategy/sizing tests

| File | What it tests |
|------|--------------|
| `test_market_analysis.py` | Edge computation + double-bootstrap CI |
| `test_fdr.py` | FDR filter correctness |
| `test_kelly.py` | Kelly sizing |
| `test_kelly_cascade_bounds.py` | Kelly cascade product bounds [0.001, 1.0] |
| `test_kelly_live_safety_cap.py` | P1 relationship tests: live_safety_cap_usd clip invariant (K3→execution boundary) |
| `test_correlation.py` | Cross-city/bin correlation |
| `test_bootstrap_symmetry.py` | Bootstrap symmetry properties |
| `test_alpha_target_coherence.py` | Alpha target coherence |

### Execution/lifecycle tests

| File | What it tests |
|------|--------------|
| `test_executor.py` | Order execution (paper + live) |
| `test_execution_price.py` | Typed execution price semantics |
| `test_riskguard.py` | Risk level behavior changes (INV-05) |
| `test_entry_exit_symmetry.py` | Entry/exit use same statistical burden |
| `test_exit_authority.py` | Exit decision authority chain |
| `test_churn_defense.py` | 8-layer churn defense |
| `test_force_exit_review.py` | Force exit review process |
| `test_pnl_flow_and_audit.py` | P&L data flow chain invariants |
| `test_tracker_integrity.py` | Strategy tracker integrity |
| `test_strategy_tracker_regime.py` | Strategy tracker regime transitions |
| `test_polymarket_error_matrix.py` | CLOB 429/5xx/timeout error handling: executor rejects gracefully, lifecycle retries not closes |

### Data/ETL tests

| File | What it tests |
|------|--------------|
| `test_ensemble_client.py` | Ensemble data retrieval from DB |
| `test_etl_recalibrate_chain.py` | ETL recalibration chain |
| `test_etl_market_price_history.py` | Market price-history attribution ETL |
| `test_audit_city_data_readiness.py` | City readiness status semantics |
| `test_backfill_openmeteo_previous_runs.py` | Dynamic-city forecast-history backfill from Open-Meteo Previous Runs |
| `test_observation_contract.py` | Observation data contracts |
| `test_observation_instants_etl.py` | Observation instants ETL |
| `test_solar_etl.py` | Solar time ETL |
| `test_cluster_taxonomy_backfill.py` | Cluster taxonomy backfill |
| `test_semantic_snapshot_backfill.py` | Semantic snapshot backfill |

### State/truth tests

| File | What it tests |
|------|--------------|
| `test_db.py` | Database operations |
| `test_truth_layer.py` | Truth layer consistency |
| `test_chronicle_dedup.py` | Chronicle deduplication |
| `test_instrument_invariants.py` | Instrument invariant properties |

### Evaluation/assessment tests

| File | What it tests |
|------|--------------|
| `test_bayesian_sigma_evaluation.py` | Bayesian sigma evaluation |
| `test_lead_sigma_evaluation.py` | Lead-time sigma evaluation |
| `test_sigma_floor_evaluation.py` | Sigma floor evaluation |
| `test_temporal_closure_evaluation.py` | Temporal closure evaluation |
| `test_center_buy_diagnosis.py` | Center buy strategy diagnosis |
| `test_center_buy_repair.py` | Center buy strategy repair |
| `test_divergence_exit_counterfactual.py` | Divergence exit counterfactual |

### Runtime/integration tests

| File | What it tests |
|------|--------------|
| `test_config.py` | Configuration loading |
| `test_healthcheck.py` | System health checks |
| `test_pre_live_integration.py` | Pre-live integration checks |
| `test_runtime_guards.py` | Runtime guard checks |
| `test_runtime_artifact_refresh.py` | Runtime artifact refresh |
| `test_run_replay_cli.py` | Replay CLI invocation |
| `test_replay_time_provenance.py` | Replay time provenance |
| `test_auto_pause_entries.py` | P2 — auto-pause entries on entry-path exception; reason_code, alert, DB persistence, DB-failure resilience, post-entry path continuity (K1+K3) |
| `test_gate_decision.py` | P3 — GateDecision round-trip, reason_refuted conservatism, is_strategy_enabled integration, backward-compat bool gates, set_strategy_gate provenance (K1) |

Note: P6 settlement iterator guard test lives in `test_architecture_contracts.py::test_harvester_settlement_skips_stale_in_memory_pos_when_position_current_shows_settled`.

### Type/unit tests

| File | What it tests |
|------|--------------|
| `test_temperature.py` | Temperature/TemperatureDelta unit safety |
| `test_expiring_assumption.py` | TTL-bound assumption contracts |
| `test_assumptions_validation.py` | Assumption validation |

### Subdirectory

| Path | Purpose |
|------|---------|
| `contracts/` | Spec-owned validation manifests (YAML) |

## Domain rules

- Map tests to invariants or constraints — every test should know what law it defends
- Keep architecture tests small and legible — one failure meaning per test
- Note when a test is transitional or advisory (mark with comment, not xfail)
- Tests in `tests/contracts/` test typed contract enforcement specifically

## Common mistakes

- xfail-ing high-sensitivity architecture tests without a written sunset plan
- Encoding historical doc claims as active law (doc said X, but code never implemented X)
- Treating missing runtime convergence as if the target state already exists
- Deleting failing tests to "pass" CI → conceals real architectural violations
- Writing tests that depend on specific float values instead of invariant properties
