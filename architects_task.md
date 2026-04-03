# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.2 acceptance pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `AWAITING NEXT FREEZE`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Freeze the next P3 packet on evaluator policy consumption only before further implementation begins.

## Allowed files

- `work_packets/P3.3-EVALUATOR-POLICY-CONSUMPTION.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all repo implementation/runtime/schema surfaces until the next packet is frozen
- `AGENTS.md`
- `src/**`
- `tests/**`
- `migrations/**`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no P3.3 work without a new frozen packet
- no cutover
- no team launch

## Current blocker state

- no active blocker inside the completed P3.2 stop boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P3.2 landed with green resolver evidence
- [x] P3.2 acceptance is recorded honestly
- [ ] if resuming, freeze `P3.3-EVALUATOR-POLICY-CONSUMPTION`

## Next required action

1. Stop at the current packet boundary (`P3.2-POLICY-RESOLVER` accepted and pushed).
2. If continuing later, freeze `P3.3-EVALUATOR-POLICY-CONSUMPTION` before any implementation.
