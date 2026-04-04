# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P4.2 post-close repair pass`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `none`
- State: `P4.2 POST-CLOSE GATE FAILED / RE-REVIEW PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

No live packet. Accepted P4.2 remains blocked from advancement because the prior post-close gate failed on stale control-surface truth and incomplete verifier evidence; renewed review must complete before P4.3 can freeze.

## Allowed files

- post-close review evidence surfaces for accepted P4.2
- `.omx/artifacts/user-p4-2-postclose-review-20260404T010500Z.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- all repo implementation/runtime/schema surfaces until renewed review passes and the next packet is frozen
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

- no `P4.3-EXECUTION-FACTS` freeze before renewed post-close review passes
- no schema changes
- no cutover
- no team launch

## Current blocker state

- accepted P4.2 boundary itself has no new code blocker in the reported review
- advancement is blocked on stale control-surface truth and incomplete verifier evidence
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P4.1 post-close third-party critic review passed
- [x] P4.1 post-close third-party verifier review passed
- [x] P4.2 packet is frozen
- [x] P4.2 runtime/state seam implemented
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P4.2 accepted and pushed
- [x] external third-party review found blocker-level control/evidence discipline failures
- [ ] renewed post-close critic review passed
- [ ] renewed post-close verifier review passed

## Next required action

1. Keep `P4.3-EXECUTION-FACTS` unfrozen.
2. Complete a renewed post-close verifier/review cycle on synchronized control surfaces before any next-packet advancement.
