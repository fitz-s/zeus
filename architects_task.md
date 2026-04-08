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

No live packet is open. Stop at the BUG-MONITOR-SHARED-CONNECTION-REPAIR boundary until a new packet is explicitly frozen.

## Allowed files

- `work_packets/BUG-MONITOR-SHARED-CONNECTION-REPAIR.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/engine/cycle_runner.py`
- `src/engine/cycle_runtime.py`
- `src/engine/monitor_refresh.py`
- `src/state/db.py`
- `tests/test_runtime_guards.py`
- `tests/test_live_safety_invariants.py`
- `tests/test_pnl_flow_and_audit.py`

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

- no RiskGuard packet work in this packet
- no migration-script execution or daemon cutover claim
- no bankroll semantics redesign
- no team runtime launch

## Current blocker state

- BUG-MONITOR-SHARED-CONNECTION-REPAIR passed pre-close and post-close review gates on accepted boundary commit `f5914a8`
- no live packet remains open
- out-of-scope local dirt must remain excluded from future packet commits

## Immediate checklist

- [x] `BUG-MONITOR-SHARED-CONNECTION-REPAIR` frozen
- [x] architecture/code-review/test map captured for the packet
- [x] runtime seam repaired in code
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Do not widen into migration, retirement, or bankroll work without a new packet.
2. Freeze a new packet before any further implementation work.
3. Keep the post-close evidence surfaces available for the next cold start.
