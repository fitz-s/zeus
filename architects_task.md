# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Repair the riskguard loss authority surface so `daily_loss` means trailing 24h equity loss and `weekly_loss` means trailing 7d equity loss, with explicit degraded-truth metadata instead of silent fallback to all-time or session baselines.

## Allowed files

- `work_packets/RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/riskguard/riskguard.py`
- `tests/test_riskguard.py`
- `tests/test_pnl_flow_and_audit.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/state/**`
- `src/observability/status_summary.py`
- `src/control/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/execution/**`
- `src/engine/**`
- `tests/test_architecture_contracts.py`
- `tests/test_center_buy_diagnosis.py`
- `tests/test_center_buy_repair.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no broad settlement-authority unification yet
- no portfolio fallback rewrite yet
- no reporting/dashboard/schema work
- no schema redesign
- no data-expansion follow-up work
- no team runtime launch

## Current blocker state

- fresh evidence shows `daily_loss` currently equals all-time loss instead of trailing 24h loss
- many historical `risk_state` rows are internally inconsistent, so reference-row trust must be explicit
- this packet must stay bounded to loss authority and expose deeper truth drift rather than quietly widening into portfolio/settlement fixes

## Immediate checklist

- [x] `RISK-TRUTH-01-TRAILING-LOSS-AUTHORITY` frozen
- [ ] trailing 24h / 7d reference-row helper implemented
- [ ] baseline-driven loss math removed from riskguard
- [ ] exact truth-status / audit-field contract implemented
- [ ] targeted loss-authority tests pass
- [ ] deeper truth drift exposed by the packet recorded explicitly

## Next required action

1. Implement the bounded trailing-loss helper and semantics in `src/riskguard/riskguard.py`.
2. Replace and expand targeted tests in `tests/test_riskguard.py` and `tests/test_pnl_flow_and_audit.py`.
3. If consumer mismatch forces `src/state/**` or `src/observability/status_summary.py`, stop and freeze the next packet instead of widening silently.
