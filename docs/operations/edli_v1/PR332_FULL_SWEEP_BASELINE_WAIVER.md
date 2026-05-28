# PR332 Full Sweep Baseline Waiver

Status: WAIVED for the capped full-sweep failure set.

PR head: `127ca1d49ca67c5bfe947ff2dfa7872a58d81024`
Current main: `36bdfbaf3ca57861fad2822db785abab5576fcb0`

## Commands

PR head:

```bash
python -m pytest -q --maxfail=20 2>&1 | tee docs/operations/edli_v1/FULL_SWEEP_PR332_HEAD.log
```

Result: FAIL, stopped at cap with 19 failed, 2551 passed, 53 skipped, 19 deselected, 1 xfailed, 1 error.

Current main baseline:

```bash
python -m pytest -q --maxfail=20 \
  tests/contracts/test_settlement_semantics_unit_types.py::test_mypy_rejects_plain_decimal_without_degC_d \
  tests/data/test_ingest_unit_types.py::test_mypy_rejects_plain_float_as_celsius_arg \
  tests/data/test_ingest_unit_types.py::test_mypy_accepts_degc_wrapped_celsius \
  tests/engine/test_crossing_decision.py::TestCrossingDecisionIntegrationFlag::test_flag_off_does_not_call_crossing_decision \
  tests/engine/test_crossing_decision.py::TestCrossingDecisionIntegrationFlag::test_flag_on_without_intent_gate_does_not_call_crossing_decision \
  tests/engine/test_crossing_decision.py::TestCrossingDecisionIntegrationFlag::test_flag_on_with_intent_gate_calls_crossing_decision_with_intended_order_size \
  tests/engine/test_evaluator_unit_types.py::test_mypy_rejects_plain_float_as_celsius_arg \
  tests/engine/test_evaluator_unit_types.py::test_mypy_accepts_degc_wrapped_celsius \
  tests/maintenance_worker/test_rules/test_untracked_top_level_quarantine.py::test_file_under_task_packet_skipped \
  tests/state/test_p2_byte_equivalence.py::TestP2ByteEquivalence::test_world_plus_forecasts_schema_names_match_fixture \
  tests/test_antibody_logs_address.py::test_t2_negrisk_market_negrisk_adapter_in_logs_does_not_fire \
  tests/test_antibody_logs_address.py::test_t3_standard_market_standard_ctf_in_logs_marks_confirmed \
  tests/test_antibody_logs_address.py::test_t4_no_payout_redemption_topic_does_not_fire_antibody \
  tests/test_authority_gate.py::test_rebuild_calibration_requires_verified_observations \
  tests/test_authority_rebuild_invariants.py::test_H2_cycle_runtime_inverse_map_matches_registry_dispatch_modes \
  tests/test_b063_rescue_events_v2.py::TestEmitRescueEventIntegration::test_canonical_position_events_schema_does_not_suppress_rescue_v2 \
  tests/test_backfill_openmeteo_previous_runs.py::test_onboarding_pipeline_materializes_forecast_surfaces_after_source_backfill \
  tests/test_backtest_outcome_comparison.py::test_divergence_row_does_not_mutate_settlement_or_trade_truth \
  tests/test_backtest_outcome_comparison.py::test_trade_history_audit_coverage_counts_only_requested_window \
  tests/maintenance_worker/test_bindings/test_zeus_config.py::test_config_task_allowlist_task_ids_exist_in_catalog \
  2>&1 | tee docs/operations/edli_v1/FULL_SWEEP_PR332_MAIN_BASELINE.log
```

Result: FAIL with the same 19 failed tests and 1 error on current main.

## Failure Classification

| Test | PR result | Main result | Classification | Evidence |
|---|---:|---:|---|---|
| `tests/contracts/test_settlement_semantics_unit_types.py::test_mypy_rejects_plain_decimal_without_degC_d` | FAIL | FAIL | environment-missing | Both logs show `No module named mypy`. |
| `tests/data/test_ingest_unit_types.py::test_mypy_rejects_plain_float_as_celsius_arg` | FAIL | FAIL | environment-missing | Both logs show `No module named mypy`. |
| `tests/data/test_ingest_unit_types.py::test_mypy_accepts_degc_wrapped_celsius` | FAIL | FAIL | environment-missing | Both logs show `No module named mypy`. |
| `tests/engine/test_evaluator_unit_types.py::test_mypy_rejects_plain_float_as_celsius_arg` | FAIL | FAIL | environment-missing | Both logs show `No module named mypy`. |
| `tests/engine/test_evaluator_unit_types.py::test_mypy_accepts_degc_wrapped_celsius` | FAIL | FAIL | environment-missing | Both logs show `No module named mypy`. |
| `tests/maintenance_worker/test_bindings/test_zeus_config.py::test_config_task_allowlist_task_ids_exist_in_catalog` | ERROR | ERROR | environment-missing | Both logs show missing `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml`. |
| `tests/engine/test_crossing_decision.py::TestCrossingDecisionIntegrationFlag::test_flag_off_does_not_call_crossing_decision` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/engine/test_crossing_decision.py::TestCrossingDecisionIntegrationFlag::test_flag_on_without_intent_gate_does_not_call_crossing_decision` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/engine/test_crossing_decision.py::TestCrossingDecisionIntegrationFlag::test_flag_on_with_intent_gate_calls_crossing_decision_with_intended_order_size` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/maintenance_worker/test_rules/test_untracked_top_level_quarantine.py::test_file_under_task_packet_skipped` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/state/test_p2_byte_equivalence.py::TestP2ByteEquivalence::test_world_plus_forecasts_schema_names_match_fixture` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_antibody_logs_address.py::test_t2_negrisk_market_negrisk_adapter_in_logs_does_not_fire` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_antibody_logs_address.py::test_t3_standard_market_standard_ctf_in_logs_marks_confirmed` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_antibody_logs_address.py::test_t4_no_payout_redemption_topic_does_not_fire_antibody` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_authority_gate.py::test_rebuild_calibration_requires_verified_observations` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_authority_rebuild_invariants.py::test_H2_cycle_runtime_inverse_map_matches_registry_dispatch_modes` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_b063_rescue_events_v2.py::TestEmitRescueEventIntegration::test_canonical_position_events_schema_does_not_suppress_rescue_v2` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_backfill_openmeteo_previous_runs.py::test_onboarding_pipeline_materializes_forecast_surfaces_after_source_backfill` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_backtest_outcome_comparison.py::test_divergence_row_does_not_mutate_settlement_or_trade_truth` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |
| `tests/test_backtest_outcome_comparison.py::test_trade_history_audit_coverage_counts_only_requested_window` | FAIL | FAIL | baseline-unrelated | Same test fails on current main. |

## Waiver

No capped full-sweep failure is classified as PR332-caused. The capped full-sweep gate is waived for PR332 forecast no-submit deployment scope on the evidence above.

Operator action required outside PR332:

- Install or route the expected `mypy` dependency for the unit-type tests.
- Restore or intentionally retire the maintenance worker `TASK_CATALOG.yaml` test fixture.
- Triage the baseline-unrelated failures on `origin/main` independently of PR332.
