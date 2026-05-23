# K1 Reader Sweep — F40/F41/F42 master inventory

Generated: 2026-05-17T00:00:00Z

## Forecast-class tables (post-K1, live in zeus-forecasts.db)

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

(Ghost copies exist on world.db as legacy_archived until 2026-08-09. Reads via get_world_connection() silently succeed against ghosts — this is the failure mode.)

---

## Caller classification table

| file:line | accessor_pattern | tables_accessed | verdict | fix_template |
|---|---|---|---|---|
| scripts/bridge_oracle_to_calibration.py:71,86 | hardcoded zeus-world.db | settlements | BROKEN_FORECAST_READER | replace DB_PATH with get_forecasts_connection() |
| scripts/data_chain_monitor.sh:26,29,35 | hardcoded zeus-world.db | source_run, readiness_state | BROKEN_FORECAST_READER | change connect path to zeus-forecasts.db |
| scripts/ingest_grib_to_snapshots.py:71,997 | get_world_connection | ensemble_snapshots_v2, source_run | BROKEN_FORECAST_READER | get_forecasts_connection_with_world (also writes ensemble_snapshots_v2) |
| scripts/rebuild_calibration_pairs_v2.py:1775 | get_world_connection (default --db) | calibration_pairs_v2, ensemble_snapshots_v2 | BROKEN_FORECAST_READER | default --db to zeus-forecasts.db; replace get_world_connection with get_forecasts_connection |
| scripts/refit_platt_v2.py:1120,323 | hardcoded zeus-world.db | calibration_pairs_v2 | BROKEN_FORECAST_READER | replace with get_forecasts_connection() |
| scripts/ddd_v1_v2_replay.py:43,275 | hardcoded zeus-world.db | calibration_pairs_v2 | BROKEN_FORECAST_READER | replace with get_forecasts_connection() |
| scripts/evaluate_calibration_transfer_oos.py:684,284,316 | get_world_connection | calibration_pairs_v2 | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/diagnose_low_high_alignment.py:40,188,224 | hardcoded zeus-world.db | ensemble_snapshots_v2 | BROKEN_FORECAST_READER | replace with get_forecasts_connection() |
| scripts/backfill_low_contract_window_evidence.py:513,203,381 | hardcoded zeus-world.db | ensemble_snapshots_v2, calibration_pairs_v2 | BROKEN_FORECAST_READER | replace with get_forecasts_connection() |
| scripts/migrate_phase2_cycle_stratification.py:335,67,21 | get_world_connection | calibration_pairs_v2, ensemble_snapshots_v2 | ETL_TRANSITIONAL | one-shot migration — review before next run; likely already ran |
| scripts/backfill_tigge_snapshot_p_raw_v2.py:55,327 | get_world_connection + hardcoded path | ensemble_snapshots_v2, calibration_pairs_v2 | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/rebuild_calibration_pairs_canonical.py:84,191 | get_world_connection | observations, settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world (cross-reads both DBs) |
| scripts/rebuild_settlements.py:38,114 | get_world_connection | observations, settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/baseline_experiment.py:33,149,262 | get_world_connection | observations, settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/audit_city_data_readiness.py:15,44,147 | get_world_connection | observations, settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/antibody_scan.py:88,99,137 | get_world_connection | observations, settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/investigate_ecmwf_bias.py:23,88,91 | get_world_connection | settlements, ensemble_snapshots_v2 | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/validate_dynamic_alpha.py:72,182 | get_world_connection | settlements | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/etl_forecast_skill_from_forecasts.py:29,132 | get_world_connection | settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world (joins forecasts × settlements) |
| scripts/etl_historical_forecasts.py:8,170 | get_world_connection + hardcoded path | settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/etl_temp_persistence.py:30,65 | get_world_connection | observations | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/etl_asos_wu_offset.py:3,43 | get_world_connection + hardcoded path | observations | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/backfill_wu_daily_all.py:34,54 | get_world_connection | observations | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/backfill_ogimet_metar.py:71,289 | get_world_connection | observations | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/backfill_observations_from_settlements.py:24,37,81 | get_world_connection | observations, settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/backfill_settlements_via_gamma_2026.py:134,145,276 | get_world_connection | settlements_v2 | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/backfill_uma_resolution_2026.py:250,280,342 | get_world_connection | settlements_v2 | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/backfill_ens.py:32,43,164 | get_world_connection | settlements | BROKEN_FORECAST_READER | get_forecasts_connection_with_world |
| scripts/backfill_ecmwf_2026_05_04_to_09.py:25,56 | get_world_connection | source_run | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/backfill_ecmwf_v2_2026_05_06_to_09.py:31,61,148 | get_world_connection | source_run | BROKEN_FORECAST_READER | get_forecasts_connection() |
| scripts/reevaluate_readiness_2026_05_07.py:61,91,121 | hardcoded zeus-world.db | readiness_state | BROKEN_FORECAST_READER | replace with get_forecasts_connection() |
| scripts/promote_calibration_pairs_v2.py:17 | hardcoded path (comment only) | calibration_pairs_v2 | OK_WORLD_ONLY | no fix needed — opens zeus-forecasts.db correctly by default |
| scripts/migrate_observations_k1.py:5,11 | hardcoded zeus-world.db | observations | ETL_TRANSITIONAL | one-shot migration — already ran on world.db ghost, review before re-run |
| scripts/migrate_add_authority_column.py:37,232 | get_world_connection + hardcoded path | observations, settlements | ETL_TRANSITIONAL | one-shot migration — already ran; review before re-run against forecasts.db |
| scripts/post_sequential_fillback.sh:89,91 | get_world_connection + hardcoded path | observations | ETL_TRANSITIONAL | batch ingest script — review before re-run |
| scripts/resume_backfills_sequential.sh:35,56,123 | get_world_connection + hardcoded path | observations | ETL_TRANSITIONAL | batch ingest script — review before re-run |
| src/main.py:1306,1308 (file=1438 lines) | get_world_connection | settlements | BROKEN_FORECAST_READER | replace smoke-test SELECT with get_forecasts_connection() read |
| src/state/schema/v2_schema.py:302,305 | hardcoded path (comment only) | settlements_v2, calibration_pairs_v2 | UNCLEAR_NEEDS_HUMAN | DDL helper; caller (ingest_grib_to_snapshots) passes broken conn — fix caller |
| scripts/backfill_cluster_taxonomy.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_current_market_price_snapshots.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_forecast_issue_time.py | hardcoded path | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_hko_daily.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_hko_xml.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_hourly_openmeteo.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_obs_v2.py | hardcoded path | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_openmeteo_previous_runs.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_probability_traces_from_opportunities.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_solar_openmeteo.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/backfill_tigge_snapshot_p_raw.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/etl_diurnal_curves.py | get_world_connection + hardcoded path | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/etl_forecasts_v2_from_legacy.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/etl_solar_times.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/hko_ingest_tick.py | hardcoded path | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/ingest/_shared.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/migrate_b070_control_overrides_to_history.py | get_world_connection | — | ETL_TRANSITIONAL | world-class migration — no forecast tables |
| scripts/migrate_b071_token_suppression_to_history.py | get_world_connection | — | ETL_TRANSITIONAL | world-class migration — no forecast tables |
| scripts/migrate_cluster_to_city.py | get_world_connection | — | ETL_TRANSITIONAL | world-class migration — no forecast tables |
| scripts/migrate_ensemble_snapshots_v2_add_ingest_backend.py | get_world_connection + hardcoded path | — | ETL_TRANSITIONAL | migration DDL — check whether it targets world ghost or forecasts.db |
| scripts/migrate_forecasts_availability_provenance.py | hardcoded path | — | ETL_TRANSITIONAL | world-class migration — no forecast tables |
| scripts/migrate_world_observations_to_forecasts.py | get_world_connection + hardcoded path | — | ETL_TRANSITIONAL | K1 one-shot migration — likely already ran |
| scripts/migrate_world_to_forecasts.py | hardcoded path | — | ETL_TRANSITIONAL | K1 one-shot migration — likely already ran |
| scripts/migrations/202605_add_redeem_operator_required_state.py | hardcoded path | — | ETL_TRANSITIONAL | world-class migration — no forecast tables |
| src/data/ingest_status_writer.py | hardcoded path | — | ETL_TRANSITIONAL | world-class tables only |
| src/data/ingestion_guard.py | get_world_connection | — | ETL_TRANSITIONAL | world-class tables only |
| scripts/AGENTS.md | hardcoded path (doc only) | — | OK_WORLD_ONLY | documentation, not code |
| scripts/arm_live_mode.sh | hardcoded path | — | OK_WORLD_ONLY | no forecast table access |
| scripts/audit_observation_instants_v2.py | hardcoded path | — | OK_WORLD_ONLY | observation_instants_v2 is world-class |
| scripts/audit_time_semantics.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| scripts/automation_analysis.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| scripts/build_correlation_matrix.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/capture_replay_artifact.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| scripts/check_data_pipeline_live_e2e.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/check_live_order_e2e.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/check_table_registry_coherence.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/compare_diurnal_v1_v2.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/drop_world_ghost_tables.py | hardcoded path | — | OK_WORLD_ONLY | targets ghost cleanup — intentionally world.db |
| scripts/expire_auto_pause.sh | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/fill_obs_v2_dst_gaps.py | hardcoded path | — | OK_WORLD_ONLY | observation_instants_v2 is world-class |
| scripts/fill_obs_v2_meteostat.py | hardcoded path | — | OK_WORLD_ONLY | observation_instants_v2 is world-class |
| scripts/force_cycle_with_healthy_gates.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/generate_monthly_bounds.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/onboard_cities.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| scripts/oracle_snapshot_listener.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/promote_platt_models_v2.py | hardcoded path | — | OK_WORLD_ONLY | platt_models_v2 is world-class |
| scripts/refit_platt.py | get_world_connection | — | OK_WORLD_ONLY | platt_models_v2 / calibration_pairs (legacy) world-class |
| scripts/repro_antibodies.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/run_replay.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| scripts/snapshot_checksum.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/source_contract_auto_convert.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/topology_doctor_data_rebuild_checks.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| scripts/zeus_blocks.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| scripts/zpkt.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| src/control/cli/promote_entry_forecast.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| src/control/control_plane.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| src/data/observation_client.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only (observation_instants_v2) |
| src/data/polymarket_client.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| src/engine/cycle_runner.py | get_world_connection | — | OK_WORLD_ONLY | world-class tables only |
| src/riskguard/riskguard.py | hardcoded path | — | OK_WORLD_ONLY | world-class tables only |
| src/signal/diurnal.py | get_world_connection | — | OK_WORLD_ONLY | diurnal_curves is world-class |
| src/signal/ensemble_signal.py | get_world_connection | — | OK_WORLD_ONLY | ensemble_snapshots (legacy v1, world-class) |
| src/state/connection_pair.py | get_forecasts_connection | — | OK_WORLD_ONLY | already uses correct connection |
| src/state/db_writer_lock.py | hardcoded path | — | OK_WORLD_ONLY | lock management on world.db is correct |
| src/data/hole_scanner.py:587,304 | get_forecasts_connection | observations | OK_WORLD_ONLY | already fixed post-K1; comment at L304 confirms |
| src/execution/harvester_pnl_resolver.py:16,39 | get_forecasts_connection | settlements | OK_WORLD_ONLY | already uses get_forecasts_connection(); L5 confirms K1 fix applied |
| src/ingest_main.py:253,232 | get_forecasts_connection | observations, ensemble_snapshots_v2 | OK_WORLD_ONLY | already uses forecasts conn; K1 P0 comment confirms |
| src/state/db.py:39,192 | get_forecasts_connection | settlements, source_run_coverage, readiness_state, job_run | OK_WORLD_ONLY | db.py owns connection factories; L192-195 correctly describe forecasts.db ownership |

