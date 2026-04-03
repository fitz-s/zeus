# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.1 freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P3.1-STRATEGY-POLICY-TABLES`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Establish the durable strategy-policy table/bootstrap substrate that P3 needs before any resolver or actuation work begins.

## Allowed files

- `work_packets/P3.1-STRATEGY-POLICY-TABLES.md`
- `migrations/2026_04_02_architecture_kernel.sql`
- `src/state/db.py`
- `tests/test_architecture_contracts.py`
- `tests/test_db.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `src/riskguard/**`
- `src/control/**`
- `src/engine/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `tests/test_riskguard.py`
- `tests/test_runtime_guards.py`
- `tests/test_live_safety_invariants.py`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no resolver/evaluator/riskguard actuation work in this packet
- no manual override precedence work in this packet
- no team launch

## Current blocker state

- no blocker inside packet scope
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] implement durable strategy-policy table/bootstrap surfaces
- [ ] verify targeted schema/db contract evidence
- [ ] keep out-of-scope dirt excluded from the packet commit

## Next required action

1. Implement the durable strategy-policy table/bootstrap slice.
2. Keep `P3.1-STRATEGY-POLICY-TABLES` on migration/db helper surfaces only.
3. Keep out-of-scope dirt excluded from any commit.
