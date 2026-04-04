# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P5.2 freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P5.2-FOLD-LEGALITY-FOLLOW-THROUGH`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Extend the lifecycle kernel from P5.1's vocabulary-first surface into explicit, packet-bounded fold legality on the remaining current canonical builder transitions, especially settlement-side folds, without yet widening into broad runtime phase-mutation cleanup or src/execution rewiring.

## Allowed files

- work_packets/P5.2-FOLD-LEGALITY-FOLLOW-THROUGH.md
- src/state/lifecycle_manager.py
- src/engine/lifecycle_events.py
- tests/test_architecture_contracts.py
- architects_progress.md
- architects_task.md
- architects_state_index.md

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
- `src/execution/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/state/db.py`
- `src/state/portfolio.py`
- `src/state/chain_reconciliation.py`
- `src/supervisor_api/**`
- `tests/test_db.py`
- `tests/test_runtime_guards.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_replay_time_provenance.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no broad runtime hotspot rewiring
- no dashboard/observability/control-plane widening
- no schema changes
- no cutover or migration claims
- no team launch

## Current blocker state

- no active blocker inside the frozen P5.2 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P5.1 packet frozen
- [x] lifecycle kernel surface installed
- [x] targeted lifecycle-kernel architecture tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.1 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.2 packet frozen
- [ ] touched canonical builder fold legality implemented
- [ ] targeted lifecycle fold-legality architecture tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P5.2 accepted and pushed

## Next required action

1. Implement `P5.2-FOLD-LEGALITY-FOLLOW-THROUGH` within the frozen file boundary.
2. Run targeted tests plus pre-close critic/verifier before acceptance.