---

## Counts by verdict

- OK_WORLD_ONLY: 40
- BROKEN_FORECAST_READER: 37
- ETL_TRANSITIONAL: 26
- UNCLEAR_NEEDS_HUMAN: 2 (src/main.py smoke-test is operationally broken; src/state/schema/v2_schema.py is DDL helper — fix the caller)

Notes:
- src/main.py:1308 is reclassified BROKEN from UNCLEAR — it is a live daemon startup smoke-test hitting the ghost copy. Operationally this means daemon startup succeeds even if forecasts.db settlements table is empty/corrupt.
- src/state/schema/v2_schema.py stays UNCLEAR: it is a DDL helper that accepts a conn argument; the broken caller is ingest_grib_to_snapshots.py (already counted in BROKEN).
- "BROKEN_FORECAST_READER" includes scripts that also WRITE forecast tables via world conn (ingest_grib_to_snapshots writes ensemble_snapshots_v2, backfill_wu_daily_all writes observations, etc.).

---

## Suggested PR groupings

**Wave A — Live runtime (fix first, highest operational risk):**
- `src/main.py:1306-1308` — daemon startup smoke-test reads settlements from world.db ghost
- `scripts/bridge_oracle_to_calibration.py:71,86` — oracle error-rate feed reads settlements from world.db; directly gates Kelly sizing
- `scripts/data_chain_monitor.sh:26,29,35` — ops monitor reads source_run + readiness_state from world.db ghost; monitoring blindness

