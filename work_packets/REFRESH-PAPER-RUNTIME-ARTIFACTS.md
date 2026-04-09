# REFRESH-PAPER-RUNTIME-ARTIFACTS

```yaml
work_packet_id: REFRESH-PAPER-RUNTIME-ARTIFACTS
packet_type: repair_packet
objective: Add a bounded, reproducible refresh path for paper-mode runtime artifacts so `risk_state-paper.db` and `status_summary-paper.json` can be regenerated from current clean-branch truth instead of preserving stale snapshots.
why_this_now: Fresh clean-branch probes now show the core runtime truth seams are coherent: `load_portfolio(state/positions-paper.json)` returns `positions=12`, `recent_exits=19`, `recent_exit_pnl=-13.03`; `_load_riskguard_portfolio_truth()` returns `fallback_active=false`; and `_trailing_loss_reference()` no longer accepts the row `13.73h` older than the daily cutoff. Yet the persisted paper artifacts still say the old story: `risk_state-paper.db` details still show `portfolio_truth_source=working_state_fallback`, `settlement_sample_size=22`, `daily_loss=13.26`, and `status_summary-paper.json` still reflects that old risk snapshot. The next seam is therefore artifact refresh, not another core truth computation change.
why_not_other_approach:
  - Keep changing core truth readers | current clean-branch probes already show coherent inputs; the contradiction is the stale persisted artifact layer
  - Refresh artifacts manually ad hoc every time | not reproducible and easy to lose or mis-sequence
  - Fold output parity or consumer rewrites into this packet | too wide; first establish a bounded refresh path for the persisted paper artifacts
truth_layer: persisted paper runtime artifacts must be refreshable from current coherent runtime truth without relying on stale prior snapshots.
control_layer: keep this packet bounded to a refresh entrypoint plus packet-bounded tests and control surfaces. Do not widen into new truth math, status-summary schema redesign, or general runtime orchestration in this packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, packet-bounded pytest output, and a direct before/after artifact probe note.
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
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/BUG-TRAILING-LOSS-REFERENCE-FRESHNESS-WINDOW.md
  - src/riskguard/riskguard.py
  - src/observability/status_summary.py
files_may_change:
  - work_packets/REFRESH-PAPER-RUNTIME-ARTIFACTS.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - scripts/refresh_paper_runtime_artifacts.py
  - tests/test_runtime_artifact_refresh.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/**
  - src/riskguard/**
  - src/observability/**
  - src/control/**
  - src/execution/**
  - src/engine/**
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_runtime_guards.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - /Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_runtime_artifact_refresh.py
parity_required: false
replay_required: false
rollback: Revert the refresh entrypoint, paired tests, and control-surface updates together; persisted paper artifacts remain stale until refreshed by other means.
acceptance:
  - a bounded refresh entrypoint can regenerate paper runtime artifacts from current clean-branch truth in the correct order
  - packet-bounded tests prove the refresh path invokes the intended paper-mode refresh steps without widening into unrelated runtime orchestration
  - the packet leaves broader consumer/output parity work explicit if refreshed artifacts still expose downstream mismatches
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - packet-bounded pytest output
  - direct stale-artifact probe note
```

## Notes

- This packet is about reproducible paper artifact refresh, not about changing the underlying truth math again.
- The critic/reviewer lane must keep asking whether the packet is truly bounded to refresh orchestration rather than drifting into broader runtime redesign.
- If implementation proves a core reader/writer still must change, stop and freeze that deeper packet separately.

## Evidence log

- 2026-04-09: persisted paper artifacts still showed stale values after the clean-branch truth repairs:
  - `risk_state-paper.db` details -> `portfolio_truth_source=working_state_fallback`, `settlement_sample_size=22`, `daily_loss=13.26`
  - `status_summary-paper.json` timestamp advanced, but still reflected the old persisted risk snapshot
  - direct clean-branch truth probes were already coherent (`positions=12`, `recent_exits=19`, `recent_exit_pnl=-13.03`)
