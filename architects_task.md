# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.5 closeout pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `AWAITING POST-CLOSE REVIEW / P3 FAMILY CLOSEOUT`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Hold at the accepted P3.5 boundary until the required post-close third-party critic + verifier review finishes, then record the P3 family closeout truth.

## Allowed files

- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all repo implementation/runtime/schema surfaces until post-close review finishes
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

- no post-P3 phase work before post-close review finishes
- no cutover
- no team launch

## Current blocker state

- no active blocker inside the completed P3.5 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P3.5 landed with green precedence evidence
- [x] P3.5 acceptance is recorded honestly
- [ ] run post-close third-party critic review
- [ ] run post-close third-party verifier review
- [ ] if both pass, record the P3 family closeout truth

## Next required action

1. Run post-close third-party critic + verifier on the accepted P3.5 boundary.
2. If both pass, record the P3 family closeout truth before moving beyond P3.
