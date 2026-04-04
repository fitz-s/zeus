# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P4.4 freeze pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Install the first durable `outcome_fact` writer path for economically completed positions without widening into analytics-query work or settlement-law redesign.

## Allowed files

- `work_packets/P4.4-OUTCOME-FACTS.md`
- `src/state/db.py`
- `src/execution/harvester.py`
- `tests/test_db.py`
- `tests/test_runtime_guards.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all repo surfaces outside the frozen P4.4 packet boundary
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

- no analytics-query work
- no runtime code change
- no schema changes
- no cutover
- no team launch

## Current blocker state

- no active blocker at freeze time
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P4.3 packet remains valid under the accepted discrete-settlement authority amendment
- [x] P4.3 runtime/state seam implemented
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P4.3 accepted and pushed
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed
- [x] P4.4 packet is frozen
- [ ] P4.4 runtime/state seam implemented
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P4.4 accepted and pushed

## Next required action

1. Implement `P4.4-OUTCOME-FACTS` within the frozen file boundary.
2. Run targeted tests plus pre-close critic/verifier before acceptance.
