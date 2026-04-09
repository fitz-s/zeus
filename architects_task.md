# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE post-close sync`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE`
- State: `POST_CLOSE_PASSED / FINAL_AUDIT_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Restore paper runtime on a clean code checkout while keeping it attached to the live paper state directory.

## Allowed files

- `work_packets/REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `/Users/leofitz/Library/LaunchAgents/com.zeus.paper-trading.plist`
- `/Users/leofitz/Library/LaunchAgents/com.zeus.riskguard.plist`
- `/Users/leofitz/.openclaw/workspace-venus/zeus-paper-runtime-clean/**`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/state/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/control/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/execution/**`
- `src/engine/**`
- `scripts/**`
- `tests/**`
- `tests/test_architecture_contracts.py`
- `tests/test_truth_surface_health.py`
 - `tests/test_pnl_flow_and_audit.py`
 - `tests/test_runtime_guards.py`
 - `tests/test_riskguard.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no core truth math changes in this packet
- no runtime service redesign beyond paper writer ownership/routing
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- packet boundary is post-close passed
- paper launchd services now run from the clean worktree and coherent paper risk rows persisted across multiple fresh ticks
- branch is ready for one final audit before merge consideration

## Immediate checklist

- [x] `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE` frozen
- [x] clean runtime worktree prepared
- [x] paper launchd jobs rerouted
- [x] paper artifact writes remain coherent after re-enable

## Next required action

1. Push this branch for final audit.
2. Do not widen this packet without a new contradiction.
