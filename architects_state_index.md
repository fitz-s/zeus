# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE acceptance sync`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `post-P7R7 bounded bugfix`
- Active packet: `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE`
- Active packet state: `accepted_local / post_close_pending`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE`
- Execution mode default: `solo lead with bounded subagents`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team runtime is active; bounded subagents allowed inside the frozen packet

## Current next action

1. Run post-close critic + verifier on the accepted reroute boundary.
2. Keep core truth logic and broader parity work out of this packet unless a new packet is frozen.
3. Preserve the distinction between clean-runtime routing and downstream consumer parity.

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
5. `work_packets/REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE.md`
