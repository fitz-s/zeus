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

- Packet: `P1.7B-RECONCILIATION-QUERY-COMPAT`
- State: `APPROVED / READY TO COMMIT`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Make the reconciliation pending-fill query path tolerate canonically bootstrapped databases so the later reconciliation migration packet can proceed without the current raw legacy-column failure.

## Allowed files

- `work_packets/P1.7B-RECONCILIATION-QUERY-COMPAT.md`
- `src/state/chain_reconciliation.py`
- `tests/test_architecture_contracts.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `AGENTS.md`
- `migrations/**`
- `src/engine/**`
- `src/execution/**`
- `src/riskguard/**`
- `src/control/**`
- `src/supervisor_api/**`
- `src/state/db.py`
- `src/state/chronicler.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `architecture/**`
- `docs/governance/**`
- `docs/architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- runtime cutover surfaces
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no reconciliation caller migration
- no broader dual-write in caller code
- no DB-first reads
- no exit-path migration
- no Day0/K3 work
- no team launch

## Current blocker state

- no active technical blocker inside packet scope
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] implement reconciliation query-path compatibility in `src/state/chain_reconciliation.py`
- [x] preserve fail-loud behavior for malformed/non-canonical unexpected states
- [x] add targeted architecture-contract coverage
- [x] append durable packet transition to `architects_progress.md` when implementation lands
- [x] run explicit adversarial review
- [x] obtain final architect verification
- [ ] commit and push the packet

## Next required action

1. Commit and push this accepted reconciliation query blocker packet without mixing unrelated working-tree dirt.
2. Freeze the successor reconciliation builder packet after push.
3. Keep cutover and broader state rewiring out of scope.
4. Keep reconciliation caller migration separate from this query-compat packet.
