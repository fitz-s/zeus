# architects_task.md

Purpose:
- active execution control surface only
- exactly one live packet at a time

Metadata:
- Last updated: `2026-04-08 America/Chicago`
- Last updated by: `Codex VERIFY-ETL-RECALIBRATE-CONTAMINATION freeze`
- Authority scope: `live packet control only`

Do not use this file for:
- broad history
- repeated rationale
- test-output dumps
- micro-event notes

## Current active packet

- Packet: `VERIFY-ETL-RECALIBRATE-CONTAMINATION`
- State: `FROZEN / IMPLEMENTATION_READY`
- Execution mode: `SOLO_LEAD / BOUNDED_SUBAGENTS_ALLOWED`
- Current owner: `Architects mainline lead`

## Objective

Prove the shared ETL/recalibrate chain stays on shared truth surfaces and repair the discovered TIGGE multi-step collapse before advancing into later leftover families.

## Allowed files

- `work_packets/VERIFY-ETL-RECALIBRATE-CONTAMINATION.md`
- `architects_progress.md`
- `architects_task.md`
- `architects_state_index.md`
- `src/main.py`
- `scripts/etl_tigge_calibration.py`
- `tests/test_observation_instants_etl.py`
- `tests/test_run_replay_cli.py`
- `tests/test_etl_recalibrate_chain.py`

## Forbidden files

- `AGENTS.md`
- `docs/governance/**`
- `docs/architecture/**`
- `architecture/**`
- `src/control/**`
- `src/execution/**`
- `src/supervisor_api/**`
- `src/observability/**`
- `src/riskguard/**`
- `src/state/db.py`
- `src/state/portfolio.py`
- `migrations/**`
- `tests/test_healthcheck.py`
- `.github/workflows/**`
- `.claude/CLAUDE.md`
- `zeus_final_tribunal_overlay/**`

## Non-goals

- no trade/lifecycle/risk/status truth repair
- no broad 20-script migration cleanup
- no schema redesign or replay-engine contract rewrite
- no daemon cutover or scheduler timing claim
- no team runtime launch

## Current blocker state

- session leftovers still rank ETL/recalibrate contamination as the highest-risk open family
- fresh repo inspection already found one concrete blocker inside that family: `etl_tigge_calibration.py` only preserves the last step file per date directory and stamps `lead_hours = 24.0`
- representative ETL scripts appear migrated to `get_shared_connection()`, but the weekly subprocess chain still lacks packet-bounded proof
- packet must stay off trade/lifecycle/risk/status surfaces

## Immediate checklist

- [x] `VERIFY-ETL-RECALIBRATE-CONTAMINATION` frozen
- [x] ETL/recalibrate code-review/test map captured for the packet
- [x] shared-binding/import proof captured in tests
- [x] TIGGE multi-step truth repaired
- [x] targeted tests pass
- [x] pre-close critic review passed
- [x] pre-close verifier review passed
- [x] packet accepted locally
- [x] post-close third-party critic review passed
- [x] post-close third-party verifier review passed

## Next required action

1. Cherry-pick accepted commit `0c9a348` onto `Architects` cleanly when ready.
2. Update the live branch control surfaces only after transport is complete.
3. Do not widen into trade/lifecycle/risk/status truth work or broader ETL cleanup without a new packet.
