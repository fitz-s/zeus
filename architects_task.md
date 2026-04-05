# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7.7 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7.7-M3-STRATEGY-TRACKER-COMPATIBILITY-HARDENING`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Bring the persisted strategy-tracker compatibility surface into explicit alignment with repo law by hardening tracker metadata and compatibility semantics, without deleting the tracker or widening into M4 retirement work.

## Allowed files

- `work_packets/P7.7-M3-STRATEGY-TRACKER-COMPATIBILITY-HARDENING.md`
- `src/state/strategy_tracker.py`
- `scripts/rebuild_strategy_tracker_current_regime.py`
- `tests/test_strategy_tracker_regime.py`
- `tests/test_truth_layer.py`
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
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `tests/test_runtime_guards.py`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no broad DB-first cutover yet
- no legacy-surface deletion yet
- no M4 retirement/delete work
- no team launch

## Current blocker state

- current runtime tracker metadata still advertises `tracker_role = attribution_surface`, which contradicts compatibility-only repo law
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.7 packet frozen
- [x] tracker compatibility metadata aligns with compatibility-only law
- [x] targeted tracker compatibility tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P7.7 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P7.7` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze the next packet until P7.7 post-close gate passes.
