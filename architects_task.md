# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7R3 post-close + P7R4 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7R4-OPEN-POSITION-CANONICAL-BACKFILL`
- State: `FROZEN / READY FOR EXECUTION`
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

- parity on current runtime truth still reports canonical open side empty while `positions-paper.json` reports 12 open `opening_inertia` positions
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7R4 packet frozen
- [ ] bounded canonical backfill path seeds open legacy paper positions through canonical events + projection
- [ ] targeted backfill/parity tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P7R4 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Implement the bounded canonical backfill path for currently open legacy paper positions.
2. Prove the backfill path advances parity beyond the current empty-canonical-open-side mismatch on the touched seam.
3. Do not widen into DB-first cutover or legacy-surface deletion.
