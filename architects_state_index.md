# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P3 family closeout pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `P3 family complete`
- Active packet: `none`
- Active packet state: `P3 family closeout recorded / awaiting next phase freeze`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P3.5-MANUAL-OVERRIDE-PRECEDENCE`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team is active

## Current next action

1. Stop at the completed P3 family boundary.
2. If work continues, freeze the next non-P3 packet explicitly before implementation.
3. Keep out-of-scope local dirt excluded from packet commits.

## Current out-of-scope dirt

- `README.md` is untracked and out of scope
- `docs/architecture/zeus_durable_architecture_spec.md` has unrelated local modifications and stays out of scope
- `docs/governance/zeus_runtime_delta_ledger.md` has unrelated local modifications and stays out of scope
- `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` is untracked and out of scope
- `docs/archives/` is untracked and out of scope
- `zeus_final_tribunal_overlay/` is an untracked reference directory outside packet scope
- `architects_progress_archive.md`, `root_progress.md`, and `root_task.md` have unrelated local deletions and stay out of scope
- `tests/test_calibration_quality.py` and `work_packets/MATH-002-BIN-HIT-RATE-CALIBRATION.md` are unrelated untracked files outside packet scope

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. the next frozen non-P3 packet, if work resumes
