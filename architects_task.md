# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.4 closeout pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `AWAITING POST-CLOSE REVIEW / NEXT FREEZE`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Hold at the accepted P3.4 boundary until the required post-close third-party critic + verifier review finishes, then freeze the next packet.

## Allowed files

- `work_packets/P3.5-MANUAL-OVERRIDE-PRECEDENCE.md`
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

- no P3.5 work before post-close review finishes
- no cutover
- no team launch

## Current blocker state

- no active blocker inside the completed P3.4 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P3.4 landed with green riskguard emission evidence
- [x] P3.4 acceptance is recorded honestly
- [ ] run post-close third-party critic review
- [ ] run post-close third-party verifier review
- [ ] if both pass, freeze `P3.5-MANUAL-OVERRIDE-PRECEDENCE`

## Next required action

1. Run post-close third-party critic + verifier on the accepted P3.4 boundary.
2. If both pass, freeze `P3.5-MANUAL-OVERRIDE-PRECEDENCE` before any more implementation.
