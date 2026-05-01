# Phase 2 Trading-Side Deliverables — Executor Report

**Date:** 2026-04-30
**Branch:** claude/quizzical-bhabha-8bdc0d
**Tests:** 145 passed (31 new + 114 regression), 0 failed

---

## Files Created

### A. Trading freshness gate (§3.1, P0)
- `src/control/freshness_gate.py` — three-branch FRESH/STALE/ABSENT decision tree; `evaluate_freshness()`, `evaluate_freshness_at_boot()` (5-min retry then SystemExit), `evaluate_freshness_mid_run()` (degrade-only); operator override via control_plane.json `force_ignore_freshness`

### B. World-view accessor layer (§3.2, P0)
- `src/contracts/world_view/__init__.py` — re-exports all four typed accessors
- `src/contracts/world_view/observations.py` — `get_latest_observation(world_conn, city, target_date) -> ObservationView | None`; queries observation_instants_v2 first, falls back to legacy
- `src/contracts/world_view/settlements.py` — `get_settlement_truth(world_conn, city, target_date) -> SettlementView | None`
- `src/contracts/world_view/calibration.py` — `get_active_platt_model(world_conn, city, season, metric_identity) -> PlattModelView | None`; wraps load_platt_model_v2 with typed API
- `src/contracts/world_view/forecasts.py` — `get_latest_forecast(world_conn, city, target_date, lead_days) -> ForecastView | None`
- `src/state/connection_pair.py` — `ConnectionPair(trade_conn, world_conn)` dataclass; `get_connection_pair()` factory
- `tests/conftest_connection_pair.py` — `fake_connection_pair()` helper for test monkeypatches; migration guide for 10+ riskguard test sites

### C. Replay moved to scripts/audit/ (§3.5, P1)
- `scripts/audit/replay.py` — audit-lane entry point; monkey-patches `get_trade_connection_with_world` to open world DB with `?mode=ro` URI; re-exports public API from src.engine.replay for backward compat

### D+E. Operational hardening
- `config/ops/zeus.newsyslog.conf.template` — §4.5(b) daily log rotation, 14-day retention, gzip; 4 log files; sudo install instructions
- `docs/runbooks/secrets_rotation.md` — §4.5(c) WU/ECMWF rotation touches ingest plist only; POLYMARKET_API_KEY touches trading plist only; explicit muscle-memory warning
- `~/Library/LaunchAgents/com.zeus.heartbeat-sensor.plist.proposed` — §4.5(d) extends sensor to monitor both daemon-heartbeat.json and daemon-heartbeat-ingest.json; NOT installed (proposed only)

### E(a). control_plane dual consumer stub
- `src/ingest_main.py` — PHASE-3-STUB §4.5(a) comment at START of main() boot block; documents pause_source/resume_source/pause_ingest wiring for Phase 3

### F. Schema manifest + validator (§1 axis 4, P1)
- `architecture/world_schema_manifest.yaml` — 8 world DB tables documented with required/optional columns + semantic notes; schema_version=1
- `src/contracts/world_schema_validator.py` — `validate_world_schema_at_boot(world_conn) -> bool`; reads manifest, runs PRAGMA table_info(), warns on mismatch (Phase 2: warn; Phase 3: FATAL)

### G. 4 antibody test files
- `tests/test_data_freshness_gate.py` — **Antibody #6**: 14 tests covering FRESH/STALE/ABSENT branches, operator override, retry exhaustion SystemExit
- `tests/test_no_raw_world_attach.py` — **Antibody #13**: grep scan of src/engine|strategy|signal|execution; execution/ files allowlisted during Phase 2 overlap; asserts world_view + connection_pair modules exist
- `tests/test_control_plane_dual_consumer.py` — **Antibody #14**: PHASE-3-STUB marker present; functional operator-override test via freshness_gate
- `tests/test_heartbeat_dual_coverage.py` — **Antibody #15**: both daemon heartbeat files written; proposed plist monitors both; functional ingest heartbeat shape

## Files Modified

- `src/engine/cycle_runner.py:40-46` — Phase 2 seam migration comment; added `get_connection_pair` import alongside legacy `get_connection` alias
- `src/main.py` — wired `_startup_freshness_check()` at boot (warn-only Phase 2); `validate_world_schema_at_boot()` call; `_etl_recalibrate_body` replay audit now uses `scripts/audit/replay.py` with ro enforcement; §3.7 gate-split comment (wallet=never override, data=operator override)
- `src/ingest_main.py` — PHASE-3-STUB §4.5(a) prepended to main() (task-spec exception: only START of boot block)

## Key Design Decisions

1. **freshness_gate is consume-only**: reads source_health.json written by ingest (Phase 2 ingest-side). Zero new writes in trading lane.
2. **world_view uses explicit world_conn**: no module-level singletons; no ATTACH DATABASE. Caller opens and closes connection.
3. **connection_pair keeps legacy alias alive**: `get_connection = get_trade_connection_with_world` in cycle_runner.py untouched; Phase 3 deletes it.
4. **replay.py stays in src/engine/**: 6 test files import from it. `scripts/audit/replay.py` is the new invocation path with ro enforcement.
5. **Phase 2 freshness gate is warn-only**: ABSENT at boot logs CRITICAL but does not exit (Phase 3 promotes to FATAL).

## Verification

```
ZEUS_MODE=live pytest tests/test_data_freshness_gate.py tests/test_no_raw_world_attach.py \
  tests/test_control_plane_dual_consumer.py tests/test_heartbeat_dual_coverage.py \
  tests/test_trading_isolation.py tests/test_world_writer_boundary.py \
  tests/test_dual_run_lock_obeyed.py tests/test_harvester_split_independence.py \
  tests/test_load_platt_v2_data_version_filter.py tests/test_evaluator_explicit_n_mc.py \
  tests/test_runtime_n_mc_floor.py tests/test_live_safe_strategies.py \
  tests/test_platt_bootstrap_equivalence.py tests/test_platt.py \
  tests/test_phase4_platt_v2.py tests/test_ingest_isolation.py tests/test_config.py

145 passed, 2 warnings in 4.89s
```
