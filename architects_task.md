# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P4.5 acceptance pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `P4.5 ACCEPTED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. P4.5 is accepted and pushed under current repo truth, and the post-close third-party gate must now finish before P4 family closeout can be recorded.

## Allowed files

- post-close review evidence surfaces for accepted P4.5
- `work_packets/P4.5-ANALYTICS-SMOKE-QUERIES.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all repo implementation/runtime/schema surfaces until the post-close gate passes and the family closeout state is recorded
- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
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

- no P4 family closeout before the post-close gate passes
- no dashboard/observability surface widening
- no schema changes
- no cutover
- no team launch

## Current blocker state

- no blocker on the accepted P4.5 boundary itself
- the user-required post-close third-party critic/verifier gate is still pending
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P4.3 packet remains valid under the accepted discrete-settlement authority amendment
- [x] P4.3 runtime/state seam implemented
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P4.3 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P4.4 packet is frozen
- [x] P4.4 runtime/state seam implemented
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P4.4 accepted and pushed

## Next required action

1. Run the post-close third-party critic + verifier on accepted P4.5.
2. Record honest P4 family closeout only after that gate passes.
