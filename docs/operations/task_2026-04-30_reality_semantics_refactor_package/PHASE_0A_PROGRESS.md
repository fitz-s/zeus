# Phase 0/A Progress

Status: first guardrail slice implemented.

## Slice Completed

Topology admission can now recognize the pricing/reality semantics refactor:

- added digest profile `pricing semantics authority cutover`
- added profile tests for admitted Phase 0/A files and blocked live/prod side-effect scope
- regenerated `architecture/digest_profiles.py` from canonical `architecture/topology.yaml`

Authority and tests now register the first corrected-semantics guardrails:

- `INV-33` / `NC-20`: corrected posterior modes do not accept raw quote/VWMP vectors
- `INV-34` / `NC-21`: implied probability, even fee-adjusted, is not corrected Kelly cost authority
- `INV-35` / `NC-22`: `FinalExecutionIntent` contract shape carries submit-ready final-limit lineage without posterior/VWMP recompute inputs
- `INV-36` / `NC-23`: monitor held-token quote refresh stays out of corrected posterior prior evidence

## Evidence

Passing checks:

- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py`
- `pytest -q -p no:cacheprovider tests/test_architecture_contracts.py::test_pricing_semantics_guardrail_law_is_registered tests/test_no_bare_float_seams.py`
- `python3 scripts/digest_profiles_export.py --check`
- `python3 scripts/topology_doctor.py --schema`
- `python3 scripts/topology_doctor.py --invariants --json`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files tests/test_no_bare_float_seams.py tests/test_architecture_contracts.py --json`
- `pytest -q -p no:cacheprovider tests/test_executable_market_snapshot_v2.py tests/test_execution_intent_typed_slippage.py tests/test_market_analysis.py tests/test_executor.py tests/test_runtime_guards.py`
- `python3 -m py_compile src/strategy/market_fusion.py src/contracts/execution_intent.py src/contracts/execution_price.py src/execution/executor.py src/engine/monitor_refresh.py tests/test_no_bare_float_seams.py tests/test_digest_profile_matching.py tests/test_architecture_contracts.py`
- `pytest -q -p no:cacheprovider tests/test_load_platt_v2_data_version_filter.py tests/test_evaluator_explicit_n_mc.py tests/test_digest_profile_matching.py tests/test_architecture_contracts.py tests/test_no_bare_float_seams.py tests/test_executable_market_snapshot_v2.py tests/test_execution_intent_typed_slippage.py tests/test_market_analysis.py tests/test_executor.py tests/test_executor_command_split.py tests/test_exit_safety.py tests/test_harvester_metric_identity.py tests/test_harvester_dr33_live_enablement.py tests/test_collateral_ledger.py tests/test_day0_runtime_observation_context.py tests/test_model_agreement.py tests/test_k3_slice_p.py tests/test_k6_slice_n.py tests/test_k8_slice_r.py tests/test_lifecycle.py tests/test_phase9c_gate_f_prep.py tests/test_riskguard_red_durable_cmd.py tests/test_run_replay_cli.py tests/test_runtime_guards.py tests/test_v2_adapter.py` -> 838 passed, 23 skipped
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files tests/test_no_bare_float_seams.py tests/test_architecture_contracts.py tests/test_digest_profile_matching.py tests/test_evaluator_explicit_n_mc.py tests/test_load_platt_v2_data_version_filter.py --json`

Review gates:

- verifier pass: APPROVE for admission, law registration, freshness metadata,
  package registration, and focused tests
- code-reviewer pass: initial REVISE on plan scope / INV-35 scope / monitor
  test breadth; fixes applied; re-review APPROVE

## Not Completed

This slice does not implement runtime rewiring. Legacy executor limit
computation, monitor/exit executable proceeds, persistence, reporting, shadow,
canary, and promotion evidence remain later phases under `WORKFLOW.md`.

No live venue submission, production DB mutation, schema migration apply,
source-routing change, config flip, or live strategy promotion was performed.

Known global workspace drift observed during closeout: `topology_doctor --tests`
still reports pre-existing unclassified tests outside this slice, and
`topology_doctor --docs --issues-scope all` reports current-state/archive
references outside this package. The files introduced by this slice have
freshness headers and test-topology registration.
