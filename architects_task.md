# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex GOV-01 closeout pass`
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

Freeze the first real P3 packet after honest GOV-01 closeout.

## Allowed files

- `work_packets/P3.1-STRATEGY-POLICY-TABLES.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files until the next packet is frozen
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

- no implementation before the next packet is frozen
- no runtime/schema edits inside this control-only closeout slice
- no team launch

## Current blocker state

- no blocker inside this closeout slice
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] read P3 authority surfaces
- [ ] freeze `P3.1-STRATEGY-POLICY-TABLES`
- [ ] keep out-of-scope dirt excluded from the next packet commit

## Next required action

1. Freeze `P3.1-STRATEGY-POLICY-TABLES`.
2. Keep the first P3 slice on durable strategy-policy table/bootstrap surfaces only.
3. Keep out-of-scope dirt excluded from any commit.
