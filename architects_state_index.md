# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex mainline coordination pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `Stage 2 canonical-authority rollout`
- Active packet: `P1.7I-CHAIN-ONLY-QUARANTINE-EXCLUSION-FOLLOW-THROUGH`
- Active packet state: `frozen / ready for execution`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P1.7H-CHAIN-ONLY-QUARANTINE-EXCLUSION-RESOLUTION`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - current packet is `NO_TEAM_DEFAULT`

## Current next action

1. Execute `P1.7I-CHAIN-ONLY-QUARANTINE-EXCLUSION-FOLLOW-THROUGH`
2. Keep chain-only quarantines outside canonical lifecycle truth
3. Make the exclusion explicit and visible without inventing attribution or widening schema/law

## Current out-of-scope dirt

- `AGENTS.md` has unrelated local dirt outside the active packet scope
- `zeus_final_tribunal_overlay/` is an untracked reference directory outside packet scope

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `work_packets/P1.7I-CHAIN-ONLY-QUARANTINE-EXCLUSION-FOLLOW-THROUGH.md`
6. current packet `required_reads`
