# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE post-close sync (merged with VERIFY-ETL-RECALIBRATE-CONTAMINATION)`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE`
- State: `POST_CLOSE_PASSED / FINAL_AUDIT_READY`
- Previous packet (merged): `VERIFY-ETL-RECALIBRATE-CONTAMINATION` — `ACCEPTED / POST_CLOSE_PASSED`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Restore paper runtime on a clean code checkout while keeping it attached to the live paper state directory.

(Previous packet VERIFY-ETL-RECALIBRATE-CONTAMINATION objective: Prove the shared ETL/recalibrate chain stays on shared truth surfaces and repair the discovered TIGGE multi-step collapse before advancing into later leftover families.)

## Allowed files

- `work_packets/REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE.md`
- `work_packets/VERIFY-ETL-RECALIBRATE-CONTAMINATION.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `/Users/leofitz/Library/LaunchAgents/com.zeus.paper-trading.plist`
- `/Users/leofitz/Library/LaunchAgents/com.zeus.riskguard.plist`
- `/Users/leofitz/.openclaw/workspace-venus/zeus-paper-runtime-clean/**`
- `src/main.py`
- `scripts/etl_tigge_calibration.py`
- `tests/test_observation_instants_etl.py`
- `tests/test_run_replay_cli.py`
- `tests/test_etl_recalibrate_chain.py`

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
- no trade/lifecycle/risk/status truth repair
- no broad 20-script migration cleanup
- no daemon cutover or scheduler timing claim
- no team runtime launch

## Current blocker state

- packet boundary is post-close passed
- paper launchd services now run from the clean worktree and coherent paper risk rows persisted across multiple fresh ticks
- branch is ready for one final audit before merge consideration

### VERIFY-ETL-RECALIBRATE-CONTAMINATION (merged)

- session leftovers ranked ETL/recalibrate contamination as the highest-risk open family
- fresh repo inspection found one concrete blocker: `etl_tigge_calibration.py` only preserved the last step file per date directory and stamped `lead_hours = 24.0`
- representative ETL scripts migrated to `get_shared_connection()`, subprocess chain proof captured
- packet stayed off trade/lifecycle/risk/status surfaces

## Immediate checklist

### REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE

- [x] `REROUTE-PAPER-LAUNCHD-TO-CLEAN-WORKTREE` frozen
- [x] clean runtime worktree prepared
- [x] paper launchd jobs rerouted
- [x] paper artifact writes remain coherent after re-enable

### VERIFY-ETL-RECALIBRATE-CONTAMINATION (merged)

- [x] `VERIFY-ETL-RECALIBRATE-CONTAMINATION` frozen
- [x] ETL/recalibrate code-review/test map captured for the packet
- [x] shared-binding/import proof captured in tests
- [x] TIGGE multi-step truth repaired
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Push this branch for final audit.
2. Do not widen this packet without a new contradiction.
3. VERIFY-ETL-RECALIBRATE-CONTAMINATION code has been transported via this merge.
