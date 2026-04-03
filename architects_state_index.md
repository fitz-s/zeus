# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex mainline coordination pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `Stage 3 / P2 execution-truth mainline`
- Active packet: `P2.4-ECONOMIC-CLOSE-SETTLEMENT-SPLIT`
- Active packet state: `frozen / ready for execution`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P2.3-PENDING-EXIT-OWNERSHIP-HARDENING`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - current packet is `NO_TEAM_DEFAULT`

## Current next action

1. Execute `P2.4-ECONOMIC-CLOSE-SETTLEMENT-SPLIT`
2. Keep scope on the economic-close/settlement split only
3. Do not widen into cutover or broader migration claims in this packet

## Current out-of-scope dirt

- `AGENTS.md` has unrelated local dirt outside the active packet scope
- `zeus_final_tribunal_overlay/` is an untracked reference directory outside packet scope

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `work_packets/P2.4-ECONOMIC-CLOSE-SETTLEMENT-SPLIT.md`
6. current packet `required_reads`
