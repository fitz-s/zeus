# P3 Usage-Path Residual Guards Work Log

Date: 2026-04-25
Branch: `midstream_remediation`
Task: P3 usage-path residual guards after 4.5.A closeout

Changed files:

- `src/engine/replay.py`
- `tests/test_semantic_linter.py`
- `tests/test_phase8_shadow_code.py`
- `tests/test_backtest_settlement_value_outcome.py`
- `tests/test_replay_time_provenance.py`
- `architecture/test_topology.yaml`
- `architecture/docs_registry.yaml`
- `architecture/topology.yaml`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-25_p3_usage_path_residual_guards/plan.md`
- `docs/operations/task_2026-04-25_p3_usage_path_residual_guards/work_log.md`
- `docs/operations/task_2026-04-25_p3_usage_path_residual_guards/scope.yaml`
- `docs/operations/task_2026-04-25_p3_usage_path_residual_guards/receipt.json`

Summary: replay settlement reads now pin the settlement metric axis; canonical
hourly-observations ban proof is recorded; operations routing now points at
this residual repair packet.

Verification:

- `python3 -m py_compile src/engine/replay.py tests/test_semantic_linter.py tests/test_phase8_shadow_code.py tests/test_backtest_settlement_value_outcome.py tests/test_replay_time_provenance.py`
- `python3 scripts/semantic_linter.py --check src/engine/replay.py src/calibration src/engine scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py scripts/validate_dynamic_alpha.py scripts/etl_forecast_skill_from_forecasts.py scripts/etl_historical_forecasts.py`
- `pytest -q tests/test_semantic_linter.py tests/test_phase8_shadow_code.py tests/test_backtest_settlement_value_outcome.py tests/test_replay_time_provenance.py tests/test_run_replay_cli.py` -> 89 passed
- `python3 scripts/audit_replay_fidelity.py`
- `python3 scripts/topology_doctor.py --tests --json`
- `python3 scripts/topology_doctor.py --scripts --json`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files <packet tests>`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_p3_usage_path_residual_guards/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `git diff --check -- <packet files>`

Next: critic/reviewer pass, then commit and push this residual repair before
opening P2 4.4.A as a dedicated planning-locked writer-history packet.

## 2026-04-25

- Reread root `AGENTS.md`, `workspace_map.md`, scoped operations/source/test
  routers, current fact surfaces, POST_AUDIT 4.4/4.5 sections, and replay
  semantic boot surfaces after compaction.
- Reviewed scout result for POST_AUDIT 4.5.C: no remaining active canonical
  bare `hourly_observations` readers in `src/calibration/**`, `src/engine/**`,
  or canonical rebuild scripts; remaining code references are compatibility,
  schema/evidence adapter, migration, tests, or script-name mentions.
- Reviewed architect result: P2 4.4.A is the next high-value mainline packet,
  but the replay H3 residual must be repaired first because it contradicts
  the just-closed settlement metric-read evidence.
- Opened this narrow repair packet before changing replay code.
- Implemented replay metric predicates:
  - `ReplayContext.get_settlement()` now accepts a `temperature_metric` kwarg
    and filters settlements by it.
  - `run_replay()` filters settlement rows by the public
    `temperature_metric` argument before replaying them.
  - `run_trade_history_audit()` reads the position's stored
    `position_current.temperature_metric` and uses it for WU comparison.
  - `run_wu_settlement_sweep()` remains a HIGH-only compatibility lane and
    filters settlements to `temperature_metric='high'`.
- Updated stale replay tests so fixtures carry explicit HIGH/LOW settlement
  identity and current `market_events` preflight evidence.
- Added a LOW trade-history regression with both HIGH and LOW settlement rows
  for the same `(city, target_date)`, proving the audit reads the stored
  `position_current.temperature_metric` row.
- Added semantic-linter proof that `src/engine/replay.py` has no H3 settlement
  metric-read violations and that canonical calibration/engine/rebuild-v2 paths
  do not read bare `hourly_observations`.
- Addressed reviewer feedback by clarifying warning/test prose: WU sweep is
  HIGH-only, while trade-history ignores the public replay kwarg but matches
  settlements using stored position metric identity.
- Re-ran reviewer-requested targeted evidence after the fix:
  - `pytest -q tests/test_backtest_settlement_value_outcome.py::test_trade_history_audit_uses_position_metric_for_settlement_match tests/test_phase8_shadow_code.py::TestRBURunReplayModeMetricWarning tests/test_semantic_linter.py::test_h3_replay_source_is_clean tests/test_semantic_linter.py::test_canonical_paths_do_not_read_bare_hourly_observations tests/test_replay_time_provenance.py` -> 10 passed via verifier.
- Runtime heartbeat/status JSON regenerated during verification and was split
  into separate runtime snapshot commits so this packet commit remains scoped.
- Verification:
  - `python3 -m py_compile src/engine/replay.py tests/test_semantic_linter.py tests/test_phase8_shadow_code.py tests/test_backtest_settlement_value_outcome.py tests/test_replay_time_provenance.py`
  - `python3 scripts/semantic_linter.py --check src/engine/replay.py src/calibration src/engine scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py scripts/validate_dynamic_alpha.py scripts/etl_forecast_skill_from_forecasts.py scripts/etl_historical_forecasts.py`
  - `pytest -q tests/test_semantic_linter.py tests/test_phase8_shadow_code.py tests/test_backtest_settlement_value_outcome.py tests/test_replay_time_provenance.py tests/test_run_replay_cli.py` -> 89 passed
  - `python3 scripts/audit_replay_fidelity.py` -> exited 0 with diagnostic coverage output
  - `python3 scripts/topology_doctor.py --tests --json` -> ok
  - `python3 scripts/topology_doctor.py --scripts --json` -> ok
  - `python3 scripts/topology_doctor.py --freshness-metadata --changed-files <packet tests>` -> ok
  - `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_p3_usage_path_residual_guards/plan.md` -> ok
  - `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>` -> ok
  - `git diff --check -- <packet files>` -> ok
