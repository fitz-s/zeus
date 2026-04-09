# REPAIR-REALIZED-TRUTH-CONVERGENCE

```yaml
work_packet_id: REPAIR-REALIZED-TRUTH-CONVERGENCE
packet_type: repair_packet
objective: Reopen and repair the realized-PnL truth seam so `outcome_fact`, deduped `chronicle`, `risk_state`, and `status_summary` converge on one current-mode realized PnL instead of diverging across fallback surfaces.
why_this_now: Fresh runtime evidence disproves the previously closed bankroll-truth boundary. In paper mode, `outcome_fact` and deduped `chronicle` both report `-13.03`, while `risk_state-paper.db` and `status_summary-paper.json` still report realized PnL near `+208.89`, with `portfolio_truth_source = working_state_fallback`. Under repo law, later truth contradiction reopens the earlier claim explicitly before further packet advancement.
why_not_other_approach:
  - Continue with BUG-CANONICAL-CLOSURE-TRACEABILITY first | would advance past a now-proven contradiction on the already-closed bankroll/truth seam
  - Patch only status_summary display | would leave the underlying risk/truth source divergence intact
  - Patch only riskguard metrics | would leave operator truth and evidence surfaces drifting from the canonical settlement facts
truth_layer: current-mode realized PnL must converge across canonical settlement facts (`outcome_fact` / deduped `chronicle`), RiskGuard state, and operator summary; fallback paths must be explicit and must not silently outrank canonical current-mode truth.
control_layer: limit the repair to `src/riskguard/riskguard.py`, `src/observability/status_summary.py`, packet/control surfaces, and targeted tests proving realized-truth convergence. Do not widen into control-plane durability, lifecycle closure/projection, migrations, or ETL contamination.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted convergence pytest output, direct current-mode SQL/JSON evidence for the four truth surfaces, and a short realized-truth contract note.
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
  - src/state/AGENTS.md
  - docs/archives/sessions/session_2026_04_07_final_state.md
  - src/riskguard/riskguard.py
  - src/observability/status_summary.py
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_cross_module_relationships.py
files_may_change:
  - work_packets/REPAIR-REALIZED-TRUTH-CONVERGENCE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/riskguard/riskguard.py
  - src/observability/status_summary.py
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
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
  - src/state/portfolio.py
  - src/state/db.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_riskguard.py::TestRiskGuardSettlementSource::test_tick_prefers_position_current_for_portfolio_truth tests/test_riskguard.py::TestRiskGuardSettlementSource::test_tick_records_explicit_portfolio_fallback_when_projection_unavailable
  - .venv/bin/pytest -q tests/test_pnl_flow_and_audit.py::test_inv_status_reports_real_pnl tests/test_pnl_flow_and_audit.py::test_inv_riskguard_reads_real_pnl tests/test_pnl_flow_and_audit.py::test_inv_riskguard_prefers_canonical_position_events_settlement_source tests/test_pnl_flow_and_audit.py::test_inv_riskguard_falls_back_to_legacy_settlement_source
  - .venv/bin/pytest -q tests/test_cross_module_relationships.py::test_riskguard_realized_pnl_matches_chronicle
parity_required: false
replay_required: false
rollback: Revert the realized-truth convergence edits and paired tests/control-surface updates together; repo returns to the reopened contradiction state with the divergence explicitly documented.
acceptance:
  - current-mode `risk_state` realized PnL converges with deduped `chronicle` and `outcome_fact`
  - `status_summary` realized PnL converges with the same current-mode truth instead of reflecting stale fallback state
  - fallback paths remain explicit and operator-visible when canonical/current-mode truth is unavailable
  - targeted tests pass and the control surfaces record the reopened repair boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted convergence pytest output
  - direct SQL/JSON truth comparison note
  - realized-truth contract note
```

## Notes

- This repair packet supersedes the previous claim that BUG-BANKROLL-TRUTH-CONSISTENCY fully closed the bankroll-truth boundary; the new runtime contradiction reopens the seam explicitly.
- Degraded-path rule for this packet: current-mode canonical truth beats fallback truth; fallback is allowed only when the current-mode substrate is unavailable and must be labeled as such.
- If implementation shows the fix requires `src/state/db.py` or lifecycle/projection changes, stop and freeze a new packet instead of widening this repair packet.


## Evidence log

- work-packet grammar output: `python3 scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `python3 scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted convergence pytest output: `.venv/bin/pytest -q tests/test_riskguard.py::TestRiskGuardSettlementSource::test_tick_prefers_position_current_for_portfolio_truth tests/test_riskguard.py::TestRiskGuardSettlementSource::test_tick_records_explicit_portfolio_fallback_when_projection_unavailable` -> `2 passed`; `.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py::test_inv_status_reports_real_pnl tests/test_pnl_flow_and_audit.py::test_inv_riskguard_reads_real_pnl tests/test_pnl_flow_and_audit.py::test_inv_status_summary_converges_to_current_mode_realized_truth tests/test_pnl_flow_and_audit.py::test_inv_riskguard_prefers_canonical_position_events_settlement_source tests/test_pnl_flow_and_audit.py::test_inv_riskguard_falls_back_to_legacy_settlement_source` -> `5 passed`; `.venv/bin/pytest -q tests/test_cross_module_relationships.py::test_riskguard_realized_pnl_matches_chronicle` -> `1 passed`
- direct SQL/JSON truth comparison note: after running `riskguard.tick()` and `status_summary.write_status()` in paper mode, `outcome_fact_total = -13.03`, deduped `chronicle_settlement = -13.03`, `risk_state-paper.db realized_pnl = -13.03`, and `status_summary-paper.json realized_pnl = -13.03`
- realized-truth contract note: `src/riskguard/riskguard.py` now opens the current-mode trade DB instead of the legacy monolith for runtime truth, and it derives realized PnL from `outcome_fact` first, then deduped `chronicle`, before falling back to broader settlement rows; `src/observability/status_summary.py` then reads the corrected current-mode `risk_state` output.
- independent pre-close critic artifact: `.omx/artifacts/gemini-repair-realized-truth-preclose-critic-20260408T035939Z.md` -> `APPROVE / no blockers`
- independent pre-close verifier artifact: `.omx/artifacts/claude-repair-realized-truth-preclose-verifier-20260408T035939Z.md` -> `SUFFICIENT`
- third-party post-close critic artifact: `.omx/artifacts/gemini-repair-realized-truth-postclose-critic-20260408T040100Z.md` -> `PASS`
- third-party post-close verifier artifact: `.omx/artifacts/claude-repair-realized-truth-postclose-verifier-20260408T040100Z.md` -> `PASS`
