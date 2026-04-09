# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE`
- State: `FROZEN / IMPLEMENTATION_READY`
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

- paper runtime is currently disabled on the launchd side
- stale-writer ownership is fixed, but clean-runtime routing is not yet restored
- this packet must stay bounded to clean worktree routing and paper launchd ownership only

## Immediate checklist

- [x] `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE` frozen
- [ ] clean runtime worktree prepared
- [ ] paper launchd jobs rerouted
- [ ] paper artifact writes remain coherent after re-enable

## Next required action

1. Prepare a stable clean worktree for runtime use.
2. Reroute the two paper launchd jobs onto that clean worktree.
3. Verify that refreshed artifacts stay coherent after re-enable.
