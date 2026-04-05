# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7.1 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7.1-M0-SCHEMA-ADD-ONLY`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Start P7 with an additive-only schema prep slice for later migration work, with no runtime behavior change or cutover claims.

## Allowed files

- `work_packets/P7.1-M0-SCHEMA-ADD-ONLY.md`
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
- `src/control/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_runtime_guards.py`
- `tests/test_riskguard.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no runtime behavior change
- no dual-write yet
- no parity/cutover/delete work yet
- no destructive migration action
- no team launch

## Current blocker state

- no blocker inside the frozen P7.1 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.1 packet frozen
- [ ] additive-only schema prep installed if needed
- [ ] targeted schema smoke tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P7.1 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Implement `P7.1-M0-SCHEMA-ADD-ONLY`.
2. Run targeted tests plus pre-close critic + verifier before any acceptance claim.
3. Do not freeze the next packet until P7.1 post-close gate passes.
