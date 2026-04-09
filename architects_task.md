# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW post-close sync`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW`
- State: `POST_CLOSE_PASSED / NEXT_FREEZE_ALLOWED`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Remove the legacy timestamp shadow that still forces canonical portfolio truth to degrade to `stale_legacy_fallback` even after the mode-aware DB probe and stage-event dedupe packets have cleared the earlier seams.

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
- no RiskGuard output-layer parity assertion yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- post-close review completed with no blocker-level contradictions on the accepted comparator/shadow boundary
- the next visible portfolio-truth seam is outside this packet: `load_portfolio()` still returns JSON `recent_exits` (`14 / +210.35`) while canonical paper settlements are `19 / -13.03`
- unsuffixed `zeus.db` still holds one true semantic stale projection (`08d6c939-038`), which remains follow-up work rather than a reason to reopen this accepted packet

## Immediate checklist

- [x] `BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW` frozen
- [x] comparator/shadow root cause reproduced in packet-bounded tests
- [x] same-phase legacy shadow degradation removed without hiding true later semantic lag
- [x] targeted truth-surface tests pass
- [x] wider fallback-reader / output-layer drift remains explicit

## Next required action

1. Freeze the next bounded portfolio-truth packet.
2. Keep `BUG-PORTFOLIO-LEGACY-TIMESTAMP-SHADOW` closed unless a new contradiction reopens it.
