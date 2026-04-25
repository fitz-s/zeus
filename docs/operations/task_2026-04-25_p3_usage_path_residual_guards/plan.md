# P3 Usage-Path Residual Guards Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: in progress

## Background

P3 4.5.A closed the named script-side settlement metric-read linter boundary,
but the follow-up source-wide check exposed a remaining replay settlement read
without a `temperature_metric` predicate. Replay is diagnostic, not promotion
authority, but it still must preserve HIGH/LOW settlement identity; otherwise a
future LOW settlement row can silently match a HIGH replay subject on the same
`(city, target_date)`.

The same phase-entry review found POST_AUDIT 4.5.C largely closed by the prior
legacy-hourly evidence-view packet: canonical `src/calibration/**`,
`src/engine/**`, and `scripts/rebuild_*_v2.py` paths no longer read the bare
`hourly_observations` table. This packet records that proof and keeps the
remaining change narrow.

## Semantic Proofs

- Source role: this packet does not choose or change settlement source routing.
  It preserves existing settlement reads while requiring explicit metric
  identity.
- Dual-track separation: replay reads filter `settlements.temperature_metric`
  with either the replay `temperature_metric` argument or the position's stored
  `position_current.temperature_metric`; WU/trade-history compatibility lanes
  remain HIGH-default when they do not expose a low-aware public lane.
- Hourly evidence: canonical code must not consume bare `hourly_observations`;
  remaining references are schema/evidence adapter, compatibility ETL, tests,
  migration tooling, or docs evidence.
- No DB authority change: no production DB rows, migrations, schema objects, or
  v2 data population are changed.

## Scope

The machine-readable list lives in `scope.yaml`.

### In scope

- `src/engine/replay.py`
- `tests/test_semantic_linter.py`
- `tests/test_phase8_shadow_code.py`
- `tests/test_backtest_settlement_value_outcome.py`
- `tests/test_replay_time_provenance.py`
- `architecture/test_topology.yaml`
- `architecture/docs_registry.yaml`
- `architecture/topology.yaml`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/AGENTS.md`
- `docs/README.md`
- this packet folder

### Out of scope

- production DB mutation
- settlement source routing changes
- schema or migration changes
- P2 4.4.A upsert/revision-history redesign
- P3 4.5.B observation_instants_v2 reader-gate design
- P4 data population

## Deliverables

- Add explicit settlement `temperature_metric` predicates to replay settlement
  reads.
- Preserve backwards compatibility for HIGH-default replay lanes and update the
  metric-threading regression fixture to use metric-bearing settlements.
- Add semantic-linter proof that `src/engine/replay.py` and canonical hourly
  paths are clean.
- Refresh operations and topology control surfaces so `4.5.A` is closed and
  this repair packet is the active packet.

## Verification

- `python3 -m py_compile src/engine/replay.py tests/test_semantic_linter.py tests/test_phase8_shadow_code.py tests/test_backtest_settlement_value_outcome.py tests/test_replay_time_provenance.py`
- `python3 scripts/semantic_linter.py --check src/engine/replay.py src/calibration src/engine scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py scripts/validate_dynamic_alpha.py scripts/etl_forecast_skill_from_forecasts.py scripts/etl_historical_forecasts.py`
- `pytest -q tests/test_semantic_linter.py tests/test_phase8_shadow_code.py tests/test_backtest_settlement_value_outcome.py tests/test_replay_time_provenance.py tests/test_run_replay_cli.py`
- `python3 scripts/audit_replay_fidelity.py`
- `python3 scripts/topology_doctor.py --tests --json`
- `python3 scripts/topology_doctor.py --scripts --json`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <packet files> --plan-evidence docs/operations/task_2026-04-25_p3_usage_path_residual_guards/plan.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files <packet files>`
- `python3 scripts/topology_doctor.py --work-record --work-record-path docs/operations/task_2026-04-25_p3_usage_path_residual_guards/work_log.md`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files <packet tests>`
- `git diff --check -- <packet files>`

## Stop Conditions

- Stop if the replay fix requires a source-routing decision.
- Stop if the change expands into observation_instants_v2 metric-layer design.
- Stop if a DB migration or production data mutation becomes necessary.
