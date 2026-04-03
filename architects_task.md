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

- Packet: `P2R-EXECUTION-TRUTH-REPAIR`
- State: `FROZEN / REPAIR IN PROGRESS`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Repair the bottom-layer execution-truth contradictions that invalidate the prior P2 closure claim.

## Allowed files

- `work_packets/P2R-EXECUTION-TRUTH-REPAIR.md`
- `src/contracts/semantic_types.py`
- `src/state/portfolio.py`
- `src/engine/cycle_runtime.py`
- `src/state/chain_reconciliation.py`
- `src/engine/lifecycle_events.py`
- `src/execution/exit_lifecycle.py`
- `src/execution/harvester.py`
- `src/state/db.py`
- `tests/test_runtime_guards.py`
- `tests/test_live_safety_invariants.py`
- `tests/test_architecture_contracts.py`
- `tests/test_db.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all non-allowed files
- `AGENTS.md`
- `src/riskguard/**`
- `src/control/**`
- `src/supervisor_api/**`
- `migrations/**`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no P3 work
- no cutover or migration claims
- no schema migration
- no team launch

## Current blocker state

- the active blocker is semantic contradiction between prior P2-closed control claims and current runtime truth
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [ ] make `pending_exit` bottom-layer authoritative lifecycle truth
- [ ] remove reconciliation flattening / holding-like lifecycle inventions
- [ ] seal economically_closed/admin_closed/quarantined open/exposure/runtime leaks
- [ ] disposition additional low-level critic findings that belong in the same repair packet
- [ ] run adversarial review before acceptance

## Next required action

1. Land the repair in the allowed files only.
2. Verify with targeted tests and explicit adversarial review.
3. Keep out-of-scope dirt excluded from any commit.
