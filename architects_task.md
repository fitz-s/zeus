# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7R freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7R-DELTA-05-RUNTIME-POSITION-CURRENT-BOOTSTRAP`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Resolve DELTA-05 by making the canonical projection substrate (`position_current` and compatible runtime bootstrap path) actually present in current runtime DB reality, without pretending DB-first reads or cutover are already authorized.

## Allowed files

- `work_packets/P7R-DELTA-05-RUNTIME-POSITION-CURRENT-BOOTSTRAP.md`
- `migrations/**`
- `tests/test_architecture_contracts.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/**`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_runtime_guards.py`
- `tests/test_riskguard.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no DB-first cutover yet
- no dual-write widening yet
- no deletion of legacy surfaces
- no team launch

## Current blocker state

- current parity evidence says `position_current` is absent in the local runtime DB reality
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7R packet frozen
- [ ] runtime/bootstrap path can produce `position_current`
- [ ] targeted schema/bootstrap tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P7R accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Implement `P7R-DELTA-05-RUNTIME-POSITION-CURRENT-BOOTSTRAP`.
2. Run targeted tests plus pre-close critic + verifier before any acceptance claim.
3. Do not freeze the next packet until this packet’s post-close gate passes.
