# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P4 family closeout pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `P4 FAMILY COMPLETE / AWAITING NEXT PHASE FREEZE`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. The P4 family is complete under current repo truth.

## Allowed files

- future packet surfaces only after a new non-P4 freeze

## Forbidden files

- all repo implementation/runtime/schema surfaces until the next packet is frozen
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

- no next-phase work without a new frozen packet
- no dashboard/observability surface widening
- no schema changes
- no cutover
- no team launch

## Current blocker state

- no active blocker inside the completed P4 family boundary
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

1. Stop at the completed P4 family boundary.
2. If continuing later, freeze the next non-P4 packet before any implementation.
