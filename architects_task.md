# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P6.0 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P6.0-STATUS-SUMMARY-INPUT-READINESS`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Make `strategy_health` a real substrate for later status-summary DB cutover without yet cutting `status_summary.py` over or widening into control-plane durability work.

## Allowed files

- `work_packets/P6.0-STATUS-SUMMARY-INPUT-READINESS.md`
- `src/riskguard/riskguard.py`
- `src/state/db.py`
- `tests/test_riskguard.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
- `src/observability/**`
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_runtime_guards.py`
- `tests/test_db.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no `status_summary.py` source cutover yet
- no control-plane durable-write conversion yet
- no `strategy_tracker` deletion or demotion yet
- no schema changes
- no new operator field widening
- no team launch

## Current blocker state

- no blocker inside the frozen P6.0 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P6.0 packet frozen
- [x] `strategy_health` refresh/query substrate installed from lawful inputs
- [x] explicit present-path and absent/stale-path semantics tested
- [x] targeted strategy-health tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P6.0 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P6.0` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze `P6.1-STATUS-SUMMARY-DB-DERIVED` until P6.0 post-close gate passes.
