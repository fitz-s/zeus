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

- Packet: `P2.2-CYCLE-RUNTIME-EXIT-INTENT-CLOSEOUT`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Prove or reopen the current cycle-runtime exit-intent path without widening into pending-exit or settlement semantics.

## Allowed files

- `work_packets/P2.2-CYCLE-RUNTIME-EXIT-INTENT-CLOSEOUT.md`
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

- no pending-exit handling change
- no economic-close vs settlement change
- no new runtime implementation unless the evidence reopens the claim
- no schema changes
- no team launch

## Current blocker state

- no blocker yet; the packet outcome depends on the evidence suite
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] run the targeted cycle-runtime exit-intent evidence suite
- [ ] adversarially review the closeout claim
- [ ] accept or reopen strictly from the evidence

## Next required action

1. Run the packet evidence suite.
2. Decide closeout vs reopen strictly from the evidence.
3. Keep out-of-scope dirt excluded from any commit.
