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

- Packet: `REPAIR-REALIZED-TRUTH-CONVERGENCE`
- State: `REOPENED CONTRADICTION / FROZEN REPAIR`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Reopen and repair the realized-PnL truth seam so current-mode canonical settlement facts, RiskGuard, and operator summary converge before any other packet advances.

## Allowed files

- `work_packets/REPAIR-REALIZED-TRUTH-CONVERGENCE.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/riskguard/riskguard.py`
- `src/observability/status_summary.py`
- `tests/test_riskguard.py`
- `tests/test_pnl_flow_and_audit.py`
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
- no projection-query compatibility cleanup
- no control-plane durability work
- no lifecycle closure/projection rewrite
- no ETL/recalibration contamination work
- no team runtime launch

## Current blocker state

- fresh runtime evidence disproves the previously closed bankroll/truth boundary: `outcome_fact` and deduped `chronicle` show `-13.03` while `risk_state` and `status_summary` still show realized PnL near `+208.89`
- this repair packet is frozen to restore one realized-truth seam before any other packet advances
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] `REPAIR-REALIZED-TRUTH-CONVERGENCE` frozen
- [ ] architecture/code-review/test map captured for the packet
- [ ] realized-truth contract repaired in code
- [ ] targeted tests pass
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] packet accepted locally
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Map the repair into RiskGuard truth-source and operator-summary convergence slices.
2. Repair the realized-truth contract only inside `riskguard.py`, `status_summary.py`, and targeted tests.
3. Do not widen into projection-query cleanup, control-plane durability, lifecycle/projection, or ETL contamination work without a new packet.
