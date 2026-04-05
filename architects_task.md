# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P6.2 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P6.2-CONTROL-PLANE-DURABLE-OVERRIDE-WRITES`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Move the active control-plane command path off memory-only `_control_state` for the durable override subset by writing operator commands into `control_overrides` while preserving ingress-only `control_plane.json` and leaving `lifecycle_commands` for later.

## Allowed files

- `work_packets/P6.2-CONTROL-PLANE-DURABLE-OVERRIDE-WRITES.md`
- `src/control/control_plane.py`
- `src/state/db.py`
- `tests/test_pnl_flow_and_audit.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/observability/**`
- `src/riskguard/riskguard.py`
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `tests/test_architecture_contracts.py`
- `tests/test_runtime_guards.py`
- `tests/test_riskguard.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no `lifecycle_commands` widening yet
- no `control_plane.json` deletion
- no `strategy_tracker` deletion or demotion yet
- no schema changes
- no unrelated operator field widening
- no team launch

## Current blocker state

- no blocker inside the frozen P6.2 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P6.2 packet frozen
- [x] durable override subset written into `control_overrides`
- [x] restart-survival behavior proven on the durable command path
- [x] targeted control-plane durability tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P6.2 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P6.2` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze the next packet until P6.2 post-close gate passes.
