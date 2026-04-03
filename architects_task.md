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

- Packet: `P1.7A-RECONCILIATION-LIFECYCLE-EVENT-COMPAT`
- State: `APPROVED / READY TO COMMIT`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Make the reconciliation lifecycle-event helper in `src/state/db.py` degrade cleanly on canonically bootstrapped databases so the remaining reconciliation blocker inventory can proceed without the current raw legacy-event-helper crash.

## Allowed files

- `work_packets/P1.7A-RECONCILIATION-LIFECYCLE-EVENT-COMPAT.md`
- `src/state/db.py`
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

- [x] implement reconciliation lifecycle-event compatibility in `src/state/db.py`
- [x] preserve legacy-schema behavior for the touched helper
- [x] add targeted architecture-contract coverage
- [x] append durable packet transition to `architects_progress.md` when implementation lands
- [x] run explicit adversarial review
- [x] obtain final architect verification
- [ ] commit and push the packet

## Next required action

1. Commit and push this accepted reconciliation lifecycle-event blocker packet without mixing unrelated working-tree dirt.
2. Freeze the successor reconciliation query-path blocker packet after push.
3. Keep cutover and broader state rewiring out of scope.
4. Keep reconciliation caller migration separate from this helper-compat packet.
