# GOV-PACKET-ENTRY-CONTROL-SURFACE

```yaml
work_packet_id: GOV-PACKET-ENTRY-CONTROL-SURFACE
packet_type: governance_packet
objective: Archive the legacy root/architects ledger files as active control surfaces and make the current work packet the live control entry surface.
why_this_now: After the authority-amendment packet, the user explicitly directed that `root_progress.md`, `root_task.md`, `architects_state_index.md`, `architects_task.md`, and `architects_progress.md` should no longer remain active surfaces. Fresh authority review confirms those ledgers are procedural/control artifacts rather than principal authority, and that the cleanest replacement is to treat the current `work_packets/<active-packet>.md` file as the live control entry surface, with `docs/known_gaps.md` remaining the active antibody register.
why_not_other_approach:
  - Keep the ledgers active | directly conflicts with the updated user directive
  - Introduce another new live ledger file | would recreate the same control-surface sprawl under a different name
  - Archive the ledgers immediately without updating references | would strand many work packets and routing files without a clear live entry surface
truth_layer: the live control entry surface should be the current work packet, while root/architects ledgers become historical records.
control_layer: keep this packet bounded to control-surface demotion, archive moves, and reference updates. Do not widen into constitutions, runtime code, or unrelated docs cleanup in this packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, control-surface reference inventory, and a post-move reference scan proving the legacy ledger paths are no longer treated as active control authority.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-03
  - INV-07
required_reads:
  - AGENTS.md
  - architecture/self_check/authority_index.md
  - docs/README.md
  - WORKSPACE_MAP.md
  - root_progress.md
  - root_task.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - docs/known_gaps.md
  - work_packets/GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE.md
files_may_change:
  - work_packets/GOV-PACKET-ENTRY-CONTROL-SURFACE.md
  - AGENTS.md
  - architecture/self_check/authority_index.md
  - docs/README.md
  - WORKSPACE_MAP.md
  - root_progress.md
  - root_task.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - docs/archives/**
  - CURRENT_STATE.md
files_may_not_change:
  - docs/governance/**
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - docs/zeus_FINAL_spec.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/known_gaps.md
  - migrations/**
  - src/**
  - tests/**
  - scripts/**
  - .github/workflows/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required: []
parity_required: false
replay_required: false
rollback: Revert the control-surface replacement and archive moves together; repo returns to the post-close authority-amendment baseline with legacy ledgers still active.
acceptance:
  - the current work packet is explicitly named as the live control entry surface in top routing files
  - `root_progress.md`, `root_task.md`, `architects_state_index.md`, `architects_task.md`, and `architects_progress.md` are archived or explicitly demoted to historical-only surfaces
  - top routing/orientation files no longer point at the superseded ledger paths as active control authority
  - post-move reference scans show remaining mentions of the superseded ledgers are archive/historical only or explicit compatibility-only notes
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - control-surface reference inventory
  - post-move reference scan
```

## Notes

- This packet supersedes the earlier assumption that either `root_*` or `architects_*` ledgers would remain live control surfaces.
- It should not create a new long-lived ledger replacement unless implementation proves a minimal shim is unavoidable.
