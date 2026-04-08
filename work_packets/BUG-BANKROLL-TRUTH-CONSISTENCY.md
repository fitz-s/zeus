# BUG-BANKROLL-TRUTH-CONSISTENCY

```yaml
work_packet_id: BUG-BANKROLL-TRUTH-CONSISTENCY
packet_type: bugfix_packet
objective: Eliminate bankroll-truth loss between entry sizing, RiskGuard, and status summary so paper/live capital semantics come from one explicit contract instead of three drifting local interpretations.
why_this_now: BUG-MONITOR-SHARED-CONNECTION-REPAIR is closed. Fresh repo truth still shows the next highest-leverage cross-module loss on capital semantics: `cycle_runtime.entry_bankroll_for_cycle()` uses `portfolio.effective_bankroll`, `riskguard._load_riskguard_portfolio_truth()` rebuilds a new portfolio with reset baselines/recent exits, and `status_summary.write_status()` can fabricate `effective_bankroll` from `total_pnl` while dropping regime scoping. This keeps paper/live simulation and operator truth from being end-to-end trustworthy even when the monitoring seam is fixed.
why_not_other_approach:
  - Chase each bankroll anomaly separately in entry, RiskGuard, and status summary | would preserve the same semantic drift across modules under different local patches
  - Jump to lifecycle/projection or external ETL contamination first | those remain important, but capital-truth drift still corrupts trade sizing, risk interpretation, and operator reporting at every cycle
  - Widen into control-plane durability now | that is a different governance seam and would hide whether bankroll truth itself is coherent
truth_layer: paper/live entry sizing, RiskGuard accounting, and operator summary must agree on what effective bankroll means, when wallet balance matters, when config caps apply, and how missing substrates degrade without inventing bankroll truth from unrelated fields.
control_layer: limit the change to `src/engine/cycle_runtime.py`, `src/riskguard/riskguard.py`, `src/observability/status_summary.py`, and packet-bounded tests proving consistent bankroll semantics across those seams. Do not widen into migration scripts, control-plane durability, lifecycle phase rewrites, or ETL contamination work.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted bankroll/risk/status pytest output, and a short bankroll-contract note that explains paper/live present-path and degraded-path semantics.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-05
  - INV-09
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
  - src/engine/AGENTS.md
  - src/state/AGENTS.md
  - docs/session_2026_04_07_final_state.md
  - src/engine/cycle_runtime.py
  - src/riskguard/riskguard.py
  - src/observability/status_summary.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_riskguard.py
  - tests/test_cross_module_relationships.py
files_may_change:
  - work_packets/BUG-BANKROLL-TRUTH-CONSISTENCY.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/engine/cycle_runtime.py
  - src/riskguard/riskguard.py
  - src/observability/status_summary.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_riskguard.py
  - tests/test_cross_module_relationships.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/control/**
  - src/execution/**
  - src/supervisor_api/**
  - src/state/ledger.py
  - src/state/projection.py
  - src/state/lifecycle_manager.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_pnl_flow_and_audit.py::test_inv_status_reports_real_pnl tests/test_pnl_flow_and_audit.py::test_inv_status_passes_current_regime_start_to_learning_surface tests/test_pnl_flow_and_audit.py::test_inv_kelly_uses_effective_bankroll tests/test_pnl_flow_and_audit.py::test_inv_riskguard_reads_real_pnl
  - .venv/bin/pytest -q tests/test_riskguard.py::TestRiskGuardSettlementSource::test_tick_prefers_position_current_for_portfolio_truth tests/test_riskguard.py::TestRiskGuardSettlementSource::test_tick_records_explicit_portfolio_fallback_when_projection_unavailable
  - .venv/bin/pytest -q tests/test_cross_module_relationships.py -k "riskguard_realized_pnl_matches_chronicle or position_current"
parity_required: false
replay_required: false
rollback: Revert the bankroll-semantics edits and paired tests/control-surface updates together; repo returns to the current closed monitor-seam boundary with the bankroll/risk/operator truth drift still explicitly open.
acceptance:
  - paper/live entry sizing exposes one explicit bankroll contract instead of silent local interpretation
  - RiskGuard DB-first portfolio truth no longer silently resets bankroll/baseline semantics in a way that changes risk meaning
  - status summary no longer fabricates `effective_bankroll` from `total_pnl`, and it passes current-regime scoping through to execution/learning summaries when the source exists
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted bankroll/risk/status pytest output
  - bankroll-contract note
```

## Notes

- This packet is the next K-level seam after the monitoring shared-connection fix: one bankroll truth across entry, risk, and operator views.
- Degraded-path rule for this packet: if the upstream bankroll source is unavailable, the touched code must surface the degradation explicitly rather than fabricate bankroll truth from `total_pnl` or reset baselines silently.
- `src/state/portfolio.py` remains out of scope; if the fix needs `PortfolioState` semantics adjusted, do it only at the touched `riskguard.py` / `cycle_runtime.py` / `status_summary.py` call sites, not by editing `portfolio.py` itself.
- If implementation shows the fix requires lifecycle/projection rewrites, control-plane migration, or ETL/recalibration changes, stop and freeze a new packet instead of widening this one.
- Freeze review artifacts: `.omx/artifacts/gemini-bug-bankroll-truth-freeze-critic-20260408T014904Z.md` -> `APPROVE`; `.omx/artifacts/claude-bug-bankroll-truth-freeze-verifier-20260408T014904Z.md` -> `READY`.
- Follow-on K-level candidates after this packet likely include: control-plane durable truth completeness, canonical lifecycle closure/projection traceability, and external ETL/recalibration contamination proof.
