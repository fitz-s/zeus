# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.1 acceptance pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `P3 strategy-aware protective spine`
- Active packet: `none (P3.1-STRATEGY-POLICY-TABLES accepted and pushed)`
- Active packet state: `awaiting next freeze`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P3.1-STRATEGY-POLICY-TABLES`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team is active

## Current next action

1. If P3 continues, freeze `P3.2-POLICY-RESOLVER` next.
2. Keep the next P3 slice on policy resolution only.
3. Keep out-of-scope local dirt excluded from the next packet commit.

## Current out-of-scope dirt

- `README.md` is untracked and out of scope
- `docs/architecture/zeus_durable_architecture_spec.md` has unrelated local modifications and stays out of scope
- `docs/governance/zeus_runtime_delta_ledger.md` has unrelated local modifications and stays out of scope
- `zeus_final_tribunal_overlay/` is an untracked reference directory outside packet scope

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `docs/architecture/zeus_durable_architecture_spec.md` — P3 section
6. `work_packets/P3.1-STRATEGY-POLICY-TABLES.md`
7. freeze the next packet before further implementation
