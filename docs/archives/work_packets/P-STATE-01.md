# P-STATE-01

```yaml
work_packet_id: P-STATE-01
packet_type: refactor_packet
objective: Remove the highest-risk current-phase state drift by eliminating strategy fallback and authority-sensitive date fallback without widening into schema, control-plane, or authority-law edits.
why_this_now: The tribunal package map and archive/cutover plan both mark these as patch-now drift surfaces that should be cleaned before larger runtime or foundation work.
why_not_other_approach:
  - Leave the drifts in place until foundation mainline work | current-phase operation would stay semantically dishonest
  - Broaden into schema/control refactors now | would overrun the packet boundary and planning lock
truth_layer: Current runtime behavior should stop inventing strategy attribution and stop using implicit local-date fallback in authority-sensitive observation paths.
control_layer: This packet changes no schema or control-plane contract; it only removes high-risk runtime drift inside the named files.
evidence_layer: Before/after behavior note, targeted tests, architecture-contract review, and no-scope-widening note.
zones_touched:
  - K2_runtime
invariants_touched:
  - INV-04
  - INV-06
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_runtime_delta_ledger.md
  - docs/rollout/zeus_authority_cutover_and_archive_plan.md
  - src/state/AGENTS.md
  - architecture/invariants.yaml
  - architecture/negative_constraints.yaml
  - src/state/strategy_tracker.py
  - src/data/observation_client.py
files_may_change:
  - src/state/strategy_tracker.py
  - src/data/observation_client.py
  - targeted tests/docs only
  - architects_progress.md
  - architects_task.md
  - work_packets/P-STATE-01.md
files_may_not_change:
  - migrations/**
  - architecture/**
  - docs/governance/**
  - docs/architecture/**
  - src/control/**
  - src/supervisor_api/**
  - .github/workflows/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - targeted regression tests for strategy fallback removal
  - targeted regression tests for authority-sensitive date fallback removal
parity_required: false
replay_required: false
rollback: Revert the two runtime files, any paired targeted tests/docs, and the paired Architects ledger updates together.
acceptance:
  - No default strategy bucket fallback remains in the targeted state path.
  - No implicit local-date fallback remains in the authority-sensitive observation path.
  - Schema/control scope does not widen.
  - The packet remains limited to the named files plus targeted tests/docs.
evidence_required:
  - before/after behavior note
  - targeted test output
  - no-scope-widening note
  - rollback note
```

## Notes

- This is the first current-phase packet in the queue that touches runtime code.
- If execution discovers schema or control-plane fallout, stop and freeze a new packet.
