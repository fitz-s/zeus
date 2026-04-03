# P-BOUND-01

```yaml
work_packet_id: P-BOUND-01
packet_type: feature_packet
objective: Freeze the repo-local Zeus ↔ Venus / OpenClaw boundary so outer systems remain non-authoritative and only narrow typed contracts and advisory audit surfaces stay live.
why_this_now: Boundary confusion is a live governance risk. The remaining current-phase packets should not proceed under ambiguous repo-vs-host authority assumptions.
why_not_other_approach:
  - Leave boundary rules implicit in chat and operator memory | zero-context agents and outer tools would keep drifting authority inward
  - Push straight into state or rollout work first | later packets would inherit unresolved repo/host ambiguity
truth_layer: The repo-local boundary note and typed supervisor/control contract surfaces describe what Zeus, Venus, and OpenClaw may do in the current phase.
control_layer: Venus may propose narrow ingress and consume derived exports; OpenClaw may host/session-automate externally; neither may mutate repo authority, schema, or canonical truth.
evidence_layer: Boundary note diff, contract map, external-surface assumptions note, targeted manual contract review, and rollback note.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_foundation_package_map.md
  - docs/governance/zeus_openclaw_venus_delivery_boundary.md
  - docs/governance/AGENTS.md
  - src/supervisor_api/contracts.py
  - scripts/audit_architecture_alignment.py
files_may_change:
  - docs/governance/zeus_openclaw_venus_delivery_boundary.md
  - scripts/audit_architecture_alignment.py
  - src/supervisor_api/contracts.py
  - architects_progress.md
  - architects_task.md
  - work_packets/P-BOUND-01.md
files_may_not_change:
  - architecture/**
  - migrations/**
  - src/state/**
  - src/control/**
  - .github/workflows/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - targeted manual contract review
parity_required: false
replay_required: false
rollback: Revert the boundary note, audit script, narrow contract change, and paired Architects ledger updates together.
acceptance:
  - Repo-local boundary law is explicit and current-phase honest.
  - Zeus, Venus, and OpenClaw responsibilities are non-overlapping and non-authoritative where required.
  - No outer host hook or audit surface is allowed to mutate repo authority or schema.
  - External-surface assumptions remain explicit instead of implied.
evidence_required:
  - contract map
  - external-surface assumptions note
  - targeted review notes
  - rollback note
```

## Notes

- This packet is first in the remaining user-ordered current-phase queue.
- If boundary work requires edits to architecture law, stop and freeze a new authority packet.
