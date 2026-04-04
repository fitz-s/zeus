# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3 family closeout pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `P3 FAMILY COMPLETE / AWAITING NEXT PHASE FREEZE`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. The P3 family is complete under current repo truth.

## Allowed files

- future packet surfaces only after a new freeze

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

- no next-phase work without a new frozen packet
- no cutover
- no team launch

## Current blocker state

- no active blocker inside the completed P3 family boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P3.5 landed with green precedence evidence
- [x] P3.5 acceptance is recorded honestly
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P3 family closeout is recorded honestly

## Next required action

1. Stop at the completed P3 family boundary.
2. If continuing later, freeze the next non-P3 packet before any implementation.
