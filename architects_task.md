# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P6.3 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P6.3-STRATEGY-TRACKER-DELETION-PATH`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Demote or remove the remaining `strategy_tracker` authority role by shifting surviving consumer dependence onto the already-installed DB-backed operator surfaces, while preserving any still-needed tracker output only as explicit non-authority compatibility output.

## Allowed files

- `work_packets/P6.3-STRATEGY-TRACKER-DELETION-PATH.md`
- `src/state/strategy_tracker.py`
- `src/observability/status_summary.py`
- `scripts/healthcheck.py`
- `tests/test_strategy_tracker_regime.py`
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
- `src/control/**`
- `src/riskguard/**`
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

- no control-plane durable-write changes
- no schema changes
- no unrelated operator field widening
- no broader P7 migration/cutover work yet
- no team launch

## Current blocker state

- no blocker inside the frozen P6.3 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P6.3 packet frozen
- [x] remaining strategy-tracker authority dependence removed or demoted on touched surfaces
- [x] targeted strategy-tracker demotion tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P6.3 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P6.3` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze the next packet until P6.3 post-close gate passes.
