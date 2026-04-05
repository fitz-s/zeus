# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-05 America/Chicago`
- Last updated by: `Codex P7R3 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Resolve the legacy `position_events` schema collision that blocks append-first canonical seeding, so later canonical backfill can land honestly without bypassing event authority.

## Allowed files

- `work_packets/P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR.md`
- `src/state/db.py`
- `src/state/ledger.py`
- `migrations/**`
- `tests/test_architecture_contracts.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/projection.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_runtime_guards.py`
- `tests/test_riskguard.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no DB-first cutover yet
- no legacy-surface deletion yet
- no broad migration cleanup
- no team launch

## Current blocker state

- append-first canonical seeding had been blocked by the legacy `position_events` schema shape
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7R3 packet frozen
- [x] event-authority collision repaired on the touched seam
- [x] targeted schema/bootstrap tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P7R3 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P7R3` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze the next packet until P7R3 post-close gate passes.
