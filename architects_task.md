# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7R7 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7R7-RUNTIME-TRACKER-COMPATIBILITY-NORMALIZATION`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Normalize the live runtime `strategy_tracker-paper.json` compatibility metadata so the persisted file matches the compatibility-only law already enforced by code/tests, without widening into M4 retirement or broader runtime redesign.

## Allowed files

- `work_packets/P7R7-RUNTIME-TRACKER-COMPATIBILITY-NORMALIZATION.md`
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

- live runtime `state/strategy_tracker-paper.json` still showed stale `tracker_role = attribution_surface` until explicit normalization ran
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7R7 packet frozen
- [ ] live runtime tracker file normalized to compatibility-only metadata
- [ ] packet-bounded evidence recorded
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P7R7 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Record the runtime before/after metadata normalization evidence honestly.
2. Keep this repair bounded to runtime tracker-file normalization only.
3. Do not widen into M4 retirement/delete work.
