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
- State: `REOPENED CONTRADICTION / IMPLEMENTATION_VERIFIED`
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

- realized-truth contract has been repaired in code across `riskguard` and `status_summary`
- targeted convergence tests now pass, and fresh paper-mode SQL/JSON evidence converges at `-13.03` across canonical facts, `risk_state`, and `status_summary`
- pre-close critic + verifier still need to run before local acceptance
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] `REPAIR-REALIZED-TRUTH-CONVERGENCE` frozen
- [x] architecture/code-review/test map captured for the packet
- [x] realized-truth contract repaired in code
- [x] targeted tests pass
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] packet accepted locally
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Run pre-close critic review on the repaired realized-truth seam.
2. Run pre-close verifier review on the repaired realized-truth seam.
3. Do not widen into projection-query cleanup, control-plane durability, lifecycle/projection, or ETL contamination work without a new packet.
