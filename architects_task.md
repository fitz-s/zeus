# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-05 America/Chicago`
- Last updated by: `Codex P7.3 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7.3-M1-OPEN-POSITION-CANONICAL-BACKFILL`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Seed canonical event+projection state for currently open legacy positions so parity no longer reports an empty canonical open side against a non-empty legacy export, without claiming DB-first cutover or deleting legacy surfaces.

## Allowed files

- `work_packets/P7.3-M1-OPEN-POSITION-CANONICAL-BACKFILL.md`
- `src/state/db.py`
- `src/engine/lifecycle_events.py`
- `scripts/**`
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
- no deletion of legacy surfaces
- no broad migration cleanup
- no team launch

## Current blocker state

- parity now reports an empty canonical open side against non-empty legacy paper positions
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.3 packet frozen
- [ ] currently open legacy positions gain canonical event+projection representation on the touched backfill path
- [ ] targeted backfill/parity tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P7.3 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Implement `P7.3-M1-OPEN-POSITION-CANONICAL-BACKFILL`.
2. Run targeted tests plus pre-close critic + verifier before any acceptance claim.
3. Do not freeze the next packet until P7.3 post-close gate passes.
