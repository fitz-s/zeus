# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Deduplicate legacy `POSITION_SETTLED` stage events before they feed authoritative settlement queries, so settlement sample counts and strategy settlement summaries stop disagreeing with headline realized PnL.

## Allowed files

- `work_packets/BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/db.py`
- `tests/test_db.py`

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
- `tests/test_truth_surface_health.py`
- `tests/test_riskguard.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no `src/state/decision_chain.py` fallback-reader cleanup yet
- no RiskGuard output-layer parity assertion yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- fresh evidence shows headline realized PnL comes from `outcome_fact`, while authoritative settlement queries still prefer duplicated legacy `POSITION_SETTLED` stage events
- direct repro confirmed duplicate stage events for `0c108102-032`, `6f8ce461-902`, and `9e97c78f-2a8`
- this packet must stay bounded to the stage-event query seam and expose the wider comparator/shadow drift without silently widening into other modules

## Immediate checklist

- [x] `BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE` frozen
- [ ] duplicate legacy stage events reproduced in packet-bounded tests
- [ ] stage-event query dedupes duplicates with deterministic latest-wins behavior
- [ ] targeted settlement-query tests pass
- [ ] wider comparator/shadow and output-layer drift remain explicit

## Next required action

1. Implement the bounded stage-event dedupe in `src/state/db.py`.
2. Lock the duplicate-stage-event repro and latest-wins behavior in `tests/test_db.py`.
3. If implementation proves the RiskGuard output layer also needs a parity assertion, stop and freeze that follow-up packet instead of widening silently.
