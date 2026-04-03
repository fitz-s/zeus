# P-OPS-01

```yaml
work_packet_id: P-OPS-01
packet_type: feature_packet
objective: Freeze the operator command, runbook, and cookbook surfaces so repo-local operating guidance stays aligned with the actual current-phase Zeus workflow and packet discipline.
why_this_now: Once the remaining current-phase packets close, operators need a stable repo-local source of truth for command paths, recovery, and daily execution before team mode opens.
why_not_other_approach:
  - Keep relying on operator memory and external docs | command drift and team misuse would reappear quickly
  - Open team mode before operator docs are stable | execution lanes would inherit unclear shutdown, recovery, and packet-entry practice
truth_layer: Repo-local runbook, cookbook, and first-phase plan define how current-phase Zeus work is started, reviewed, recovered, and handed off.
control_layer: This packet updates operator guidance only; it does not change manifests, schema, or runtime truth ownership.
evidence_layer: Command/source update note, manual review, and explicit current-phase-vs-end-state wording.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_top_tier_decision_register.md
  - docs/governance/zeus_omx_omc_command_cookbook.md
  - docs/governance/zeus_omx_omc_operator_runbook.md
  - docs/governance/zeus_first_phase_execution_plan.md
  - docs/governance/AGENTS.md
files_may_change:
  - docs/governance/zeus_omx_omc_*
  - docs/governance/zeus_first_phase_execution_plan.md
  - architects_progress.md
  - architects_task.md
  - work_packets/P-OPS-01.md
files_may_not_change:
  - architecture/**
  - migrations/**
  - src/**
  - .github/workflows/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - manual operator review
parity_required: false
replay_required: false
rollback: Revert the operator docs touched by this packet and the paired Architects ledger updates together.
acceptance:
  - Repo-local runbook/cookbook reflect the actual current-phase command path.
  - Packet-first and recovery discipline are explicit.
  - Operator docs do not overclaim end-state convergence.
  - Team entry remains gated behind approved packet discipline.
evidence_required:
  - command/source update note
  - manual review note
  - rollback note
```

## Notes

- User-directed queue order places this after `P-STATE-01`, even though package-map dependencies allow some operator work earlier.
- After this packet family closes, the next program step is the foundation-mainline architecture plan and team-readiness preparation.
