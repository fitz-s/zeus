# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7.7 post-close boundary`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `NO LIVE PACKET / AWAITING NEXT LAWFUL FREEZE`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. Hold at the post-P7.7 boundary until the next bounded non-destructive packet is explicitly justified.

## Allowed files

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
- no M4 retirement/delete freeze without a new explicit packet
- no team launch

## Current blocker state

- no later bounded non-destructive packet has been frozen yet
- obvious next work trends toward M4 retirement/delete territory, which is not auto-authorized by momentum alone
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.7 packet frozen
- [x] tracker compatibility metadata aligns with compatibility-only law
- [x] targeted tracker compatibility tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P7.7 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Hold at the post-P7.7 boundary until a new bounded packet is explicitly justified.
2. Do not invent a fake next freeze just to preserve momentum.
3. Treat destructive/retirement transitions as out of the current autonomous stop boundary.
