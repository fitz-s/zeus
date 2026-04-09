# GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE

```yaml
work_packet_id: GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE
packet_type: governance_packet
objective: Amend the top authority and orientation surfaces so they reflect the current truth-mainline, current active control surfaces, and the new archive boundary.
why_this_now: Two archive passes have moved a large set of historical notes, reports, audits, traces, and handoffs out of the active root/docs surfaces. Fresh rechecks now show that the highest guidance files still point at stale or ambiguous active surfaces: `architecture/self_check/authority_index.md` still names `.claude/CLAUDE.md` as the operator brief and points at historical paths like `docs/progress/zeus_progress.md`; `WORKSPACE_MAP.md` still references obsolete root/docs locations and pre-isolation state files; `docs/README.md` is only partially archive-aware; and the relationship between `docs/zeus_FINAL_spec.md`, `docs/architecture/zeus_durable_architecture_spec.md`, `root_progress.md`/`root_task.md`, and `architects_*` control surfaces is not yet explicitly harmonized.
why_not_other_approach:
  - Continue archiving miscellaneous files first | top authority surfaces still misroute readers to stale active paths, so more file movement would deepen ambiguity
  - Rewrite governance constitutions broadly | too wide; first align the highest routing and orientation surfaces with current repo truth
  - Leave `WORKSPACE_MAP.md` and authority routing as-is | they still point agents/readers at stale control and documentation surfaces
truth_layer: the repo must have one explicit current authority stack, one explicit active-control entry surface, and one explicit archive boundary after the truth-mainline/archive cleanup.
control_layer: keep this packet bounded to top authority/orientation surfaces and active-control role clarification. Do not widen into runtime code, migrations, launchd/service ownership, or broad governance constitution rewrites unless later evidence forces a superseding packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, an authority mismatch matrix, and a reference scan proving the amended routing surfaces no longer point at stale active paths.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-03
  - INV-07
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_change_control_constitution.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/zeus_FINAL_spec.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - AGENTS.md
  - docs/README.md
  - WORKSPACE_MAP.md
  - root_progress.md
  - root_task.md
  - docs/known_gaps.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
files_may_change:
  - work_packets/GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - AGENTS.md
  - architecture/self_check/authority_index.md
  - docs/README.md
  - WORKSPACE_MAP.md
  - root_progress.md
  - root_task.md
  - docs/known_gaps.md
  - docs/zeus_FINAL_spec.md
  - docs/architecture/zeus_durable_architecture_spec.md
files_may_not_change:
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - migrations/**
  - src/**
  - tests/**
  - scripts/**
  - .github/workflows/**
  - .claude/CLAUDE.md
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required: []
parity_required: false
replay_required: false
rollback: Revert the amended authority/orientation/control-surface files together; repo returns to the pre-amendment routing ambiguity.
acceptance:
  - the top routing files explicitly distinguish active authority, active control surfaces, and archived historical surfaces
  - `WORKSPACE_MAP.md` and `docs/README.md` no longer direct readers to stale active paths
  - the amended stack resolves the current ambiguity between `docs/zeus_FINAL_spec.md` and `docs/architecture/zeus_durable_architecture_spec.md`
  - root-vs-architects control surfaces are described explicitly enough that later archive work can proceed without authority confusion
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - authority mismatch matrix
  - routing/reference scan note
```

## Notes

- This is the “highest authority amendment” packet requested after the archive cleanup passes.
- It should prefer explicit role clarification over prose expansion.
- If implementation proves the constitutions themselves must change, stop and freeze a superseding packet rather than widening silently.
