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
- Active packet: `P1.7G-CHAIN-QUARANTINE-STRATEGY-RESOLUTION`
- Active packet state: `blocked / human decision required`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P1.7F-RECONCILIATION-SIZE-CORRECTION-DUAL-WRITE` (`eead3bc`)
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - current packet is `NO_TEAM_DEFAULT`

## Current next action

1. Await human decision on chain-only quarantine attribution
2. Freeze a superseding governance/migration packet only after that decision exists
3. Do not continue autonomous P1 work past this boundary

## Current out-of-scope dirt

- `AGENTS.md` has unrelated local dirt outside the active packet scope
- `zeus_final_tribunal_overlay/` is an untracked reference directory outside packet scope

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `work_packets/P1.7G-CHAIN-QUARANTINE-STRATEGY-RESOLUTION.md`
6. current packet `required_reads`
