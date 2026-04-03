# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.3 freeze pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `P3 strategy-aware protective spine`
- Active packet: `P3.3-EVALUATOR-POLICY-CONSUMPTION`
- Active packet state: `frozen / ready for execution`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P3.2-POLICY-RESOLVER`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - current packet is `NO_TEAM_DEFAULT`

## Current next action

1. Read the current packet `required_reads` before implementation.
2. Keep `P3.3-EVALUATOR-POLICY-CONSUMPTION` confined to evaluator policy consumption only.
3. Keep out-of-scope local dirt excluded from packet commits.

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
5. `work_packets/P3.3-EVALUATOR-POLICY-CONSUMPTION.md`
6. current packet `required_reads`
