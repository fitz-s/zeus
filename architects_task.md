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

- Packet: `P1.8-CANONICAL-AUTHORITY-CLOSEOUT`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Run the narrow Stage 2 / P1 closeout evidence suite and close P1 only if repo-truth evidence proves canonical authority is honestly installed.

## Allowed files

- `work_packets/P1.8-CANONICAL-AUTHORITY-CLOSEOUT.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `AGENTS.md`
- `src/**`
- `tests/**`
- `migrations/**`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime cutover surfaces
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no new runtime/schema/test implementation
- no P2 work mixed in
- no law rewrite
- no cutover
- no team launch

## Current blocker state

- no blocker yet; packet outcome depends on the evidence suite
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] run authority checks
- [ ] run the targeted P1 closeout suite
- [ ] adversarially review the closeout claim
- [ ] close P1 only if the evidence remains green

## Next required action

1. Run the packet evidence suite.
2. Decide closeout vs reopen strictly from the evidence.
3. Keep out-of-scope dirt excluded from any commit.
