# Bugfix packet: evaluator microstructure range label

```yaml
work_packet_id: FEAT-BUG-001
packet_type: feature_packet
objective: "Fix the evaluator microstructure logging path so it no longer raises a NameError and misclassifies healthy candidates as MARKET_LIQUIDITY failures."
why_this_now: "Verification on the current Architects branch exposed a real runtime bug: evaluator references an undefined local variable `b` while logging microstructure, causing false MARKET_LIQUIDITY rejections and multiple failing tests."
why_not_other_approach:
  - "Not updating tests to accept the failure: the rejection is caused by a real code bug, not a contract change."
  - "Not widening scope into broader evaluator refactors: the fault is a one-line variable reference bug."
truth_layer: "Runtime evaluator bugfix only; no lifecycle, schema, or control semantics change."
control_layer: "Restores the normal candidate evaluation path by removing a false liquidity error source."
evidence_layer: "Targeted evaluator/runtime tests plus full pytest suite."
zones_touched: [K2_runtime]
invariants_touched: [INV-10]
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_change_control_constitution.md
  - src/engine/AGENTS.md
  - src/engine/evaluator.py
files_may_change:
  - work_packets/FEAT-BUG-001-fix-microstructure-range-label.md
  - src/engine/evaluator.py
files_may_not_change:
  - src/state/**
  - architecture/**
  - docs/governance/**
  - migrations/**
  - tests/**
schema_changes: false
ci_gates_required: []
tests_required:
  - pytest -q tests/test_pnl_flow_and_audit.py::test_inv_kelly_uses_effective_bankroll
  - pytest -q tests/test_runtime_guards.py -k 'gfs_crosscheck_uses_local_target_day_hours_instead_of_first_24h or gfs_crosscheck_failure_rejects_instead_of_defaulting_to_agree'
  - pytest -q
authority_packet: false
parity_required: false
replay_required: false
rollback: "Revert this packet commit to restore the previous evaluator state if needed."
acceptance:
  - "Evaluator no longer raises NameError while logging microstructure."
  - "Affected tests pass without changing their expectations."
  - "Full pytest suite returns green."
evidence_required:
  - "Targeted failing tests green."
  - "Full pytest suite green."
```
