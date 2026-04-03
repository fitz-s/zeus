# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.2 freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P3.2-POLICY-RESOLVER`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Create the standalone strategy policy resolver before any P3 evaluator or riskguard behavior wiring begins.

## Allowed files

- `work_packets/P3.2-POLICY-RESOLVER.md`
- `src/riskguard/policy.py`
- `tests/test_riskguard.py`
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
- `src/engine/**`
- `src/execution/**`
- `src/state/**`
- `src/supervisor_api/**`
- `src/riskguard/riskguard.py`
- `tests/test_architecture_contracts.py`
- `tests/test_db.py`
- `tests/test_runtime_guards.py`
- `tests/test_live_safety_invariants.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no evaluator-consumption work in this packet
- no cycle-runner or control-plane behavior changes in this packet
- no riskguard emission work in this packet
- no team launch

## Current blocker state

- no blocker inside packet scope
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] read packet authority and scoped runtime surfaces
- [ ] implement the standalone policy resolver
- [ ] verify targeted resolver evidence
- [ ] keep out-of-scope dirt excluded from the packet commit

## Next required action

1. Implement the standalone policy resolver slice.
2. Keep `P3.2-POLICY-RESOLVER` on resolver/test surfaces only.
3. Keep out-of-scope local dirt excluded from any commit.
