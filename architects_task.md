# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P5.3A freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P5.3A-EXIT-LIFECYCLE-PHASE-HOTSPOT`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Remove the direct lifecycle phase-mutation hot spot in `src/execution/exit_lifecycle.py` by routing the touched pending-exit enter/release semantics through the lifecycle kernel, without yet widening into other phase hot spots.

## Allowed files

- work_packets/P5.3A-EXIT-LIFECYCLE-PHASE-HOTSPOT.md
- src/state/lifecycle_manager.py
- src/execution/exit_lifecycle.py
- tests/test_runtime_guards.py
- tests/test_live_safety_invariants.py
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
- `src/observability/**`
- `src/riskguard/**`
- `src/state/db.py`
- `src/state/portfolio.py`
- `src/state/chain_reconciliation.py`
- `src/engine/**`
- `src/supervisor_api/**`
- `tests/test_architecture_contracts.py`
- `tests/test_db.py`
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

- no active blocker inside the frozen P5.3A boundary
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
- [x] touched canonical builder fold legality implemented
- [x] targeted lifecycle fold-legality architecture tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.2 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3A packet frozen
- [ ] touched exit-lifecycle hotspot routed through lifecycle kernel
- [ ] targeted exit-lifecycle hotspot tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P5.3A accepted and pushed

## Next required action

1. Implement `P5.3A-EXIT-LIFECYCLE-PHASE-HOTSPOT` within the frozen file boundary.
2. Run targeted tests plus pre-close critic/verifier before acceptance.
