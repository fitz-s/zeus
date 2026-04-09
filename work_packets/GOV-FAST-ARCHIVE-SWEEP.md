# GOV-FAST-ARCHIVE-SWEEP

```yaml
work_packet_id: GOV-FAST-ARCHIVE-SWEEP
packet_type: governance_packet
objective: Rapidly archive the remaining clearly historical top-level docs and legacy root artifacts so the live repo surface is reduced to a small set of active authority and runtime-entry files.
why_this_now: After the root authority guide and control-surface consolidation, the user explicitly directed a much faster cleanup posture: archive almost everything that is not still needed, instead of preserving scattered historical files in the root or top-level docs surface.
why_not_other_approach:
  - Leave the remaining files where they are | keeps the repo visually noisy and contradicts the user's cleanup directive
  - Reclassify the remaining files one by one over many packets | too slow for this stage and preserves clutter longer than necessary
  - Delete the historical files outright | loses provenance that the repo still benefits from retaining
truth_layer: historical analysis, design, migration, and artifact files remain useful as provenance, but they are not live authority and should live under `docs/archives/**`.
control_layer: keep this packet bounded to bulk archive moves of clearly historical files plus the minimal pointer/reference updates required to keep the cleaned repo navigable. Do not widen into governance constitutions, code behavior, or runtime state.
evidence_layer: before/after top-level file inventory, targeted reference scan for moved files, and standard packet/manifests gates.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - AGENTS.md
  - ZEUS_AUTHORITY.md
  - architecture/self_check/authority_index.md
  - docs/README.md
  - CURRENT_STATE.md
  - WORKSPACE_MAP.md
  - work_packets/GOV-PACKET-ENTRY-CONTROL-SURFACE.md
  - work_packets/GOV-ROOT-AUTHORITY-GUIDE.md
files_may_change:
  - work_packets/GOV-FAST-ARCHIVE-SWEEP.md
  - WORKSPACE_MAP.md
  - docs/README.md
  - CURRENT_STATE.md
  - docs/archives/**
  - docs/ground_truth_pnl.md
  - docs/isolation_design.md
  - docs/isolation_migration_map.md
  - docs/venus_sensing_design.md
  - fix_linter.py
  - risk_state.db
  - trading.db
  - zeus.db
  - zeus_state.db
  - zeus_data_inventory.xlsx
  - tests/test_day0_exit_gate.py
files_may_not_change:
  - docs/governance/**
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/negative_constraints.yaml
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/zeus_FINAL_spec.md
  - src/**
  - scripts/**
  - migrations/**
  - .github/workflows/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required: []
parity_required: false
replay_required: false
rollback: Revert the archive sweep commit as one batch to restore the historical files to their previous scattered locations.
acceptance:
  - the remaining clearly historical top-level docs are moved under `docs/archives/**`
  - the retired root artifacts moved by this packet are no longer scattered in the repo root
  - only the minimal special root files remain visible as live authority/runtime entry points
  - any remaining references to moved files point to archive paths or are compatibility-only
evidence_required:
  - before/after top-level file inventory
  - targeted reference scan for moved files
  - work-packet grammar output
  - kernel-manifest check output
```

## Notes

- This is an intentionally aggressive archive sweep.
- When a file is clearly historical or an inert root artifact, prefer archival demotion over prolonged case-by-case hesitation.
