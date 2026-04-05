# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7R2 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7R2-DELTA-05-INIT-SCHEMA-ADDITIVE-CANONICAL-TABLES`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Resolve DELTA-05 by upgrading the runtime bootstrap seam in `src/state/db.py::init_schema()` so current/fresh runtime DBs gain the additive canonical support tables without claiming cutover.

## Allowed files

- `work_packets/P7R2-DELTA-05-INIT-SCHEMA-ADDITIVE-CANONICAL-TABLES.md`
- `src/state/db.py`
- `tests/test_architecture_contracts.py`
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
- `src/observability/**`
- `src/riskguard/**`
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
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
- no dual-write widening yet
- no deletion of legacy surfaces
- no team launch

## Current blocker state

- current parity evidence says `position_current` is absent in current runtime DB reality
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7R2 packet frozen
- [x] init_schema/runtime bootstrap path can produce `position_current` and additive canonical support tables
- [x] targeted schema/bootstrap tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P7R2 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P7R2` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze the next packet until this packet’s post-close gate passes.
