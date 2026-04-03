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
- State: `FROZEN / READY TO IMPLEMENT`
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

- [ ] implement reconciliation query-path compatibility in `src/state/chain_reconciliation.py`
- [ ] preserve fail-loud behavior for malformed/non-canonical unexpected states
- [ ] add targeted architecture-contract coverage
- [ ] append durable packet transition to `architects_progress.md` when implementation lands
- [ ] run explicit adversarial review
- [ ] obtain final architect verification
- [ ] commit and push the packet

## Next required action

1. Fix only the reconciliation query-path compatibility blocker.
2. Keep reconciliation caller migration for a later successor packet.
3. Keep cutover and broader state rewiring out of scope.
4. Keep unrelated working-tree dirt out of the packet commit.
