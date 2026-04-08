# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-07 America/Chicago`
- Last updated by: `Codex BUG-MONITOR-SHARED-CONNECTION-REPAIR freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `NO_LIVE_PACKET / STOP_AT_PACKET_BOUNDARY`
- Execution mode: `SOLO_LEAD / WAITING_FOR_NEXT_FREEZE`
- Current owner: `Architects mainline lead`

## Objective

No live packet is open. Stop at the BUG-BANKROLL-TRUTH-CONSISTENCY boundary until a new packet is explicitly frozen.

## Allowed files

- `work_packets/BUG-BANKROLL-TRUTH-CONSISTENCY.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/engine/cycle_runtime.py`
- `src/riskguard/riskguard.py`
- `src/observability/status_summary.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_riskguard.py`
- `tests/test_cross_module_relationships.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `migrations/**`
- `tests/test_architecture_contracts.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no migration-script execution or daemon cutover claim
- no control-plane durability work
- no lifecycle/projection rewrite
- no ETL/recalibration contamination work
- no team runtime launch

## Current blocker state

- BUG-BANKROLL-TRUTH-CONSISTENCY passed pre-close and post-close review gates on accepted boundary commit `7cde843`
- no live packet remains open
- out-of-scope local dirt must remain excluded from future packet commits

## Immediate checklist

- [x] `BUG-BANKROLL-TRUTH-CONSISTENCY` frozen
- [x] architecture/code-review/test map captured for the packet
- [x] bankroll contract repaired in code
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Do not widen into control-plane durability, lifecycle/projection, or ETL contamination work without a new packet.
2. Freeze a new packet before any further implementation work.
3. Keep the bankroll-truth evidence surfaces available for the next cold start.
