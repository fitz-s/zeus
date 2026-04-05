# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7.2 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7.2-M2-PARITY-REPORTING`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Replace the placeholder replay/parity reporting with a truthful parity surface that compares canonical DB projection against surviving legacy exports on the touched migration seams, without cutover or deletion.

## Allowed files

- `work_packets/P7.2-M2-PARITY-REPORTING.md`
- `scripts/replay_parity.py`
- `tests/test_architecture_contracts.py`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `migrations/**`
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
- no deletion/demotion of runtime surfaces here
- no team launch

## Current blocker state

- no blocker inside the frozen P7.2 boundary
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.2 packet frozen
- [ ] placeholder parity surface replaced with truthful compare output
- [ ] targeted parity/reporting tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P7.2 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Implement `P7.2-M2-PARITY-REPORTING`.
2. Run targeted tests plus pre-close critic + verifier before any acceptance claim.
3. Do not freeze the next packet until P7.2 post-close gate passes.
