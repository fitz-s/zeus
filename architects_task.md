# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.1 acceptance pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `AWAITING NEXT FREEZE`
- Execution mode: `SOLO_EXECUTE`
- Current owner: `Architects mainline lead`

## Objective

No live packet. Current run horizon is satisfied because `P3.1-STRATEGY-POLICY-TABLES` is accepted and pushed.

## Allowed files

- `work_packets/P3.2-POLICY-RESOLVER.md`
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

- no P3.2 work without a new frozen packet
- no cutover
- no team launch

## Current blocker state

- no active blocker inside the completed P3.1 stop boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P3.1 landed with green schema/contract evidence
- [x] P3.1 acceptance is recorded honestly
- [ ] if resuming, freeze `P3.2-POLICY-RESOLVER`

## Next required action

1. Stop at the current packet boundary (`P3.1-STRATEGY-POLICY-TABLES` accepted and pushed).
2. If continuing later, freeze `P3.2-POLICY-RESOLVER` before any implementation.
