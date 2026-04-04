# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P5.1 acceptance pass`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `P5 lifecycle phase engine start`
- Active packet: `P5.1-LIFECYCLE-PHASE-KERNEL`
- Active packet state: `accepted and pushed / post-close gate pending`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P5.1-LIFECYCLE-PHASE-KERNEL`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team is active

## Current next action

1. Run the post-close third-party critic and verifier gate on accepted `P5.1-LIFECYCLE-PHASE-KERNEL`.
2. Freeze the next P5 packet only after the post-close gate passes.
3. Keep out-of-scope local dirt excluded from packet commits.

## Current out-of-scope dirt

- `README.md` is untracked and out of scope
- `docs/architecture/zeus_durable_architecture_spec.md` has unrelated local modifications and stays out of scope
- `docs/governance/zeus_runtime_delta_ledger.md` has unrelated local modifications and stays out of scope
- `docs/architecture/zeus_design_philosophy.md` has an unrelated local deletion and stays out of scope
- `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` is untracked and out of scope
- `docs/archives/` is untracked and out of scope
- `architects_progress_archive.md`, `root_progress.md`, and `root_task.md` have unrelated local deletions and stay out of scope
- `next_round_handoff.md` has an unrelated local deletion and stays out of scope
- `src/signal/forecast_uncertainty.py` has unrelated local modifications and stays out of scope
- `.trash/` and `memory/` are untracked workspace artifacts outside packet scope
- local DB artifacts (`risk_state.db`, `trading.db`, `zeus.db`, `zeus_state.db`) are untracked and out of scope
- `tests/test_calibration_quality.py` and `work_packets/MATH-002-BIN-HIT-RATE-CALIBRATION.md` are unrelated untracked files outside packet scope
- `zeus_final_tribunal_overlay/` is a tracked reference subtree outside packet scope and must remain untouched

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `work_packets/P5.1-LIFECYCLE-PHASE-KERNEL.md`
