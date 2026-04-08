# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-08 America/Chicago`
- Last updated by: `Codex BUG-CANONICAL-CLOSURE-TRACEABILITY closure slice`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `BUG-CANONICAL-CLOSURE-TRACEABILITY`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Restore one truthful close path so execution facts, outcome facts, and settlement legality stay durable and semantically aligned before broader projection cleanup.

## Allowed files

- `work_packets/BUG-CANONICAL-CLOSURE-TRACEABILITY.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/state/db.py`
- `src/execution/harvester.py`
- `src/state/lifecycle_manager.py`
- `tests/test_db.py`
- `tests/test_architecture_contracts.py`
- `tests/test_runtime_guards.py`

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
- no ETL/recalibration contamination work
- no team runtime launch

## Current blocker state

- realized-truth contract has been repaired in code across `riskguard` and `status_summary`
- targeted convergence tests now pass, and fresh paper-mode SQL/JSON evidence converges at `-13.03` across canonical facts, `risk_state`, and `status_summary`
- pre-close critic + verifier passed on the repaired realized-truth seam
- post-close third-party critic + verifier still need to run before packet closeout
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] `BUG-CANONICAL-CLOSURE-TRACEABILITY` frozen
- [x] architecture/code-review/test map captured for the packet
- [x] closure contract repaired in code
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Cherry-pick accepted commit `89579cb` onto `Architects` cleanly when ready.
2. Update the live branch control surfaces only after transport is complete.
3. Do not widen into projection-query cleanup, control-plane durability, or ETL contamination work without a new packet.
