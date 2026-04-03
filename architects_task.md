# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.1 closeout pass`
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

Freeze the next P3 packet on policy resolution only before further implementation begins.

## Allowed files

- `work_packets/P3.2-POLICY-RESOLVER.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files until the next packet is frozen
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

- no implementation before the next packet is frozen
- no runtime/schema edits inside this control-only closeout slice
- no team launch

## Current blocker state

- no blocker inside this closeout slice
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] read the P3 policy-resolver authority surfaces
- [ ] freeze `P3.2-POLICY-RESOLVER`
- [ ] keep out-of-scope dirt excluded from the next packet commit

## Next required action

1. Freeze `P3.2-POLICY-RESOLVER`.
2. Keep the next P3 slice on policy resolution only.
3. Keep out-of-scope dirt excluded from any commit.
