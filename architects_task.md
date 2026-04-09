# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex REFRESH-PAPER-RUNTIME-ARTIFACTS freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REFRESH-PAPER-RUNTIME-ARTIFACTS`
- State: `FROZEN / IMPLEMENTATION_READY`
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

- clean-branch direct truth probes are coherent, but persisted paper artifacts still preserve old snapshots
- `risk_state-paper.db` still reports `portfolio_truth_source=working_state_fallback`, `settlement_sample_size=22`, `daily_loss=13.26`
- `status_summary-paper.json` still follows the stale persisted risk snapshot instead of a refreshed one

## Immediate checklist

- [x] `REFRESH-PAPER-RUNTIME-ARTIFACTS` frozen
- [ ] stale paper artifacts reproduced with packet-bounded evidence
- [ ] bounded refresh entrypoint implemented
- [ ] packet-bounded refresh tests pass
- [ ] broader parity work remains explicit

## Next required action

1. Implement the bounded paper runtime artifact refresh entrypoint.
2. Lock the refresh sequence with packet-bounded tests.
3. If implementation proves a core reader/writer still must change, stop and freeze that deeper packet instead of widening silently.
