# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.5 freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P3.5-MANUAL-OVERRIDE-PRECEDENCE`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Prove final manual override precedence on the active evaluator/resolver path before declaring P3 complete.

## Allowed files

- `work_packets/P3.5-MANUAL-OVERRIDE-PRECEDENCE.md`
- `src/riskguard/policy.py`
- `tests/test_pnl_flow_and_audit.py`
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
- `src/riskguard/riskguard.py`
- `src/engine/**`
- `src/execution/**`
- `src/state/**`
- `src/supervisor_api/**`
- `tests/test_architecture_contracts.py`
- `tests/test_db.py`
- `tests/test_runtime_guards.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no riskguard emission changes in this packet
- no control-plane-write changes in this packet
- no post-P3 phase work in this packet
- no team launch

## Current blocker state

- no blocker inside packet scope
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] read packet authority and precedence surfaces
- [ ] implement/lock final precedence proof
- [ ] verify targeted precedence evidence
- [ ] keep out-of-scope local dirt excluded from the packet commit

## Next required action

1. Implement the final manual-override-precedence slice.
2. Keep `P3.5-MANUAL-OVERRIDE-PRECEDENCE` on precedence/test surfaces only.
3. Keep out-of-scope local dirt excluded from any commit.
