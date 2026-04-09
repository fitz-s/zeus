# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-LOAD-PORTFOLIO-MODED-DB-PROBE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-LOAD-PORTFOLIO-MODED-DB-PROBE`
- State: `FROZEN / IMPLEMENTATION_READY`
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

- fresh evidence shows `query_portfolio_loader_view()` returns `ok` on `zeus-paper.db` while unsuffixed `zeus.db` still returns `stale_legacy_fallback`
- `load_portfolio()` still probes unsuffixed `zeus.db`, so paper mode degrades even when the mode-specific paper DB projection is healthy
- this packet must stay bounded to the mode-aware probe seam and expose the wider comparator/shadow drift without silently widening into other modules

## Immediate checklist

- [x] `BUG-LOAD-PORTFOLIO-MODED-DB-PROBE` frozen
- [ ] mode-aware DB probe root cause reproduced in packet-bounded tests
- [ ] `load_portfolio()` no longer falls back when the mode DB is healthy and unsuffixed `zeus.db` is stale
- [ ] targeted load-portfolio tests pass
- [ ] wider comparator/shadow / settlement dedupe drift remains explicit

## Next required action

1. Implement the bounded mode-aware DB probe fix in `src/state/portfolio.py`.
2. Lock the healthy-paper-db / stale-unsuffixed-db scenario in `tests/test_runtime_guards.py` or `tests/test_db.py`.
3. If the fix proves `src/state/db.py` comparator logic or settlement dedupe must change, stop and freeze the follow-up packet instead of widening silently.
