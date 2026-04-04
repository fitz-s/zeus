# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P5.1 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P5.1-LIFECYCLE-PHASE-KERNEL`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Install the first authoritative lifecycle-phase kernel by freezing the finite P5 phase vocabulary and fold-legality surface behind a dedicated lifecycle manager, without yet rewiring the broader runtime mutation hot spots.

## Allowed files

- work_packets/P5.1-LIFECYCLE-PHASE-KERNEL.md
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

- no active blocker inside the frozen P5.1 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P5.1 packet frozen
- [ ] lifecycle kernel surface installed
- [ ] targeted lifecycle-kernel architecture tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P5.1 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Install the lifecycle kernel surface inside the frozen boundary.
2. Prove targeted lifecycle-kernel mapping/fold legality with architecture tests.
3. Do not close the packet before pre-close critic + verifier finish.
