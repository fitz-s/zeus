# Phase 0/A Progress

Status: first guardrail slice implemented.

## Worktree Continuation: Corrected Submit Path

Status: first corrected executor bridge landed in
`worktree/reality-semantics-refactor`.

Additional completed slice: buy_no exit quote split.

- `src/execution/exit_triggers.py` now threads held-token `best_bid` into the
  buy_no reversal EV gate
- buy_no exit EV comparison uses held-token sell quote when available; it no
  longer reads `current_edge_context.p_market[0]` as a sell-value proxy
- runtime guard tests lock both sides of the split: a high `p_market` vector
  cannot force a buy_no exit when held-token `best_bid` is uneconomic, and a
  low `p_market` vector cannot block an exit when held-token `best_bid` beats
  hold value
- no lifecycle, state, config, schema, venue, or live side-effect surface was
  changed in this slice

Additional completed slice: runtime corrected live gate.

- added default-off `CORRECTED_PRICING_LIVE_ENABLED` runtime gate in
  `src/engine/cycle_runtime.py`
- when the gate is false or absent, live entry continues through the legacy
  `create_execution_intent()` / `execute_intent()` path and records legacy
  corrected-shadow comparison evidence as before
- when the gate is true, live entry reconstructs an immutable
  `FinalExecutionIntent` from corrected shadow evidence and calls
  `deps.execute_final_intent()` without passing posterior, VWMP, or `BinEdge`
  inputs into the corrected submit path
- corrected live branch rejects not-submit-ready shadow evidence before legacy
  submit creation/execution, preserving fail-closed behavior for passive
  nonmarketable candidates
- production runtime remains non-promoted: `cycle_runner.py` was not rewired in
  this slice, config was not changed, and no live venue/prod side effect was
  performed

Additional completed slice: corrected executor bridge.

- added `execute_final_intent()` as the corrected entry submit boundary in
  `src/execution/executor.py`
- `execute_final_intent()` consumes `FinalExecutionIntent` plus live submission
  authority context; it does not accept posterior, VWMP, `BinEdge`, or legacy
  label inputs
- corrected bridge maps final token, final limit price, executable snapshot
  lineage, min tick/min order metadata, `neg_risk`, order type, source context,
  and quantized BUY shares into the existing `_live_order` command path
- `_live_order` now rejects corrected entry intents if the final order type
  conflicts with the allocator-selected venue order type, or if post-only entry
  submission would otherwise be silently dropped
- corrected pricing shadow now marks marketable sweep evidence with
  `depth_proof_source="CLOB_SWEEP"` and only opens PASS-depth construction on
  that sweep path
