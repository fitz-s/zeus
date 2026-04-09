# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-LOAD-PORTFOLIO-MODED-DB-PROBE refreshed acceptance`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-LOAD-PORTFOLIO-MODED-DB-PROBE`
- State: `ACCEPTED_LOCAL / POST_CLOSE_PENDING`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Make `load_portfolio()` probe the mode-correct trade DB instead of unsuffixed `zeus.db`, so paper-mode canonical loader truth is not shadowed by unrelated stale rows in the mixed legacy file.

## Allowed files

- `work_packets/BUG-LOAD-PORTFOLIO-MODED-DB-PROBE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/portfolio.py`
- `tests/test_runtime_guards.py`
- `tests/test_db.py`

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

- no `src/state/db.py` comparator/shadow cleanup yet
- no settlement-summary dedupe yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- accepted implementation now prefers the sibling mode DB and removes the immediate paper wrong-path fallback
- the packet still requires the mandatory post-close critic + verifier before the next freeze
- deeper `src/state/db.py` comparator/shadow drift and settlement-authority drift remain explicit follow-up work and must not be silently folded into this packet

## Immediate checklist

- [x] `BUG-LOAD-PORTFOLIO-MODED-DB-PROBE` frozen
- [x] mode-aware DB probe root cause reproduced in packet-bounded tests
- [x] `load_portfolio()` no longer falls back when the mode DB is healthy and unsuffixed `zeus.db` is stale
- [x] targeted load-portfolio tests pass
- [x] wider comparator/shadow / settlement dedupe drift remains explicit
- [ ] post-close critic review passed
- [ ] post-close verifier review passed

## Next required action

1. Run the post-close critic review on the accepted packet boundary.
2. Run the post-close verifier review on the accepted packet boundary.
3. Freeze the next portfolio-truth / settlement-authority packet only after post-close passes.
