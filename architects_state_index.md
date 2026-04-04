# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P4.3 acceptance pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `P4 in progress`
- Active packet: `none`
- Active packet state: `P4.3 accepted and pushed / post-close gate pending`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P4.3-EXECUTION-FACTS`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team is active

## Current next action

1. Run the post-close third-party critic and verifier gate on accepted P4.3.
2. Only freeze `P4.4-OUTCOME-FACTS` after the post-close gate passes.
3. Keep out-of-scope local dirt excluded from packet commits.

## Current out-of-scope dirt

- `README.md` is untracked and out of scope
- `docs/architecture/zeus_durable_architecture_spec.md` has unrelated local modifications and stays out of scope
- `docs/governance/zeus_runtime_delta_ledger.md` has unrelated local modifications and stays out of scope
- `docs/architecture/zeus_design_philosophy.md` has an unrelated local deletion and stays out of scope
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
5. `work_packets/P4.3-EXECUTION-FACTS.md`
