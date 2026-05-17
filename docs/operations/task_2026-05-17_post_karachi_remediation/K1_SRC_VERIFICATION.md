# K1 src/ Verification — 11 callers triaged

Generated: 2026-05-17T12:05:00Z
Authority: architecture/db_table_ownership.yaml

## Forecast-class tables (10)
- observations
- settlements
- settlements_v2
- source_run
- job_run
- source_run_coverage
- readiness_state
- market_events_v2
- ensemble_snapshots_v2
- calibration_pairs_v2

## Per-file table

| file:line | scope | tables accessed | verdict | fix priority |
|---|---|---|---|---|
| src/control/control_plane.py:259 | pause_entries | control_overrides | OK_WORLD_ONLY | none |
| src/control/control_plane.py:381 | refresh_control_state | control_overrides | OK_WORLD_ONLY | none |
| src/control/control_plane.py:472 | _apply_command | control_overrides | OK_WORLD_ONLY | none |
| src/data/hole_scanner.py:595 | main | data_coverage, observation_instants, solar_daily, forecasts | OK_WORLD_ONLY | none |
| src/data/ingestion_guard.py:148 | _log_availability_failure | availability_fact | OK_WORLD_ONLY | none |
| src/data/observation_client.py:521 | _get_asos_wu_offset | asos_wu_offsets | OK_WORLD_ONLY | none |
| src/engine/cycle_runner.py:882 | EntriesBlockRegistry.from_runtime | (factory injection) | OK_WORLD_ONLY | none |
| src/ingest_main.py:258 | _k2_hourly_instants_tick | observation_instants (via hourly_tick) | OK_WORLD_ONLY | none |
| src/ingest_main.py:279 | _k2_solar_daily_tick | solar_daily (via daily_tick) | OK_WORLD_ONLY | none |
| src/ingest_main.py:300 | _k2_forecasts_daily_tick | forecasts (via daily_tick) | OK_WORLD_ONLY | none |
| src/ingest_main.py:321 | _k2_hole_scanner_tick | (various scanner tables) | OK_WORLD_ONLY | none |
| src/ingest_main.py:369 | _boot_self_test | forecasts, data_coverage | OK_WORLD_ONLY | none |
| src/ingest_main.py:752 | _k2_daily_obs_tick | (legacy call site, see L395/L400) | OK_WORLD_ONLY | none |
| src/ingest_main.py:892 | _run_historical_backfill | (generic backfill) | OK_WORLD_ONLY | none |
| src/ingest_main.py:1019 | _k2_hourly_instants_tick | observation_instants | OK_WORLD_ONLY | none |
| src/ingest_main.py:1060 | _boot_self_test | zeus_meta | OK_WORLD_ONLY | none |
| src/main.py:315 | _wrap_unwrap_liveness_guard_cycle | wrap_unwrap_commands | OK_WORLD_ONLY | none |
| src/main.py:1121 | _startup_wallet_check | settlements (world shell) | OK_BY_INTENT — world-DB reachability probe | none |
| src/signal/diurnal.py:35 | get_solar_day | solar_daily | OK_WORLD_ONLY | none |
| src/signal/diurnal.py:127 | get_peak_hour_context | diurnal_curves, diurnal_peak_prob | OK_WORLD_ONLY | none |
| src/signal/diurnal.py:224 | post_peak_confidence | diurnal_peak_prob, diurnal_curves | OK_WORLD_ONLY | none |
| src/signal/ensemble_signal.py:360 | _apply_bias_correction | model_bias | OK_WORLD_ONLY | none |
| src/state/connection_pair.py:264 | get_connection_pair | (wrapper) | OK_WORLD_ONLY | none |
| src/state/connection_pair.py:282 | get_connection_triple | (wrapper) | OK_WORLD_ONLY | none |
| src/state/db.py:180 | get_world_connection | (authority definition) | OK_AUTHORITY | none |

## Counts
- OK_WORLD_ONLY: 22
- OK_BY_INTENT: 1 (main.py:1121)
- OK_AUTHORITY: 1 (db.py)
- BROKEN_FORECAST_READER: 0
- BROKEN_BOTH_CLASSES: 0
- UNCLEAR: 0

## TOP-3 most live-impactful BROKEN entries
No BROKEN entries identified in this sweep. All audited callers of `get_world_connection` are either correctly targeting world-class tables (e.g., `solar_daily`, `diurnal_curves`, `control_overrides`) or are known/authority sites.
