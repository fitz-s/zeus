# BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING

```yaml
work_packet_id: BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING
packet_type: repair_packet
objective: Stop `load_portfolio()` from mixing canonical DB-first positions with stale JSON `recent_exits` when the portfolio projection is otherwise healthy.
why_this_now: The accepted comparator/shadow packet removed a false degradation seam and exposed the next deeper contradiction. Fresh verification on the integrated branch now shows `load_portfolio(state/positions-paper.json)` returns canonical positions (`12`) from `zeus-paper.db` while still carrying JSON `recent_exits` (`14 / +210.35`), even though authoritative paper settlements are `19 / -13.03`. That means a single `PortfolioState` object still contains conflicting truths across modules.
why_not_other_approach:
  - Reopen the comparator packet | the contradiction now lives in `src/state/portfolio.py`, outside the accepted comparator boundary
  - Widen into RiskGuard or status-summary output now | those are downstream consumers; first stop the mixed source at the loader boundary
  - Keep JSON `recent_exits` as compatibility metadata during DB-first loads | that preserves a visibly contradictory truth object and keeps downstream cross-module drift alive
truth_layer: a DB-first `PortfolioState` must not silently combine canonical positions with stale JSON realized-exit history.
control_layer: keep the packet bounded to `src/state/portfolio.py`, packet-bounded tests, and control surfaces. Do not widen into `src/riskguard/**`, `src/observability/**`, or settlement aggregation code in this packet.
evidence_layer: work-packet grammar output, kernel-manifest check output, packet-bounded pytest output, and a direct probe note showing DB-first positions alongside contradictory JSON `recent_exits`.
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
  - work_packets/BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW.md
  - src/state/portfolio.py
  - tests/test_runtime_guards.py
files_may_change:
  - work_packets/BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/portfolio.py
  - tests/test_runtime_guards.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/db.py
  - src/state/decision_chain.py
  - src/riskguard/**
  - src/observability/**
  - src/control/**
  - src/execution/**
  - src/engine/**
  - tests/test_riskguard.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_truth_surface_health.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - /Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_runtime_guards.py -k 'load_portfolio and recent_exits'
parity_required: false
replay_required: false
rollback: Revert the loader recent-exit truth change, paired tests, and control-surface updates together; repo returns to the accepted comparator boundary with mixed-source `PortfolioState` loading.
acceptance:
  - when `load_portfolio()` uses canonical projection rows, it no longer blindly imports contradictory JSON `recent_exits`
  - packet-bounded tests prove DB-first loads and JSON-fallback loads still behave correctly under the new recent-exit rule
  - the packet leaves downstream output parity and broader settlement-consumer cleanup explicitly open if they remain necessary
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - packet-bounded pytest output
  - direct loader vs authoritative-settlement probe note
```

## Notes

- This packet is intentionally narrower than a full realized-PnL or status-summary unification package.
- The critic/reviewer lane must keep asking whether the fix actually stops mixed-source `PortfolioState` objects, rather than merely renaming the contradiction.
- If implementation proves `src/state/db.py` or `src/riskguard/**` must change immediately, stop and freeze that follow-up packet rather than widening silently.

## Evidence log

- 2026-04-09: follow-up probe after comparator closeout found `load_portfolio(state/positions-paper.json)` returning `positions=12`, `recent_exits=14`, `recent_exit_pnl=210.35` while authoritative paper settlements were `19 / -13.03`.
- 2026-04-09: `python3 scripts/check_work_packets.py` -> `work packet grammar ok`
- 2026-04-09: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- 2026-04-09: `python3 -m py_compile src/state/portfolio.py tests/test_runtime_guards.py` -> success
- 2026-04-09: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_runtime_guards.py -k 'load_portfolio and recent_exits'` -> `3 passed, 82 deselected`
- 2026-04-09: `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/pytest -q tests/test_runtime_guards.py -k 'load_portfolio'` -> `8 passed, 77 deselected`
- 2026-04-09: direct live-state probe after the fix showed `positions=12`, `recent_exits=19`, `recent_exit_pnl=-13.03`, matching authoritative paper settlements.
- 2026-04-09: JSON-fallback probe preserved `recent_exits=1`, `recent_exit_pnl=1.25`, `first_exit_reason=JSON_FALLBACK`.