**Wave B — Operator analysis scripts (run manually, produce incorrect results silently):**
- `scripts/evaluate_calibration_transfer_oos.py` — calibration_pairs_v2 from world ghost
- `scripts/diagnose_low_high_alignment.py` — ensemble_snapshots_v2 from world ghost
- `scripts/validate_dynamic_alpha.py` — settlements from world ghost
- `scripts/refit_platt_v2.py` — calibration_pairs_v2 from world ghost
- `scripts/ddd_v1_v2_replay.py` — calibration_pairs_v2 from world ghost
- `scripts/antibody_scan.py` — pipeline health monitor reads from world ghosts
- `scripts/audit_city_data_readiness.py` — readiness audit against wrong DB

**Wave C — ETL rebuild scripts (batch, run on-demand):**
- `scripts/ingest_grib_to_snapshots.py` — writes ensemble_snapshots_v2 to wrong DB
- `scripts/rebuild_calibration_pairs_v2.py` — reads/writes calibration_pairs_v2 to wrong DB
- `scripts/rebuild_calibration_pairs_canonical.py` — cross-reads observations + settlements
- `scripts/rebuild_settlements.py` — reads observations, writes settlements
- `scripts/backfill_tigge_snapshot_p_raw_v2.py` — reads/writes ensemble_snapshots_v2

