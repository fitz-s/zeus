# P2R-EXECUTION-TRUTH-REPAIR

```yaml
work_packet_id: P2R-EXECUTION-TRUTH-REPAIR
packet_type: refactor_packet
objective: Reopen and repair the bottom-layer execution-truth / lifecycle-authority defects that invalidate the prior P2 closure claim: make pending_exit authoritative runtime phase truth, stop reconciliation from flattening/inventing holding-like lifecycle semantics, seal economically_closed/admin_closed/quarantined open/exposure leaks, and fix adjacent low-level execution-truth contradictions discovered during critic review.
why_this_now: Post-closeout review found real contradictions between the P2-closed control claim and current runtime truth. The defects are coupled enough that the user explicitly directed they land as one repair packet rather than as separate follow-up packets.
why_not_other_approach:
  - Keep the prior P2-closed claim and patch around the edges | would leave a false-complete architectural state in repo truth
  - Split the repair into many tiny packets now | conflicts with the explicit user instruction that these belong to one repair package
truth_layer: Execution-truth repair is only honest if runtime lifecycle state, reconciliation behavior, open/exposure semantics, and close/settlement semantics agree on one bottom-layer story.
control_layer: This packet is confined to the minimum runtime/kernel/test/control surfaces needed to restore honest P2 semantics; it does not widen into P3 strategy-policy work or cutover/migration claims.
evidence_layer: Targeted runtime/architecture/db tests, adversarial review, rollback note, and explicit reopen/repair note.
zones_touched:
  - K0_frozen_kernel
  - K2_runtime
invariants_touched:
  - INV-01
  - INV-02
  - INV-07
  - INV-10
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - work_packets/FOUNDATION-TEAM-GATE.md
  - src/contracts/semantic_types.py
  - src/state/portfolio.py
  - src/engine/cycle_runtime.py
  - src/state/chain_reconciliation.py
  - src/engine/lifecycle_events.py
  - src/execution/exit_lifecycle.py
  - src/execution/harvester.py
  - src/state/db.py
  - tests/AGENTS.md
  - tests/test_runtime_guards.py
  - tests/test_live_safety_invariants.py
  - tests/test_architecture_contracts.py
  - tests/test_db.py
files_may_change:
  - work_packets/P2R-EXECUTION-TRUTH-REPAIR.md
  - src/contracts/semantic_types.py
  - src/state/portfolio.py
  - src/engine/cycle_runtime.py
  - src/state/chain_reconciliation.py
  - src/engine/lifecycle_events.py
  - src/execution/exit_lifecycle.py
  - src/execution/harvester.py
  - src/state/db.py
  - tests/test_runtime_guards.py
  - tests/test_live_safety_invariants.py
  - tests/test_architecture_contracts.py
  - tests/test_db.py
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
files_may_not_change:
  - AGENTS.md
  - src/riskguard/**
  - src/control/**
  - src/supervisor_api/**
  - migrations/**
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - .github/workflows/**
  - .claude/CLAUDE.md
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - targeted runtime/architecture/db tests
parity_required: false
replay_required: false
rollback: Revert the repair packet changes and paired slim control-surface updates together; repo truth returns to the prior P2-closed state while the contradiction remains explicitly recorded as unresolved.
acceptance:
  - pending_exit exists as authoritative bottom-layer runtime lifecycle truth rather than only as a derived sidecar mapping
  - reconciliation no longer flattens or injects holding-like lifecycle semantics for pending-exit/quarantine branches
  - economically_closed/admin_closed/quarantined positions no longer leak into open/exposure semantics or active-runtime processing
  - additional low-level contradictions discovered during critic review are either fixed or explicitly narrowed inside this packet before acceptance
  - control surfaces no longer overclaim P2 closure while contradictions remain
evidence_required:
  - targeted pytest output
  - critic findings disposition note
  - rollback note
  - explicit reopen/repair note
```

## Known defect families inside this repair packet

1. `pending_exit` is still not authoritative lifecycle phase truth.
2. Reconciliation still flattens/invents holding-like lifecycle semantics.
3. `economically_closed` still has active/open/exposure leakage.
4. Additional critic-found low-level contradictions are in scope when they are tightly coupled to the same bottom-layer execution-truth repair.
