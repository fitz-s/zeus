# BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW

```yaml
work_packet_id: BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW
packet_type: repair_packet
objective: Remove the legacy timestamp shadow that still forces canonical portfolio truth to degrade to `stale_legacy_fallback` even when the paper-mode projection is otherwise usable.
why_this_now: The accepted trailing-loss packet fixed one shallow risk symptom and exposed the deeper seam beneath it. Fresh verification shows `query_portfolio_loader_view()` returns `ok` on `zeus-paper.db` but `load_portfolio()` still falls back because the unsuffixed `zeus.db` path reports `stale_legacy_fallback`. The active stale ids (`trade-1`, `rt1`, `75c98026-cd5`) are triggered by `position_events_legacy` timestamps newer than `position_current.updated_at`, so the next lawful step is to repair the comparator/shadow seam before touching wider portfolio or settlement authority.
why_not_other_approach:
  - Jump straight to `src/state/portfolio.py` DB-path cleanup | that is a follow-up shadow-cleanup slice; the current root trigger lives in the loader comparator path
  - Widen into settlement summary dedupe now | separate authority seam; do not merge two bottom-layer repairs into one packet
  - Leave `stale_legacy_fallback` as advisory | it still forces JSON fallback and keeps portfolio truth on a degraded path
truth_layer: canonical portfolio truth should not degrade merely because legacy timestamps shadow `position_current.updated_at` without proving that the projection is semantically behind for the active mode.
control_layer: keep the packet bounded to `src/state/db.py`, packet-bounded tests, and control surfaces. Do not widen into `src/state/portfolio.py`, `src/riskguard/**`, `src/observability/**`, or settlement dedupe logic in this packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted truth-surface tests, and a direct loader probe note showing the stale ids before repair and canonical `ok` behavior after repair.
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
  - src/state/AGENTS.md
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY.md
  - src/state/db.py
  - tests/test_truth_surface_health.py
files_may_change:
  - work_packets/BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/db.py
  - tests/test_truth_surface_health.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/portfolio.py
  - src/riskguard/**
  - src/observability/**
  - src/control/**
  - src/execution/**
  - src/engine/**
  - src/state/decision_chain.py
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_truth_surface_health.py::test_portfolio_loader_ignores_same_phase_legacy_entry_shadow tests/test_truth_surface_health.py::test_portfolio_loader_marks_semantic_exit_shadow_as_stale tests/test_truth_surface_health.py::test_portfolio_loader_keeps_older_semantic_advance_stale_even_if_newer_shadow_event_exists
parity_required: false
replay_required: false
rollback: Revert the comparator/shadow fix, paired tests, and control-surface updates together; repo returns to the accepted trailing-loss boundary with the legacy timestamp shadow still forcing fallback.
acceptance:
  - `query_portfolio_loader_view()` no longer returns `stale_legacy_fallback` for the identified paper-mode shadow ids when the projection is otherwise canonical for the active mode
  - targeted comparator/shadow tests pass and directly prove same-phase legacy shadows are ignored while true later semantic lag still forces degraded loader truth
  - the packet leaves `src/state/portfolio.py` DB-path cleanup and settlement-summary dedupe explicitly open as follow-up work
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted truth-surface pytest output
  - direct loader probe note
```

## Notes

- This packet is intentionally narrower than a full portfolio-truth unification package.
- The critic/reviewer lane must keep asking whether the fix removes false degradation without hiding any real semantic lag in the projection path.
- If implementation proves `load_portfolio()` itself must change, stop and freeze the follow-up packet rather than widening this one silently.

## Evidence log

- Pending implementation.
