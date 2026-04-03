# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3.5 freeze pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `P3 strategy-aware protective spine`
- Active packet: `P3.5-MANUAL-OVERRIDE-PRECEDENCE`
- Active packet state: `frozen / ready for execution`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P3.4-RISKGUARD-POLICY-EMISSION`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - current packet is `NO_TEAM_DEFAULT`

## Current next action

1. Read the current packet `required_reads` before implementation.
2. Keep `P3.5-MANUAL-OVERRIDE-PRECEDENCE` confined to final precedence proof only.
3. Keep out-of-scope local dirt excluded from packet commits.

## Current out-of-scope dirt

- `README.md` is untracked and out of scope
- `docs/architecture/zeus_durable_architecture_spec.md` has unrelated local modifications and stays out of scope
- `docs/governance/zeus_runtime_delta_ledger.md` has unrelated local modifications and stays out of scope
- `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` is untracked and out of scope
- `docs/archives/` is untracked and out of scope
- `zeus_final_tribunal_overlay/` is an untracked reference directory outside packet scope
- `architects_progress_archive.md`, `root_progress.md`, and `root_task.md` have unrelated local deletions and stay out of scope

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `work_packets/P3.5-MANUAL-OVERRIDE-PRECEDENCE.md`
6. current packet `required_reads`
