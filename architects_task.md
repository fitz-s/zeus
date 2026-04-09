# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-LEGACY-SETTLEMENT-FALLBACK-DEDUPE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-LEGACY-SETTLEMENT-FALLBACK-DEDUPE`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Deduplicate legacy settlement fallback rows before they feed learning/risk summaries, so settlement sample counts and strategy settlement summaries stop disagreeing with headline realized PnL.

## Allowed files

- `work_packets/BUG-LEGACY-SETTLEMENT-FALLBACK-DEDUPE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/decision_chain.py`
- `tests/test_db.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/state/db.py`
- `src/state/portfolio.py`
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
- no RiskGuard output-layer parity assertion yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- fresh evidence shows headline realized PnL comes from `outcome_fact`, while settlement summaries still flatten duplicate decision-log settlement artifacts
- direct repro confirmed duplicate fallback artifacts can double summary totals
- this packet must stay bounded to the fallback reader seam and expose the wider comparator/shadow drift without silently widening into other modules

## Immediate checklist

- [x] `BUG-LEGACY-SETTLEMENT-FALLBACK-DEDUPE` frozen
- [ ] duplicate legacy settlement artifacts reproduced in packet-bounded tests
- [ ] fallback reader dedupes duplicate artifacts with deterministic latest-wins behavior
- [ ] targeted settlement-fallback tests pass
- [ ] wider comparator/shadow and output-layer drift remain explicit

## Next required action

1. Implement the bounded fallback-reader dedupe in `src/state/decision_chain.py`.
2. Lock the duplicate-artifact repro and latest-wins behavior in `tests/test_db.py`.
3. If implementation proves the RiskGuard output layer also needs a parity assertion, stop and freeze that follow-up packet instead of widening silently.
