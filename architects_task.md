# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P6.1 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P6.1-STATUS-SUMMARY-DB-DERIVED`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Move `status_summary.py` off JSON/state-object primary truth and onto DB-backed `position_current`, `strategy_health`, and durable policy reads without widening into control-plane durability or strategy-tracker deletion.

## Allowed files

- `work_packets/P6.1-STATUS-SUMMARY-DB-DERIVED.md`
- `src/observability/status_summary.py`
- `src/state/db.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_healthcheck.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
- `src/riskguard/**`
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `tests/test_architecture_contracts.py`
- `tests/test_runtime_guards.py`
- `tests/test_riskguard.py`
- `tests/test_db.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no control-plane durable-write conversion yet
- no `strategy_tracker` deletion or demotion yet
- no schema changes
- no unrelated operator field widening
- no team launch

## Current blocker state

- no blocker inside the frozen P6.1 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P6.1 packet frozen
- [x] status summary primary truth switched to DB-backed surfaces
- [x] explicit degraded-state handling proven on missing/stale DB substrate paths
- [x] targeted status-summary and healthcheck tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P6.1 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P6.1` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze `P6.2-CONTROL-PLANE-DURABLE-OVERRIDE-WRITES` until P6.1 post-close gate passes.
