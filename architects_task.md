# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-04 America/Chicago`
- Last updated by: `Codex P7.5 post-close + P7.6 freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `P7.6-M3-RISKGUARD-DB-FIRST`
- State: `FROZEN / READY FOR EXECUTION`
- Execution mode: `SOLO_EXECUTE / NO_TEAM_DEFAULT`
- Current owner: `Architects mainline lead`

## Objective

Make RiskGuard read DB-backed portfolio truth first while keeping any legacy working-state fallback explicit as emergency compatibility only when canonical projection is unavailable, without widening into broader cutover or deletion.

## Allowed files

- `work_packets/P7.6-M3-RISKGUARD-DB-FIRST.md`
- `src/riskguard/riskguard.py`
- `src/state/db.py`
- `tests/test_riskguard.py`
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
- `src/engine/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/state/portfolio.py`
- `src/state/ledger.py`
- `src/state/projection.py`
- `tests/test_runtime_guards.py`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no broad DB-first cutover yet
- no legacy-surface deletion yet
- no status-summary changes
- no team launch

## Current blocker state

- RiskGuard still depends on working-state portfolio reads as primary input after the loader seam has already moved DB-first
- out-of-scope local dirt must remain excluded from packet commits

## Immediate checklist

- [x] P7.6 packet frozen
- [ ] RiskGuard becomes DB-first on the touched seam
- [ ] targeted RiskGuard tests green
- [ ] pre-close critic review passed
- [ ] pre-close verifier review passed
- [ ] P7.6 accepted and pushed
- [ ] post-close third-party critic review passed
- [ ] post-close third-party verifier review passed

## Next required action

1. Implement the bounded RiskGuard DB-first reader seam.
2. Prove any fallback to working-state portfolio inputs stays explicit and only activates when canonical projection is unavailable.
3. Do not widen into broader cutover or deletion work.
