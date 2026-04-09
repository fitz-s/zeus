# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Remove the legacy timestamp shadow that still forces canonical portfolio truth to degrade to `stale_legacy_fallback` even when the active paper-mode projection is otherwise usable.

## Allowed files

- `work_packets/BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/db.py`
- `tests/test_truth_surface_health.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/state/portfolio.py`
- `src/state/decision_chain.py`
- `src/riskguard/**`
- `src/observability/status_summary.py`
- `src/control/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/execution/**`
- `src/engine/**`
- `tests/test_architecture_contracts.py`
- `tests/test_riskguard.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no `src/state/portfolio.py` DB-path cleanup yet
- no settlement-summary dedupe yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- fresh evidence shows `query_portfolio_loader_view()` returns `ok` on `zeus-paper.db` but `load_portfolio()` still falls back through unsuffixed `zeus.db`
- the active stale ids (`trade-1`, `rt1`, `75c98026-cd5`) are triggered by legacy timestamps newer than `position_current.updated_at`
- this packet must stay bounded to the comparator/shadow seam and expose the wider portfolio-truth drift without silently widening into other modules

## Immediate checklist

- [x] `BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW` frozen
- [ ] comparator/shadow root cause reproduced in packet-bounded tests
- [ ] `query_portfolio_loader_view()` no longer degrades on the identified stale ids
- [ ] targeted truth-surface tests pass
- [ ] wider portfolio fallback / settlement dedupe drift remains explicit

## Next required action

1. Implement the bounded loader comparator fix in `src/state/db.py`.
2. Lock the identified stale-id scenario in `tests/test_truth_surface_health.py`.
3. If the fix proves `src/state/portfolio.py` or settlement dedupe must change, stop and freeze the follow-up packet instead of widening silently.
