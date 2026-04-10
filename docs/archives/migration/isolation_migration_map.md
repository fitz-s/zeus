# Connection Isolation Migration Map

Generated: 2026-04-07
Purpose: Track migration of `get_connection()` (legacy monolithic) to split connection functions.

## Connection function reference

| Function | Database | Tables |
|----------|----------|--------|
| `get_trade_connection(mode=None)` | `zeus-{mode}.db` | trade_decisions, chronicle, position_events, decision_log, position_current, outcome_fact, opportunity_fact, strategy_health, replay_results |
| `get_shared_connection()` | `zeus-shared.db` | settlements, observations, ensemble_snapshots, calibration_pairs, platt_models, forecast_skill, market_events, token_price_log, shadow_signals, diurnal_curves, diurnal_peak_prob, ecmwf_*, asos_wu_offsets, ladder_*, solar_times, solar_daily, cluster_taxonomy, semantic_snapshots, historical_forecasts, temp_persistence, tigge_*, alpha_overrides, model_bias, observation_instants |
| `get_trade_connection_with_shared(mode=None)` | trade DB + shared attached as `shared` schema | both; shared tables use `shared.tablename` in SQL |

## Migration map

| File | Line | Classification | Tables Accessed | Notes |
|------|------|----------------|-----------------|-------|
| `src/observability/status_summary.py` | 109 | ALREADY_DONE | position_current, strategy_health (TRADE) | Uses `get_trade_connection_with_shared` |
| `src/state/portfolio.py` | 803 | TRADE | position_current, portfolio views | Was listed as :1176; actual call at 803. Use `get_trade_connection()` |
| `src/execution/harvester.py` | 118 | BOTH | position_events (TRADE); ensemble_snapshots, calibration_pairs, settlements (SHARED) | Use `get_trade_connection_with_shared()`; prefix shared table refs with `shared.` |
| `src/main.py` | 245 | SHARED | model_bias, asos_wu_offsets, observation_instants, diurnal_curves, diurnal_peak_prob, temp_persistence, solar_daily (all SHARED) | init_schema + startup health check; all queried tables are SHARED. Use `get_shared_connection()` |
| `src/engine/replay.py` | 664 | BOTH | trade_decisions, decision_log, replay_results (TRADE); shadow_signals, ensemble_snapshots, settlements, calibration_pairs, market_events (SHARED) | Use `get_trade_connection_with_shared()`; prefix SHARED tables with `shared.` in SQL |
| `src/signal/ensemble_signal.py` | 189 | ALREADY_DONE | model_bias (SHARED) | Uses `get_shared_connection` inline |
| `src/signal/diurnal.py` | 33 | ALREADY_DONE | solar_daily (SHARED) | Uses `get_shared_connection` inline |
| `src/signal/diurnal.py` | 123 | ALREADY_DONE | diurnal_curves, diurnal_peak_prob (SHARED) | Uses `get_shared_connection` inline |
| `src/signal/diurnal.py` | 207 | ALREADY_DONE | diurnal_peak_prob, diurnal_curves (SHARED) | Uses `get_shared_connection` inline |
| `src/control/control_plane.py` | 110 | ALREADY_DONE | alpha_overrides (SHARED) | Uses `get_shared_connection` |
| `src/control/control_plane.py` | 191 | ALREADY_DONE | alpha_overrides (SHARED) | Uses `get_shared_connection` |
| `src/strategy/market_fusion.py` | 174 | ALREADY_DONE | alpha_overrides (SHARED) | Uses `get_shared_connection` inline |
| `scripts/migrate_rainstorm_data.py` | 37 | SHARED | settlements, observations, market_events, token_price_log | Use `get_shared_connection()` |
| `scripts/baseline_experiment.py` | 257 | SHARED | observations, market_events, settlements | Use `get_shared_connection()` |
| `scripts/audit_replay_fidelity.py` | 22 | BOTH | settlements, ensemble_snapshots (SHARED); trade_decisions (TRADE) | Use `get_trade_connection_with_shared()`; prefix SHARED tables with `shared.` |
| `scripts/refit_platt.py` | 26 | ALREADY_DONE | calibration_pairs, platt_models (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_diurnal_curves.py` | 42 | ALREADY_DONE | diurnal_curves, diurnal_peak_prob, observation_instants (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_historical_forecasts.py` | 65 | ALREADY_DONE | historical_forecasts (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/backfill_cluster_taxonomy.py` | 37 | ALREADY_DONE | calibration_pairs (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_hourly_observations.py` | 23 | ALREADY_DONE | observation_instants (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/generate_calibration_pairs.py` | 34 | ALREADY_DONE | calibration_pairs (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/backfill_ens.py` | 148 | ALREADY_DONE | ensemble_snapshots (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/investigate_ecmwf_bias.py` | 24 | ALREADY_DONE | model_bias (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_market_price_history.py` | 58 | ALREADY_DONE | token_price_log, market_events (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/automation_analysis.py` | 29 | SHARED | alpha_overrides, model_bias, calibration_pairs, platt_models (SHARED) | Use `get_shared_connection()` |
| `scripts/run_replay.py` | 62 | SHARED | init_schema only (schema creation); actual replay uses both via `run_replay()` internal connections | Use `get_shared_connection()` for schema init connection |
| `scripts/audit_replay_completeness.py` | 19 | TRADE | trade_decisions | Use `get_trade_connection()` |
| `scripts/backfill_recent_exits_attribution.py` | 27 | TRADE | trade_decisions, decision_log | Use `get_trade_connection()` |
| `scripts/backfill_trade_decision_attribution.py` | 103 | TRADE | trade_decisions, decision_log | Use `get_trade_connection()` |
| `scripts/etl_temp_persistence.py` | 60 | ALREADY_DONE | temp_persistence (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/data_completeness_audit.py` | 60 | BOTH | token_price_log (SHARED); trade_decisions (TRADE) | Use `get_trade_connection_with_shared()`; prefix `token_price_log` with `shared.` |
| `scripts/etl_ladder_backfill.py` | 46 | ALREADY_DONE | ladder_* (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/audit_time_semantics.py` | 20 | SHARED | observation_instants, solar_daily, diurnal_curves, diurnal_peak_prob (SHARED) | Use `get_shared_connection()` |
| `scripts/force_lifecycle.py` | 13 | BOTH | token_price_log (SHARED); log_trade_entry → position_events (TRADE) | Use `get_trade_connection_with_shared()`; prefix `token_price_log` with `shared.` |
| `scripts/profit_validation_replay.py` | 40 | BOTH | token_price_log (SHARED); trade_decisions (TRADE) | Use `get_trade_connection_with_shared()`; prefix `token_price_log` with `shared.` |
| `scripts/capture_replay_artifact.py` | 35 | SHARED | shadow_signals (SHARED) | Use `get_shared_connection()` |
| `scripts/etl_observation_instants.py` | 46 | ALREADY_DONE | observation_instants (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/build_tigge_priority.py` | 25 | ALREADY_DONE | tigge_* (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_solar_times.py` | 22 | ALREADY_DONE | solar_times, solar_daily (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_tigge_ens.py` | 54 | ALREADY_DONE | ensemble_snapshots, tigge_* (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_tigge_calibration.py` | 44 | ALREADY_DONE | calibration_pairs, tigge_* (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/etl_asos_wu_offset.py` | 28 | ALREADY_DONE | asos_wu_offsets (SHARED) | Uses `get_shared_connection as get_connection` |
| `scripts/validate_dynamic_alpha.py` | 137 | SHARED | forecast_skill (SHARED) | Use `get_shared_connection()` |
| `scripts/backfill_semantic_snapshots.py` | 107 | BOTH | trade_decisions (TRADE) JOIN ensemble_snapshots (SHARED) | Use `get_trade_connection_with_shared()`; prefix `ensemble_snapshots` with `shared.` |
| `scripts/audit_replay_completeness.py` | 19 | TRADE | trade_decisions | Use `get_trade_connection()` |
| `scripts/backfill_recent_exits_attribution.py` | 27 | TRADE | trade_decisions, decision_log | Use `get_trade_connection()` |

## Summary

| Classification | Count |
|----------------|-------|
| ALREADY_DONE | 23 |
| TRADE (need migration) | 4 |
| SHARED (need migration) | 9 |
| BOTH (need migration) | 7 |
| **Total sites** | **43** |
| **Total needing changes** | **20** |
