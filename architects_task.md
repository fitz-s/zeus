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

- Packet: `BUG-BANKROLL-TRUTH-CONSISTENCY`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Eliminate bankroll-truth loss between entry sizing, RiskGuard, and status summary before touching broader lifecycle or ETL seams.

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

- bankroll truth still diverges across `cycle_runtime`, `riskguard`, and `status_summary`
- this packet is frozen to fix that single K-level seam without widening into other open families
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] `BUG-BANKROLL-TRUTH-CONSISTENCY` frozen
- [ ] architecture/code-review/test map captured for the packet
- [ ] bankroll contract repaired in code
- [ ] targeted tests pass
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] packet accepted locally
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Map the packet into bounded entry / risk / operator truth slices.
2. Repair the bankroll contract only inside `cycle_runtime`, `riskguard`, `status_summary`, and targeted tests.
3. Do not widen into control-plane durability, lifecycle/projection, or ETL contamination work without a new packet.
