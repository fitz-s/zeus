# P-ROLL-01

```yaml
work_packet_id: P-ROLL-01
packet_type: refactor_packet
objective: Freeze the current-phase migration-delta and archive/cutover documentation so Zeus can continue authority hardening without hiding runtime mismatch.
why_this_now: After boundary clarification and gate installation, rollout truth must stay explicit before state patches or any later cutover planning.
why_not_other_approach:
  - Hide mismatch details in progress notes only | runtime drift would become easy to forget or misstate
  - Attempt runtime code cleanup before recording archive/cutover order | later packets would lack a truthful current-vs-target baseline
truth_layer: The runtime delta ledger and rollout/archive plan are the current-phase truth surfaces for mismatch tracking and archive order.
control_layer: This packet changes no runtime control behavior; it only freezes rollout and rollback order.
evidence_layer: Delta-ledger updates, archive order, rollback note, and review-only proof that runtime code was not touched.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_top_tier_decision_register.md
  - docs/governance/zeus_runtime_delta_ledger.md
  - docs/rollout/zeus_authority_cutover_and_archive_plan.md
  - docs/governance/AGENTS.md
files_may_change:
  - docs/rollout/**
  - docs/governance/zeus_runtime_delta_ledger.md
  - architects_progress.md
  - architects_task.md
  - work_packets/P-ROLL-01.md
files_may_not_change:
  - src/**
  - migrations/**
  - architecture/**
  - .github/workflows/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - review-only verification
parity_required: false
replay_required: false
rollback: Revert rollout docs, delta-ledger edits, and paired Architects ledger updates together.
acceptance:
  - Current-vs-target drift remains explicit in repo-local documents.
  - Archive/cutover order is written down without claiming live cutover readiness.
  - No canonical runtime code is touched.
  - Rollback and archive sequencing are operator-readable.
evidence_required:
  - delta ledger diff
  - archive order note
  - rollback note
```

## Notes

- This packet stays documentation-only unless a narrower paired packet is later approved.
- User-directed queue order puts this after `P-BOUND-01`.