- corrected pricing shadow now materializes `FinalExecutionIntent` evidence
  when the candidate is submit-ready; passive/nonmarketable candidates remain
  `not_submit_ready` and keep `live_submit_authority=false`

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
- `/Users/leofitz/miniconda3/bin/python3 -m compileall -q src/contracts/execution_intent.py src/execution/executor.py src/engine/cycle_runtime.py`
- `/Users/leofitz/miniconda3/bin/python3 -m pytest tests/test_executor.py tests/test_executable_market_snapshot_v2.py tests/test_execution_intent_typed_slippage.py tests/test_runtime_guards.py -q` -> 263 passed, 1 skipped
- `/Users/leofitz/miniconda3/bin/python3 -m pytest tests/test_runtime_guards.py tests/test_executor.py tests/test_executable_market_snapshot_v2.py -q` -> 255 passed, 1 skipped
- `python -m pytest tests/test_runtime_guards.py::test_live_reprice_binds_intent_limit_when_dynamic_gap_would_not_jump tests/test_runtime_guards.py::test_live_corrected_pricing_uses_final_intent_when_flag_enabled tests/test_runtime_guards.py::test_live_corrected_pricing_rejects_not_submit_ready_shadow_without_legacy_submit -q` -> 3 passed
- `python -m pytest tests/test_runtime_guards.py tests/test_executor.py tests/test_executable_market_snapshot_v2.py tests/test_execution_intent_typed_slippage.py -q` -> 265 passed, 1 skipped
- `python -m compileall -q src/engine/cycle_runtime.py tests/test_runtime_guards.py`
- `python scripts/topology_doctor.py --freshness-metadata --changed-files tests/test_runtime_guards.py --json`
- `python scripts/topology_doctor.py --planning-lock --changed-files src/engine/cycle_runtime.py tests/test_runtime_guards.py docs/operations/task_2026-04-30_reality_semantics_refactor_package/PHASE_0A_PROGRESS.md --plan-evidence docs/operations/task_2026-04-30_reality_semantics_refactor_package/WORKFLOW.md`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files src/engine/cycle_runtime.py tests/test_runtime_guards.py docs/operations/task_2026-04-30_reality_semantics_refactor_package/PHASE_0A_PROGRESS.md`
- `python -m pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_pricing_semantics_authority_cutover_routes_to_refactor_profile tests/test_digest_profile_matching.py::test_pricing_semantics_authority_cutover_blocks_live_side_effect_scope tests/test_no_bare_float_seams.py tests/test_architecture_contracts.py` -> 109 passed, 22 skipped
- `python scripts/digest_profiles_export.py --check`
- `python scripts/topology_doctor.py --schema`
- `python -m pytest tests/test_runtime_guards.py::test_buy_no_exit_ev_gate_uses_held_token_best_bid_not_p_market_vector tests/test_runtime_guards.py::test_buy_no_exit_ev_gate_allows_sell_when_best_bid_beats_hold_value tests/test_churn_defense.py tests/test_lifecycle.py::TestExitTriggers -q` -> 24 passed
- `python -m pytest tests/test_runtime_guards.py tests/test_churn_defense.py tests/test_lifecycle.py tests/test_entry_exit_symmetry.py tests/test_instrument_invariants.py -q` -> 253 passed
- `python -m compileall -q src/execution/exit_triggers.py tests/test_runtime_guards.py`
- `python scripts/topology_doctor.py --freshness-metadata --changed-files tests/test_runtime_guards.py --json`
- `python scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_triggers.py tests/test_runtime_guards.py docs/operations/task_2026-04-30_reality_semantics_refactor_package/PHASE_0A_PROGRESS.md --plan-evidence docs/operations/task_2026-04-30_reality_semantics_refactor_package/WORKFLOW.md`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode closeout --changed-files src/execution/exit_triggers.py tests/test_runtime_guards.py docs/operations/task_2026-04-30_reality_semantics_refactor_package/PHASE_0A_PROGRESS.md`

Known adjacent failure observed outside the buy_no quote split:

- `python -m pytest tests/test_runtime_guards.py tests/test_churn_defense.py tests/test_lifecycle.py tests/test_entry_exit_symmetry.py tests/test_pre_live_integration.py tests/test_instrument_invariants.py -q` -> 1 failed in `tests/test_pre_live_integration.py::test_full_monitoring_pipeline`; this exercises buy_yes monitoring pipeline dirtiness and was not changed by the buy_no exit quote split

Review gates:

- verifier pass: APPROVE for admission, law registration, freshness metadata,
  package registration, and focused tests
- code-reviewer pass: initial REVISE on plan scope / INV-35 scope / monitor
  test breadth; fixes applied; re-review APPROVE
- critic pass for commit `d07d944`: PASS on default-off corrected live gate,
  fail-closed shadow handling, no config promotion, no cycle_runner injection,
  and no live/prod side effects

## Not Completed

This slice does not implement runtime rewiring. Legacy executor limit
computation, monitor/exit executable proceeds, persistence, reporting, shadow,
canary, and promotion evidence remain later phases under `WORKFLOW.md`.

The buy_no quote split in `src/execution/exit_triggers.py` does not yet route
through the state-owned `Position._buy_no_exit` path; that remains a separate
scope requiring its own topology admission.

No live venue submission, production DB mutation, schema migration apply,
source-routing change, config flip, or live strategy promotion was performed.

Known global workspace drift observed during closeout: `topology_doctor --tests`
still reports pre-existing unclassified tests outside this slice, and
`topology_doctor --docs --issues-scope all` reports current-state/archive
references outside this package. The files introduced by this slice have
freshness headers and test-topology registration.
