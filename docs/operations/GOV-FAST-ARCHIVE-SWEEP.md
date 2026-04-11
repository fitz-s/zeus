# GOV-FAST-ARCHIVE-SWEEP

```yaml
work_packet_id: GOV-FAST-ARCHIVE-SWEEP
packet_type: governance_packet
objective: Rapidly archive the remaining clearly historical top-level docs and legacy root artifacts so the live repo surface is reduced to a small set of active authority and runtime-entry files.
why_this_now: After the root authority guide and control-surface consolidation, the user explicitly directed a much faster cleanup posture: archive almost everything that is not still needed, instead of preserving scattered historical files in the root or top-level docs surface. The user also explicitly directed that completed work packets should stop living in the active `work_packets/` surface and be archived as historical modifications.
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
  - docs/README.md
  - docs/operations/current_state.md
  - workspace_map.md
files_may_change:
  - docs/operations/GOV-FAST-ARCHIVE-SWEEP.md
  - AGENTS.md
  - architecture/self_check/authority_index.md
  - workspace_map.md
  - .github/workflows/architecture_advisory_gates.yml
  - docs/README.md
  - docs/operations/current_state.md
  - docs/reference/repo_overview.md
  - docs/operations/**
  - docs/reference/**
  - docs/operations/**
  - scripts/check_work_packets.py
  - scripts/check_advisory_gates.py
  - tests/test_architecture_contracts.py
  - docs/operations/*.md
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
  - docs/authority/**
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/negative_constraints.yaml
  - docs/authority/zeus_durable_architecture_spec.md
  - docs/authority/target_state_spec.md
  - src/**
  - migrations/**
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
  - completed work packets are archived out of the active `work_packets/` surface, leaving only the current live packet in `docs/work_packets/`
  - root markdown is reduced to the minimal special set needed for authority (`AGENTS.md`, `ZEUS_AUTHORITY.md`)
  - live control/orientation markdown is classified under `docs/control/`, `docs/reference/`, and `docs/work_packets/` instead of remaining in the repo root
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

## Evidence log

- Before the sweep, the live root surface still contained historical artifacts (`fix_linter.py`, `risk_state.db`, `trading.db`, `zeus.db`, `zeus_state.db`, `zeus_data_inventory.xlsx`) alongside the true live authority/runtime entry files.
- Before the sweep, top-level `docs/` still contained historical designs/reports (`ground_truth_pnl.md`, `isolation_design.md`, `isolation_migration_map.md`, `venus_sensing_design.md`) outside `docs/archives/**`.
- After the sweep, root markdown is reduced to the minimal special set: `AGENTS.md` and `ZEUS_AUTHORITY.md`; live control/orientation markdown moved under `docs/control/`, `docs/reference/`, and `docs/work_packets/`.
- After the sweep, top-level `docs/` is reduced to `docs/README.md`, `docs/known_gaps.md`, and `docs/zeus_FINAL_spec.md`.
- Completed work packets were archived under `docs/archives/work_packets/`, leaving only `docs/work_packets/GOV-FAST-ARCHIVE-SWEEP.md` live.
- Targeted reference scan after the moves found no remaining live-surface references to the moved top-level docs or root artifacts outside `docs/archives/**`.
- Packet/tooling compatibility was preserved by repointing `scripts/check_work_packets.py`, `scripts/check_advisory_gates.py`, `.github/workflows/architecture_advisory_gates.yml`, and `tests/test_architecture_contracts.py` to `docs/work_packets/**`.
