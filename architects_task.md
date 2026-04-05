# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7R4 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7R4-OPEN-POSITION-CANONICAL-BACKFILL`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Seed canonical event+projection state for currently open legacy paper positions so parity no longer reports an empty canonical open side against a non-empty legacy export, without claiming DB-first cutover or deleting legacy surfaces.

## Allowed files

- `work_packets/P7R4-OPEN-POSITION-CANONICAL-BACKFILL.md`
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
- no legacy-surface deletion yet
- no broad migration cleanup
- no team launch

## Current blocker state

- accepted P7R4 boundary still needs the post-close critic + verifier gate before any later P7 freeze may be recorded
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7R4 packet frozen
- [x] bounded canonical backfill path seeds open legacy paper positions through canonical events + projection
- [x] targeted backfill/parity tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P7R4 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P7R4` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze the next packet until P7R4 post-close gate passes.
