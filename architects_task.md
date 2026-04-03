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

- Packet: `GOV-01-CLOSEOUT-METHODOLOGY-HARDENING`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Codify the stricter closeout/reopen methodology in AGENTS and the autonomous delivery constitution.

## Allowed files

- `work_packets/GOV-01-CLOSEOUT-METHODOLOGY-HARDENING.md`
- `AGENTS.md`
- `docs/governance/zeus_autonomous_delivery_constitution.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `src/**`
- `tests/**`
- `migrations/**`
- `docs/architecture/**`
- `architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no runtime changes
- no schema changes
- no packet-family implementation work
- no team launch

## Current blocker state

- no blocker yet inside packet scope
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] add closure-reopen doctrine
- [ ] add pre-closeout independent review requirement
- [ ] encode that user-found post-closeout issues mean process failure, not normal critic extension
- [ ] verify wording and commit/push

## Next required action

1. Edit the two methodology surfaces.
2. Verify the packet/control wording.
3. Keep out-of-scope dirt excluded from any commit.
