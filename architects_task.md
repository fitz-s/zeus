# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex P4.2 acceptance pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `P4.2 ACCEPTED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. P4.2 is accepted and pushed under current repo truth, but the post-close third-party review gate must still pass before P4.3 can freeze.

## Allowed files

- post-close review evidence surfaces for accepted P4.2
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- the next frozen packet surfaces only after the post-close gate passes

## Forbidden files

- all repo implementation/runtime/schema surfaces until the post-close gate passes and the next packet is frozen
- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
- `src/execution/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/supervisor_api/**`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_replay_time_provenance.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no `P4.3-EXECUTION-FACTS` freeze before the post-close gate passes
- no schema changes
- no cutover
- no team launch

## Current blocker state

- no blocker on the accepted P4.2 boundary itself
- the user-required post-close third-party critic/verifier gate is still pending
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P4.1 post-close third-party critic review passed
- [x] P4.1 post-close third-party verifier review passed
- [x] P4.2 packet is frozen
- [x] P4.2 runtime/state seam implemented
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P4.2 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Run the post-close third-party critic + verifier on the accepted P4.2 boundary.
2. Freeze `P4.3-EXECUTION-FACTS` only after that gate passes.
