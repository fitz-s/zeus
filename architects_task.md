# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.3 freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P3.3-EVALUATOR-POLICY-CONSUMPTION`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Wire strategy policy consumption into evaluator decisioning before any later riskguard-emission packet begins.

## Allowed files

- `work_packets/P3.3-EVALUATOR-POLICY-CONSUMPTION.md`
- `src/engine/evaluator.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_runtime_guards.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
- `src/execution/**`
- `src/state/**`
- `src/supervisor_api/**`
- `src/riskguard/**`
- `tests/test_architecture_contracts.py`
- `tests/test_db.py`
- `tests/test_riskguard.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no riskguard-emission work in this packet
- no control-plane-write changes in this packet
- no cycle-runner behavior changes in this packet
- no team launch

## Current blocker state

- no blocker inside packet scope
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] read packet authority and scoped evaluator surfaces
- [ ] implement evaluator policy consumption
- [ ] verify targeted evaluator evidence
- [ ] keep out-of-scope local dirt excluded from the packet commit

## Next required action

1. Implement the evaluator policy-consumption slice.
2. Keep `P3.3-EVALUATOR-POLICY-CONSUMPTION` on evaluator/test surfaces only.
3. Keep out-of-scope local dirt excluded from any commit.
