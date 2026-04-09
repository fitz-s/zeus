# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-08 America/Chicago`
- Last updated by: `Codex DIAGNOSE-CENTER-BUY-FAILURE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `DIAGNOSE-CENTER-BUY-FAILURE`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Produce a reproducible, strategy-isolated diagnosis of why `center_buy` is currently losing in paper mode, using one truthful aggregation path instead of mixed ad hoc queries.

## Allowed files

- `work_packets/DIAGNOSE-CENTER-BUY-FAILURE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `scripts/diagnose_center_buy_failure.py`
- `tests/test_center_buy_diagnosis.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/state/**`
- `src/execution/**`
- `src/engine/**`
- `tests/test_runtime_guards.py`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no runtime logic changes
- no ETL/recalibration work
- no broad reporting/dashboard work
- no risk/status/operator summary rewrites
- no schema redesign
- no team runtime launch

## Current blocker state

- lower-layer trace seams are stable enough that strategy diagnosis is now meaningful
- fresh live truth still reports `center_buy` settled PnL at `8 trades / -9.0`, while other surfaces can disagree unless deduped and filtered carefully
- packet must stay diagnosis-only and avoid mutating runtime behavior

## Immediate checklist

- [x] `DIAGNOSE-CENTER-BUY-FAILURE` frozen
- [x] diagnosis script created
- [x] adversarial diagnosis test added
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Freeze the next lawful center_buy repair packet from the diagnosis evidence.
2. Keep diagnosis artifacts as the authority for why the next repair exists.
3. Do not widen into strategy behavior changes or runtime logic outside the next frozen packet.
