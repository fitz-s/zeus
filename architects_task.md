# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-09 America/Chicago`
- Last updated by: `Codex INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION post-close`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION`
- State: `ACCEPTED_LOCAL / POST_CLOSE_PASSED`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Preserve the accepted truth-repair mainline while integrating the current Architects data-expansion lane, keeping additive collection/scheduling/calibration expansion and rejecting local regressions that would weaken accepted truth behavior.

## Allowed files

- `work_packets/INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `config/cities.json`
- `src/main.py`
- `scripts/etl_tigge_ens.py`
- `src/data/observation_client.py`
- `scripts/backfill_hourly_openmeteo.py`
- `scripts/backfill_wu_daily_all.py`
- `scripts/etl_tigge_direct_calibration.py`
- `scripts/migrate_rainstorm_full.py`
- `src/data/wu_daily_collector.py`
- `tests/test_etl_recalibrate_chain.py`
- `tests/test_runtime_guards.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/supervisor_api/**`
- `migrations/**`
- `src/state/**`
- `src/engine/lifecycle_events.py`
- `src/execution/**`
- `tests/test_architecture_contracts.py`
- `tests/test_pnl_flow_and_audit.py`
- `tests/test_center_buy_diagnosis.py`
- `tests/test_center_buy_repair.py`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no fresh truth-path rewrites in `src/state/**`
- no broad reporting/dashboard work
- no risk/status/operator summary rewrites
- no schema redesign
- no new strategy behavior work
- no team runtime launch

## Current blocker state

- live Architects data expansion currently exists only as a dirty local lane mixed with some regressions against accepted truth-repair files
- the merge must preserve expansion files and keep truth-repair files authoritative
- packet must stay off fresh truth rewrites and focus on bounded integration only

## Immediate checklist

- [x] `INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION` frozen
- [x] data-expansion files ported onto the accepted truth tip
- [x] `src/main.py` integrated without losing truth-safe subprocess behavior
- [x] `tests/test_runtime_guards.py` merged without dropping truth economic-close assertions
- [x] targeted ETL/runtime tests pass
- [x] follow-up data-expansion gaps recorded explicitly
- [x] post-close critic review passed
- [x] post-close verifier review passed

## Next required action

1. Hand the explicit data-expansion follow-up gaps to the responsible data-lane owner instead of widening this packet.
2. Keep truth-owned files on the accepted repair version unless a new packet explicitly authorizes change.
3. Do not freeze a new packet on this branch until the follow-up gaps are explicitly acknowledged or superseded.
