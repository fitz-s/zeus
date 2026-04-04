# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P5.3C acceptance pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P5.3C-RECONCILIATION-LIFECYCLE-HOTSPOT`
- State: `ACCEPTED / PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Remove the direct lifecycle mutation hot spots on the touched reconciliation seam by routing the pending-fill rescue and chain-only quarantine transitions through lifecycle-kernel helpers, without widening into portfolio cleanup or broader reconciliation redesign.

## Allowed files

- work_packets/P5.3C-RECONCILIATION-LIFECYCLE-HOTSPOT.md
- src/state/lifecycle_manager.py
- src/state/chain_reconciliation.py
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
- `src/engine/**`
- `src/execution/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/state/db.py`
- `src/state/portfolio.py`
- `src/supervisor_api/**`
- `tests/test_architecture_contracts.py`
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

- no blocker on the accepted P5.3C boundary itself
- the post-close third-party critic/verifier gate is still pending
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
- [x] touched exit-lifecycle hotspot routed through lifecycle kernel
- [x] targeted exit-lifecycle hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3A accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3B packet frozen
- [x] touched day0 hotspot routed through lifecycle kernel
- [x] targeted day0 hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3B accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P5.3C packet frozen
- [x] touched reconciliation hotspot routed through lifecycle kernel
- [x] targeted reconciliation hotspot tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P5.3C accepted and pushed

## Next required action

1. Run the post-close third-party critic + verifier on accepted `P5.3C-RECONCILIATION-LIFECYCLE-HOTSPOT`.
2. Freeze the next P5 packet only after that gate passes.
