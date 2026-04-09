# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP acceptance sync`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP`
- State: `ACCEPTED_LOCAL / POST_CLOSE_PENDING`
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

- the stale paper launchd writers are now disabled, and refreshed paper artifacts stay coherent
- broader runtime rerouting still remains follow-up work outside this accepted boundary
- post-close critic + verifier are still required before the next packet may freeze

## Immediate checklist

- [x] `BUG-PAPER-LAUNCHD-WRITER-OWNERSHIP` frozen
- [x] overwrite-after-refresh evidence logged
- [x] stale paper writer/owner path isolated
- [x] broader runtime redesign remains explicit

## Next required action

1. Run post-close critic + verifier on the accepted ownership boundary.
2. Freeze the next bounded packet instead of widening this one.
