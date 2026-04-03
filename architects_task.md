# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex mainline coordination pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P2.4-ECONOMIC-CLOSE-SETTLEMENT-SPLIT`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Split economic close from settlement so exit fill is no longer terminal settlement truth, while harvester becomes the sole owner of the final settlement transition.

## Allowed files

- `work_packets/P2.4-ECONOMIC-CLOSE-SETTLEMENT-SPLIT.md`
- `src/state/portfolio.py`
- `src/execution/exit_lifecycle.py`
- `src/execution/harvester.py`
- `src/engine/cycle_runtime.py`
- `src/engine/lifecycle_events.py`
- `tests/test_runtime_guards.py`
- `tests/test_live_safety_invariants.py`
- `tests/test_architecture_contracts.py`
- `tests/test_db.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `AGENTS.md`
- `src/riskguard/**`
- `src/control/**`
- `src/supervisor_api/**`
- `migrations/**`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime cutover surfaces
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no cutover or migration claims
- no schema changes
- no team launch

## Current blocker state

- no blocker yet inside packet scope
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] split economic-close vs settlement helpers in runtime truth
- [ ] make exit fill non-terminal and harvester settlement terminal
- [ ] guard economically closed positions from active reprocessing
- [ ] verify paper/live parity and canonical mapping impacts
- [ ] run adversarial review before acceptance

## Next required action

1. Land the economic-close/settlement split in the allowed files only.
2. Verify with targeted tests and explicit adversarial review.
3. Keep out-of-scope dirt excluded from any commit.