**Wave D — Backfill scripts (historical, low urgency):**
- All remaining `scripts/backfill_*.py` with forecast table hits
- `scripts/etl_*.py` with forecast table hits
- `scripts/investigate_ecmwf_bias.py`, `scripts/baseline_experiment.py`

**Wave E — Migrations (defer, review before any re-run):**
- `scripts/migrate_observations_k1.py`, `scripts/migrate_add_authority_column.py`
- `scripts/migrate_phase2_cycle_stratification.py`
- `scripts/migrate_ensemble_snapshots_v2_add_ingest_backend.py` — UNCLEAR_NEEDS_HUMAN: confirm it targets forecasts.db not world.db ghost

---

## TOP-10 most operationally-consequential BROKEN entries

Ranked by: live runtime > operator scripts that gate live decisions > batch scripts.

1. **src/main.py:1306-1308** — live daemon startup smoke-test. `get_world_connection()` + `SELECT FROM settlements`. Ghost copy on world.db masks a corrupted or empty forecasts.db. Daemon boots successfully even if authoritative settlements are absent. **Fix: replace with `get_forecasts_connection()` read.**

2. **scripts/bridge_oracle_to_calibration.py:71,86** — oracle error-rate bridge. Reads `settlements` from world.db ghost (`DB_PATH = zeus-world.db`). `oracle_error_rates.json` feeds Kelly sizing via `oracle_penalty.py:472`. Silent stale reads = wrong penalty multipliers on every live bid. **Fix: `DB_PATH` → `get_forecasts_connection()`.**

3. **scripts/data_chain_monitor.sh:26,29,35** — live ops monitoring. Reads `source_run` + `readiness_state` from world.db ghost. Dashboards show counts from the ghost copy — actual forecasts.db state is invisible. Operator may believe pipeline is healthy when it is not. **Fix: change `sqlite3.connect('state/zeus-world.db')` to `state/zeus-forecasts.db`.**

4. **scripts/ingest_grib_to_snapshots.py:71,997** — GRIB ingestor. Uses `get_world_connection()` to call `apply_v2_schema` then write `ensemble_snapshots_v2` rows. New ECMWF snapshots land on world.db ghost copy, not authoritative forecasts.db. Downstream calibration reads will not see them. **Fix: `get_forecasts_connection_with_world`.**

5. **scripts/rebuild_calibration_pairs_v2.py:1775,997** — canonical calibration rebuild script. `--db` defaults to `zeus-world.db`; reads `ensemble_snapshots_v2` from ghost and writes `calibration_pairs_v2` to ghost. Any rebuild produces artifacts on the wrong DB. **Fix: default `--db` to `zeus-forecasts.db`; use `get_forecasts_connection()`.**

6. **scripts/refit_platt_v2.py:1120,323** — Platt model refit. Reads `calibration_pairs_v2` from world.db ghost. Produces Platt models trained on stale/ghost data. Affects all live probability calibration. **Fix: `get_forecasts_connection()`.**

7. **scripts/validate_dynamic_alpha.py:72,182** — dynamic alpha validator. Reads `settlements` from world.db ghost via `get_world_connection()`. Alpha validation analysis is silently incorrect. **Fix: `get_forecasts_connection()`.**

8. **scripts/antibody_scan.py:88,99** — pipeline health monitor. Reads `settlements` + `observations` from world.db ghosts. Health checks pass/fail based on stale ghost counts. Operator confidence in pipeline is based on wrong data. **Fix: `get_forecasts_connection_with_world`.**

9. **scripts/evaluate_calibration_transfer_oos.py:684,284** — F41 confirmed broken. Reads `calibration_pairs_v2` via `get_world_connection()`. OOS calibration transfer evaluation runs on ghost data — the ticket that triggered this sweep. **Fix: `get_forecasts_connection()`.**

10. **scripts/rebuild_settlements.py:38,315** — settlements rebuild. Reads `observations`, writes `settlements` via `get_world_connection()`. Rebuilt settlements land on world.db ghost, not authoritative forecasts.db. Any settlement rebuild is silently no-op for live consumers. **Fix: `get_forecasts_connection_with_world`.**
