# BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP

```yaml
work_packet_id: BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP
packet_type: repair_packet
objective: Isolate and reroute the live paper-mode background writer that keeps overwriting refreshed artifacts with stale fallback-based snapshots.
why_this_now: The accepted refresh packet now proves the clean-branch refresh entrypoint can write coherent paper artifacts into the live state directory. Immediately after refresh, `risk_state-paper.db` showed `portfolio_truth_source=position_current`, `portfolio_loader_status=ok`, `settlement_sample_size=19`, and `daily_loss=0.0`. Within minutes, the live artifacts were overwritten back to `working_state_fallback`, `stale_legacy_fallback`, `settlement_sample_size=22`, and `daily_loss=13.26`. Fresh launchd inspection shows the active paper writers are `com.zeus.paper-trading` and `com.zeus.riskguard`, both bound to `/Users/leofitz/.openclaw/workspace-venus/zeus`, i.e. the stale checkout rather than the clean integrated branch.
why_not_other_approach:
  - Keep re-running the refresh entrypoint | that only masks the overwrite race and does not restore ownership discipline
  - Keep changing core truth readers | the clean-branch readers already produce coherent results
  - Immediately redesign the whole runtime service layer | too wide; first isolate and reroute the concrete stale writer
truth_layer: persisted paper artifacts cannot converge while a stale background writer from the wrong checkout continues to rewrite them.
control_layer: keep this packet bounded to writer ownership/rerouting. Do not widen into new truth math or broader runtime redesign in this packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, launchd ownership evidence, and direct before/after overwrite probes.
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
  - work_packets/REFRESH-PAPER-RUNTIME-ARTIFACTS.md
files_may_change:
  - work_packets/BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - /Users/leofitz/Library/LaunchAgents/com.zeus.paper-trading.plist
  - /Users/leofitz/Library/LaunchAgents/com.zeus.riskguard.plist
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
rollback: Revert the launchd ownership/routing changes and control-surface updates together; the stale writer remains the active owner.
acceptance:
  - the stale paper writer/owner path is explicitly rerouted, disabled, or superseded by a narrower external-runtime packet
  - the packet does not silently widen into core truth math or full service redesign
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - launchd ownership evidence
  - direct overwrite probe note
```

## Evidence log

- 2026-04-09: immediately after `scripts/refresh_paper_runtime_artifacts.py --state-dir /Users/leofitz/.openclaw/workspace-venus/zeus/state`, paper artifacts were coherent (`position_current`, `ok`, `19`, `0.0`).
- 2026-04-09: within minutes, fresh rows reverted to `working_state_fallback`, `stale_legacy_fallback`, `22`, `13.26`.
- 2026-04-09: `launchctl print` showed active live paper writers:
  - `com.zeus.paper-trading`
  - `com.zeus.riskguard`
  both bound to `/Users/leofitz/.openclaw/workspace-venus/zeus`.
