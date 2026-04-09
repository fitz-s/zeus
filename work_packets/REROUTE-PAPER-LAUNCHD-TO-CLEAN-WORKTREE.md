# REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE

```yaml
work_packet_id: REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE
packet_type: repair_packet
objective: Restore paper runtime on a clean code checkout while keeping it attached to the live paper state directory.
why_this_now: The ownership packet disabled the stale paper launchd writers and stopped the overwrite race, but paper runtime is now offline. The next bounded step is to reroute the paper launchd jobs onto the clean integrated branch without reintroducing the stale checkout as an active writer.
why_not_other_approach:
  - Re-enable the old launchd jobs on the stale checkout | would immediately restore the stale overwrite path
  - Merge the clean branch back into the stale checkout first | the user explicitly said that merge path is not currently workable
  - Leave paper services disabled indefinitely | protects truth temporarily but does not restore paper runtime
truth_layer: paper runtime must execute the clean integrated code while still reading/writing the live paper state directory.
control_layer: keep this packet bounded to external runtime routing: clean worktree placement, state-dir linkage, and paper launchd plist updates. Do not widen into core truth logic or broad runtime redesign.
evidence_layer: work-packet grammar output, kernel-manifest check output, launchd routing evidence, and direct paper artifact probes after re-enable.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-07
  - INV-08
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP.md
files_may_change:
  - work_packets/REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - /Users/leofitz/Library/LaunchAgents/com.zeus.paper-trading.plist
  - /Users/leofitz/Library/LaunchAgents/com.zeus.riskguard.plist
  - /Users/leofitz/.openclaw/workspace-venus/zeus-paper-runtime-clean/**
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - src/**
  - scripts/**
  - tests/**
  - migrations/**
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required: []
parity_required: false
replay_required: false
rollback: Restore the backed-up launchd plists and disable/bootout the rerouted paper services; repo returns to the ownership-packet boundary with paper runtime disabled.
acceptance:
  - paper launchd services run from the clean worktree rather than the stale checkout
  - live paper artifact writes remain coherent after re-enable
  - the packet does not widen into core truth-math changes
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - launchd routing evidence
  - direct paper artifact probe note
```

## Evidence log

- 2026-04-09: stale paper launchd writers were disabled and coherent artifacts remained stable.
- 2026-04-09: next bounded need is to restore paper runtime from clean code without reactivating the stale checkout.
