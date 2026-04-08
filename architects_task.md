# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-08 America/Chicago`
- Last updated by: `Codex REPAIR-RESIDUAL-STALE-GHOST-EXCLUSION freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REPAIR-RESIDUAL-STALE-GHOST-EXCLUSION`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Remove residual stale-open ghost rows from runtime read views so past-target canonical leftovers stop poisoning open exposure and loader truth after the trace-convergence repair.

## Allowed files

- `work_packets/REPAIR-RESIDUAL-STALE-GHOST-EXCLUSION.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/db.py`
- `tests/test_pnl_flow_and_audit.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/supervisor_api/**`
- `migrations/**`
 - `src/state/ledger.py`
 - `src/state/projection.py`
 - `src/state/portfolio.py`
 - `src/execution/**`
 - `src/engine/**`
 - `tests/test_runtime_guards.py`
 - `tests/test_architecture_contracts.py`
 - `tests/test_healthcheck.py`
 - `.github/workflows/**`
 - `.claude/CLAUDE.md`
 - `zeus_final_tribunal_overlay/**`

## Non-goals

- no ETL/recalibration work
- no broad historical migration/backfill cleanup
- no risk/status/operator summary rewrites
- no exit-writer or settlement-writer changes
- no schema redesign
- no team runtime launch

## Current blocker state

- after the accepted trace packet, live read views still show 12 open rows but only 3 are legitimate future-target positions
- the remaining 9 rows are residual ghosts with past target dates or synthetic stale rows that still poison open exposure and keep loader status degraded
- packet must stay on read-side ghost exclusion only

## Immediate checklist

- [x] `REPAIR-RESIDUAL-STALE-GHOST-EXCLUSION` frozen
- [x] residual ghost contradiction reproduced in packet-bounded tests or notes
- [x] adversarial ghost-exclusion test added
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Cherry-pick accepted commit `f179cd3` onto `Architects` cleanly when ready.
2. Update the live branch control surfaces only after transport is complete.
3. Do not widen into exit writers, ETL, risk/status/operator summary work, or broad historical cleanup without a new packet.
