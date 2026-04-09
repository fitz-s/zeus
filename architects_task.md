# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Stop `load_portfolio()` from mixing canonical DB-first positions with stale JSON `recent_exits` once the portfolio projection is otherwise healthy.

## Allowed files

- `work_packets/BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/portfolio.py`
- `tests/test_runtime_guards.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/state/db.py`
- `src/state/decision_chain.py`
- `src/riskguard/**`
- `src/observability/status_summary.py`
- `src/control/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/execution/**`
- `src/engine/**`
- `tests/test_architecture_contracts.py`
- `tests/test_truth_surface_health.py`
- `tests/test_riskguard.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no RiskGuard output-layer parity assertion yet
- no `src/state/db.py` settlement-authority work in this packet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- `load_portfolio()` still returns canonical paper positions from DB while carrying contradictory JSON `recent_exits`
- fresh probe shows `recent_exits=14 / +210.35` while authoritative paper settlements are `19 / -13.03`
- this packet must stay bounded to the loader boundary and expose broader downstream output drift without silently widening into RiskGuard or DB settlement code

## Immediate checklist

- [x] `BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING` frozen
- [ ] mixed-source `PortfolioState` reproduced with packet-bounded evidence
- [ ] DB-first loads stop importing contradictory JSON `recent_exits`
- [ ] packet-bounded loader tests pass
- [ ] wider downstream output drift remains explicit

## Next required action

1. Implement the bounded loader recent-exit truth fix in `src/state/portfolio.py`.
2. Lock the mixed-source scenario in `tests/test_runtime_guards.py`.
3. If implementation proves a consumer outside `src/state/portfolio.py` must change, stop and freeze that follow-up packet instead of widening silently.
