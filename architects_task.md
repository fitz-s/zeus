# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7.5 close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7.5-M3-LOAD-PORTFOLIO-DB-FIRST`
- State: `ACCEPTED AND PUSHED / POST-CLOSE GATE PENDING`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Make `load_portfolio()` read canonical `position_current` first while keeping JSON fallback explicit as emergency compatibility only when canonical projection is unavailable, without deleting legacy surfaces or widening into broader cutover.

## Allowed files

- `work_packets/P7.5-M3-LOAD-PORTFOLIO-DB-FIRST.md`
- `src/state/db.py`
- `src/state/portfolio.py`
- `tests/test_runtime_guards.py`
- `tests/test_pnl_flow_and_audit.py`
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
- `src/state/ledger.py`
- `src/state/projection.py`
- `tests/test_architecture_contracts.py`
- `tests/test_riskguard.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no broad DB-first cutover yet
- no legacy-surface deletion yet
- no riskguard DB-first shift yet
- no team launch

## Current blocker state

- `load_portfolio()` still treats legacy JSON as primary truth even though current paper open-position parity is now available through canonical projection
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.5 packet frozen
- [x] `load_portfolio()` becomes DB-first on the touched seam
- [x] targeted DB-first loader tests green
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] P7.5 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Finish the post-close critic + verifier on the accepted `P7.5` boundary.
2. Keep the slim control surfaces honest while the post-close gate is pending.
3. Do not freeze the next packet until P7.5 post-close gate passes.
