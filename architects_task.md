# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex REFRESH-PAPER-RUNTIME-ARTIFACTS acceptance sync`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REFRESH-PAPER-RUNTIME-ARTIFACTS`
- State: `ACCEPTED_LOCAL / POST_CLOSE_PENDING`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Add a bounded, reproducible refresh path for paper runtime artifacts so stale persisted snapshots can be regenerated from current clean-branch truth.

## Allowed files

- `work_packets/REFRESH-PAPER-RUNTIME-ARTIFACTS.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `scripts/refresh_paper_runtime_artifacts.py`
- `tests/test_runtime_artifact_refresh.py`

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
- no `src/observability/status_summary.py` parity redesign yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- packet-bounded refresh entrypoint evidence now passes, but post-close critic + verifier are still required before the next packet may freeze
- clean-branch direct truth probes remain coherent while persisted paper artifacts still preserve old snapshots
- broader downstream parity work remains follow-up work and must be handled by a new packet instead of widening this accepted boundary

## Immediate checklist

- [x] `REFRESH-PAPER-RUNTIME-ARTIFACTS` frozen
- [x] stale paper artifacts reproduced with packet-bounded evidence
- [x] bounded refresh entrypoint implemented
- [x] packet-bounded refresh tests pass
- [x] broader parity work remains explicit

## Next required action

1. Run post-close critic + verifier on the accepted refresh boundary.
2. Freeze the next bounded packet instead of widening this one.
