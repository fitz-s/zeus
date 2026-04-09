> Historical archive surface. No longer active control authority; use `CURRENT_STATE.md` and the current work packet for live control entry.

# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex GOV-PACKET-ENTRY-CONTROL-SURFACE freeze`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `post-P7R7 bounded bugfix`
- Active packet: `GOV-PACKET-ENTRY-CONTROL-SURFACE`
- Active packet state: `frozen / implementation ready`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `GOV-AUTHORITY-AMENDMENT-AFTER-ARCHIVE`
- Execution mode default: `solo lead with bounded subagents`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team runtime is active; bounded subagents allowed inside the frozen packet

## Current next action

1. Define the replacement live control entry surface and update routing files.
2. Archive/demote the superseded root/architects ledgers.
3. Keep runtime logic and broader governance rewrites out of this packet.

## Current out-of-scope dirt

- `README.md` is untracked and out of scope
- `docs/architecture/zeus_durable_architecture_spec.md` has unrelated local modifications and stays out of scope
- `docs/governance/zeus_runtime_delta_ledger.md` has unrelated local modifications and stays out of scope
- `docs/architecture/zeus_design_philosophy.md` has an unrelated local deletion and stays out of scope
- `docs/TOP_PRIORITY_zeus_reality_crisis_response.md` is untracked and out of scope
- `docs/archives/` is untracked and out of scope
- `docs/archives/architects_progress_archive.md` and `docs/archives/handoffs/next_round_handoff_p4_start.md` are historical/archive surfaces outside this packet unless only path references need updating
- `root_progress.md` and `root_task.md` are in scope only for role clarification, not archival removal
- `.trash/` and `memory/` are untracked workspace artifacts outside packet scope
- local DB artifacts (`risk_state.db`, `trading.db`, `zeus.db`, `zeus_state.db`) are untracked and out of scope
- `tests/test_calibration_quality.py` and `work_packets/MATH-002-BIN-HIT-RATE-CALIBRATION.md` are unrelated untracked files outside packet scope
- `zeus_final_tribunal_overlay/` is a tracked reference subtree outside packet scope and must remain untouched

## Fresh-session read order

1. `AGENTS.md`
2. `architects_state_index.md`
3. `architects_task.md`
4. `architects_progress.md`
5. `work_packets/GOV-PACKET-ENTRY-CONTROL-SURFACE.md`
