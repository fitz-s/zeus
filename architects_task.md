# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-08 America/Chicago`
- Last updated by: `Codex REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Eliminate close-path trace loss between `position_current`, `positions-paper.json`, and `chronicle` so exited/settled positions stop remaining falsely open and settlement history carries durable `exit_price`.

## Allowed files

- `work_packets/REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/db.py`
- `src/state/portfolio.py`
- `src/engine/lifecycle_events.py`
- `src/execution/exit_lifecycle.py`
- `src/execution/harvester.py`
- `tests/test_runtime_guards.py`
- `tests/test_architecture_contracts.py`
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
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no ETL/recalibration work
- no broad historical migration/backfill cleanup
- no risk/status/operator summary rewrites
- no schema redesign
- no team runtime launch

## Current blocker state

- session leftovers rank position/state/settlement trace convergence as the next highest-value open family
- fresh live repo truth shows all 14 `recent_exits` trade_ids still remain open in `position_current` (`5 day0_window`, `9 active`)
- fresh live repo truth also shows all 19 paper `chronicle` settlement rows still missing `exit_price`
- packet must stay on close-path truth surfaces only

## Immediate checklist

- [x] `REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE` frozen
- [x] close-path code-review/test map captured for the packet
- [x] stale-open contradiction reproduced in packet-bounded tests or notes
- [x] future economic-close canonical update repaired
- [x] settlement chronicle `exit_price` durability repaired
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [ ] packet accepted locally
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Accept the repaired close-path packet locally and commit the bounded batch.
2. Run post-close critic + verifier on the accepted boundary before freezing the next packet.
3. Do not widen into ETL, risk/status/operator summary work, or broad historical cleanup without a new packet.
