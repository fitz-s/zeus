# GOV-TOP-LAW-EXPANSION

```yaml
work_packet_id: GOV-TOP-LAW-EXPANSION
packet_type: governance_packet
objective: Expand `ZEUS_AUTHORITY.md` from a thin root guide into a true top-law file whose scope and style match the repo's prior highest-order authority surfaces while still deferring exact precedence and enforcement to the existing machine-checkable and governance files.
why_this_now: Fresh comparison shows the current `ZEUS_AUTHORITY.md` is only 88 lines, while the repo's prior top-law surfaces (`docs/zeus_FINAL_spec.md`, `docs/architecture/zeus_durable_architecture_spec.md`) operate at a much larger methodological and constitutional scale. The user explicitly rejected the thin-guide shape and directed that the highest file should look more like the original top files.
why_not_other_approach:
  - Keep `ZEUS_AUTHORITY.md` as a short guide | too thin to function as a true top-law surface
  - Copy the FINAL spec into the root file verbatim | would duplicate too much phase-specific detail and create another drift-heavy giant mirror
  - Push all top-law content back into AGENTS.md | mixes operational procedure with the repo's highest-order law
truth_layer: `ZEUS_AUTHORITY.md` should become the root constitutional statement of Zeus: foundations, method, system law, invariants, negative constraints, boundary law, and operator/change doctrine; exact machine-checkable enforcement remains in the existing manifests and governance docs.
control_layer: keep this packet bounded to the top-law expansion plus the minimal routing/index edits needed to reflect its upgraded role. Do not widen into archive cleanup, runtime code, or constitution rewrites.
evidence_layer: line-count/style comparison against prior top-law files, source-backed section mapping, packet grammar output, kernel-manifest check output, and targeted architecture/governance test evidence.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - AGENTS.md
  - ZEUS_AUTHORITY.md
  - architecture/self_check/authority_index.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/zeus_FINAL_spec.md
  - docs/governance/zeus_change_control_constitution.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/negative_constraints.yaml
  - docs/control/current_state.md
  - docs/work_packets/GOV-TOP-LAW-EXPANSION.md
files_may_change:
  - docs/work_packets/GOV-TOP-LAW-EXPANSION.md
  - ZEUS_AUTHORITY.md
  - AGENTS.md
  - architecture/self_check/authority_index.md
  - docs/README.md
  - docs/reference/repo_overview.md
  - docs/reference/workspace_map.md
files_may_not_change:
  - docs/governance/**
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/negative_constraints.yaml
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/zeus_FINAL_spec.md
  - src/**
  - tests/**
  - scripts/**
  - migrations/**
  - .github/workflows/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - /Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest -q tests/test_architecture_contracts.py -k test_advisory_gate_workflow_freezes_verdict
parity_required: false
replay_required: false
rollback: Revert the top-law expansion as one batch; repo falls back to the thin root guide plus the existing deeper authority stack.
acceptance:
  - `ZEUS_AUTHORITY.md` is materially expanded toward the scale and style of the repo's prior top-law files
  - the file contains foundations, source basis, architectural intent, non-goals, invariants, negative constraints, boundary law, archive/control doctrine, and operator/change method
  - the file still defers exact precedence and enforcement to `authority_index.md`, manifests, and governance docs rather than replacing them
  - top routing/orientation files reflect the upgraded role without creating a conflicting second constitution
evidence_required:
  - line-count and section comparison against prior top-law files
  - source-backed section mapping
  - work-packet grammar output
  - kernel-manifest check output
```

## Notes

- This packet supersedes the earlier assumption that `ZEUS_AUTHORITY.md` could stay as a thin root guide.
- The goal is not brevity. The goal is a root law surface with real bearing capacity.
