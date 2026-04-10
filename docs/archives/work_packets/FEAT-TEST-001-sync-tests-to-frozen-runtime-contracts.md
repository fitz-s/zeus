# Runtime contract test sync packet

```yaml
work_packet_id: FEAT-TEST-001
packet_type: feature_packet
objective: "Bring runtime tests back in line with the frozen strategy_key and exit-intent contracts already shipped on Architects."
why_this_now: "Full pytest exposed a set of failures caused by tests that still assume pre-freeze semantics: bare exit_intent auto-retry, missing strategy_key on mocked decisions, and exit telemetry chains that omit the explicit EXIT_INTENT event."
why_not_other_approach:
  - "Not weakening the shipped runtime contracts: the recent branch history intentionally froze strategy_key and explicit exit-intent semantics."
  - "Not ignoring failing tests: they currently obscure whether new forecast-layer work is safe."
truth_layer: "Test-suite contract alignment only; no runtime semantics should change in this packet."
control_layer: "Restores signal quality in verification by aligning assertions and test doubles to current branch law."
evidence_layer: "Targeted failing tests green plus full pytest suite green."
zones_touched: []
invariants_touched: [INV-01, INV-04, INV-10]
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_change_control_constitution.md
  - tests/AGENTS.md
  - src/engine/AGENTS.md
  - tests/test_live_safety_invariants.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_runtime_guards.py
  - src/engine/cycle_runtime.py
  - src/execution/exit_lifecycle.py
files_may_change:
  - work_packets/FEAT-TEST-001-sync-tests-to-frozen-runtime-contracts.md
  - tests/test_live_safety_invariants.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_runtime_guards.py
files_may_not_change:
  - src/**
  - architecture/**
  - docs/governance/**
  - migrations/**
schema_changes: false
ci_gates_required: []
tests_required:
  - pytest -q tests/test_live_safety_invariants.py::test_stranded_exit_intent_recovered
  - pytest -q tests/test_pnl_flow_and_audit.py::test_inv_strategy_tracker_receives_trades
  - pytest -q tests/test_runtime_guards.py -k 'trade_and_no_trade_artifacts_carry_replay_reference_fields or execute_discovery_phase_logs_rejected_live_entry_telemetry or strategy_gate_blocks_trade_execution or monitoring_phase_persists_live_exit_telemetry_chain or materialize_position_carries_semantic_snapshot_jsons'
  - pytest -q
parity_required: false
replay_required: false
rollback: "Revert this packet commit to restore the pre-sync test expectations if the underlying runtime contracts are intentionally rolled back."
acceptance:
  - "Tests reflect that bare exit_intent only retries when error-marked."
  - "Mock decisions in affected runtime tests carry valid strategy_key where current contracts require it."
  - "Exit telemetry expectations include the explicit EXIT_INTENT semantic event when current runtime emits it."
  - "Full pytest suite passes without changing runtime code."
evidence_required:
  - "Targeted failing tests green."
  - "Full pytest suite green."
```
