# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING acceptance sync`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING`
- State: `ACCEPTED_LOCAL / POST_CLOSE_PENDING`
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

- packet-bounded loader evidence now passes, but post-close critic + verifier are still required before the next packet may freeze
- fresh probes now show DB-first paper loads align `recent_exits` with authoritative settlements (`19 / -13.03`) while JSON fallback still preserves JSON exits
- downstream consumer/output drift remains follow-up work and must be handled by a new packet instead of widening this accepted boundary

## Immediate checklist

- [x] `BUG-LOAD-PORTFOLIO-RECENT-EXITS-TRUTH-MIXING` frozen
- [x] mixed-source `PortfolioState` reproduced with packet-bounded evidence
- [x] DB-first loads stop importing contradictory JSON `recent_exits`
- [x] packet-bounded loader tests pass
- [x] wider downstream output drift remains explicit

## Next required action

1. Run post-close critic + verifier on the accepted loader boundary.
2. Freeze the next bounded portfolio-truth packet instead of widening this one.
