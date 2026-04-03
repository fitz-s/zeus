# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-03 America/Chicago`
- Last updated by: `Codex mainline coordination pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P1.7G-CHAIN-QUARANTINE-STRATEGY-RESOLUTION`
- State: `BLOCKED / HUMAN DECISION REQUIRED`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Freeze the unresolved chain-quarantine attribution contradiction and record the exact human decision required before P1 can close honestly.

## Allowed files

- `work_packets/P1.7G-CHAIN-QUARANTINE-STRATEGY-RESOLUTION.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `AGENTS.md`
- `migrations/**`
- `src/execution/**`
- `src/riskguard/**`
- `src/control/**`
- `src/supervisor_api/**`
- `src/state/db.py`
- `src/state/chronicler.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `src/state/chain_reconciliation.py`
- `architecture/**`
- `docs/governance/**`
- `docs/architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime cutover surfaces
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no code changes
- no schema changes
- no caller migration
- no DB-first reads
- no cutover
- no team launch

## Current blocker state

- active human decision blocker:
  - chain-only quarantined positions have no safe repo-authorized `strategy_key` source
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] append durable contradiction and stop-boundary transition to `architects_progress.md`
- [x] freeze the blocker packet
- [x] commit and push the blocker packet
- [ ] await human decision on chain-quarantine strategy attribution

## Next required action

1. Stop autonomous execution here.
2. Await human decision on chain-only quarantine attribution.
3. After that decision, freeze a superseding packet.
4. Keep out-of-scope dirt excluded from any future commit.
