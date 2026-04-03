# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex mainline coordination pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none (P2 closed)`
- State: `IDLE / RUN-HORIZON STOP BOUNDARY REACHED`
- Execution mode: `SOLO_EXECUTE`
- Current owner: `Architects mainline lead`

## Objective

No active packet. Current run horizon is satisfied because P2 is closed.

## Allowed files

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

- no P3 work without a new frozen packet
- no law rewrite
- no cutover
- no team launch

## Current blocker state

- no active blocker inside P2; the current stop boundary is successful P2 closure
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P2 packet chain is complete and accepted
- [x] adversarial review approved the final P2 packet claim
- [x] P2 is closed honestly
- [ ] if resuming, freeze `P3.1-STRATEGY-POLICY-TABLES`

## Next required action

1. Stop at the current user-request horizon (`P2 closed`).
2. If continuing later, freeze the first P3 packet before any implementation.
