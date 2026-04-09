# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-08 America/Chicago`
- Last updated by: `Codex REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Stop `center_buy` from entering the ultra-low-price `buy_yes` cohort that the accepted diagnosis isolated as the current settled-loss cluster.

## Allowed files

- `work_packets/REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/engine/evaluator.py`
- `tests/test_center_buy_repair.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/control/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/state/**`
- `src/execution/**`
- `tests/test_runtime_guards.py`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_center_buy_diagnosis.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no ETL/recalibration work
- no broad reporting/dashboard work
- no risk/status/operator summary rewrites
- no schema redesign
- no non-center_buy behavior changes
- no team runtime launch

## Current blocker state

- accepted diagnosis isolated the current `center_buy` loss cluster to `8` settled `buy_yes` losses totaling `-9.0`
- all diagnosed losses sit in `<= 0.02` entry-price buckets
- packet must stay on this one strategy-specific cohort only

## Immediate checklist

- [x] `REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS` frozen
- [x] repair implemented
- [x] adversarial non-center_buy safety test added
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Decide whether to transport accepted commits back to `Architects` or continue branch-local sequencing.
2. Freeze no further packet on this branch until that transport/supersession decision is explicit.
3. Do not widen into other strategy behavior or runtime logic outside a new frozen packet.
