# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Isolate and reroute the live paper-mode background writer that keeps overwriting refreshed artifacts with stale fallback-based snapshots.

## Allowed files

- `work_packets/BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `/Users/leofitz/Library/LaunchAgents/com.zeus.paper-trading.plist`
- `/Users/leofitz/Library/LaunchAgents/com.zeus.riskguard.plist`

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

- the accepted refresh entrypoint can write coherent paper artifacts, but a stale live writer overwrites them again within minutes
- `launchctl` shows the active paper writers are launchd jobs bound to the stale checkout at `/Users/leofitz/.openclaw/workspace-venus/zeus`
- this packet must stay bounded to writer ownership/rerouting before any broader parity redesign

## Immediate checklist

- [x] `BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP` frozen
- [ ] overwrite-after-refresh evidence logged
- [ ] stale paper writer/owner path isolated
- [ ] broader runtime redesign remains explicit

## Next required action

1. Isolate and reroute the stale paper writer/owner path.
2. Freeze a narrower superseding packet only if launchd rerouting alone is insufficient.
