# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-05 America/Chicago`
- Last updated by: `Codex P7R3 freeze`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `P7R3 legacy position_events collision repair`
- Active packet: `P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR`
- Active packet state: `frozen / ready for execution`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `P7R2-DELTA-05-INIT-SCHEMA-ADDITIVE-CANONICAL-TABLES`
- Execution mode default: `solo`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team is active

## Current next action

1. Implement `P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR` inside its frozen boundary.
2. Run targeted schema/bootstrap evidence plus pre-close critic + verifier before any acceptance claim.
3. Keep out-of-scope local dirt excluded from packet commits.

## Current out-of-scope dirt

- `README.md` is untracked and out of scope
- `docs/architecture/zeus_durable_architecture_spec.md` has unrelated local modifications and stays out of scope
- `docs/governance/zeus_runtime_delta_ledger.md` has unrelated local modifications and stays out of scope
- `docs/architecture/zeus_design_philosophy.md` has an unrelated local deletion and stays out of scope
- `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` is untracked and out of scope
- `docs/archives/` is untracked and out of scope
- `architects_progress_archive.md`, `root_progress.md`, and `root_task.md` have unrelated local deletions and stay out of scope
- `next_round_handoff.md` has unrelated local modifications and stays out of scope
- `.trash/` and `memory/` are untracked workspace artifacts outside packet scope
- local DB artifacts (`risk_state.db`, `trading.db`, `zeus.db`, `zeus_state.db`) are untracked and out of scope
- `tests/test_calibration_quality.py` and `work_packets/MATH-002-BIN-HIT-RATE-CALIBRATION.md` are unrelated untracked files outside packet scope
- `zeus_final_tribunal_overlay/` is a tracked reference subtree outside packet scope and must remain untouched

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. current active packet
