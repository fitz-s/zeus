# architects_state_index.md

Purpose:
- shortest current-state entrypoint for fresh sessions
- minimizes reread cost before packet execution resumes

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE post-close`
- Authority scope: `current-state pointer only`

## Current state

- Stage: `post-P7R7 bounded bugfix`
- Active packet: `BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE`
- Active packet state: `accepted locally / post-close passed / ready for next packet freeze`
- Active packet owner: `Architects mainline lead`
- Last accepted packet: `BUG-LOAD-PORTFOLIO-MODED-DB-PROBE`
- Execution mode default: `solo lead with bounded subagents`
- Team status:
  - allowed in principle after `FOUNDATION-TEAM-GATE`
  - no team runtime is active; bounded subagents allowed inside the frozen packet

## Current next action

1. Freeze the next deeper truth-unification packet around comparator/shadow cleanup or output-layer parity, whichever is now the tightest live seam.
2. Keep `src/state/decision_chain.py` fallback-reader cleanup and RiskGuard output parity work out of the accepted stage-event packet unless a later packet explicitly widens it.
3. Preserve the distinction between fixing the first active counting seam and claiming full settlement-authority convergence.

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
5. `work_packets/BUG-LEGACY-SETTLED-STAGE-EVENT-DEDUPE.md`
