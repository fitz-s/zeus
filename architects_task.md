# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P4.3 freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P4.3-EXECUTION-FACTS`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Install the first durable `execution_fact` writer path for current entry/exit order lifecycle events without widening into `outcome_fact`, analytics work, or schema changes.

## Allowed files

- `work_packets/P4.3-EXECUTION-FACTS.md`
- `src/state/db.py`
- `src/engine/cycle_runtime.py`
- `src/execution/exit_lifecycle.py`
- `tests/test_db.py`
- `tests/test_runtime_guards.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all repo surfaces outside the frozen P4.3 packet boundary
- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
- `src/control/**`
- `src/execution/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/supervisor_api/**`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_replay_time_provenance.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no `outcome_fact` writer work
- no schema changes
- no cutover
- no team launch

## Current blocker state

- repo-wide `python3 scripts/check_work_packets.py` currently fails on unrelated math packet markdown files outside the frozen P4.3 boundary
- external/internal small pre-close review attempts via $ask are currently timing out
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P4.1 post-close third-party critic review passed
- [x] P4.1 post-close third-party verifier review passed
- [x] P4.2 packet is frozen
- [x] P4.2 runtime/state seam implemented
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P4.2 accepted and pushed
- [x] external third-party review found prior control/evidence discipline failure and it was repaired
- [x] renewed post-close critic review passed
- [x] renewed post-close verifier review passed
- [x] P4.3 packet is frozen
- [x] P4.3 runtime/state seam implemented
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P4.3 accepted and pushed

## Next required action

1. Preserve the implemented P4.3 slice; do not close yet.
2. Resolve or route around the repo-wide work-packet grammar blocker and complete review evidence before acceptance.
