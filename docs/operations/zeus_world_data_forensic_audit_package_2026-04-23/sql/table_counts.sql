-- table_counts.sql
-- Read-only row counts for all Zeus world DB tables.
SELECT 'availability_fact' AS table_name, COUNT(*) AS row_count FROM availability_fact
UNION ALL
SELECT 'calibration_decision_group' AS table_name, COUNT(*) AS row_count FROM calibration_decision_group
UNION ALL
SELECT 'calibration_pairs' AS table_name, COUNT(*) AS row_count FROM calibration_pairs
UNION ALL
SELECT 'calibration_pairs_v2' AS table_name, COUNT(*) AS row_count FROM calibration_pairs_v2
UNION ALL
SELECT 'chronicle' AS table_name, COUNT(*) AS row_count FROM chronicle
UNION ALL
SELECT 'control_overrides_history' AS table_name, COUNT(*) AS row_count FROM control_overrides_history
UNION ALL
SELECT 'data_coverage' AS table_name, COUNT(*) AS row_count FROM data_coverage
UNION ALL
SELECT 'day0_metric_fact' AS table_name, COUNT(*) AS row_count FROM day0_metric_fact
UNION ALL
SELECT 'day0_residual_fact' AS table_name, COUNT(*) AS row_count FROM day0_residual_fact
UNION ALL
SELECT 'decision_log' AS table_name, COUNT(*) AS row_count FROM decision_log
UNION ALL
SELECT 'diurnal_curves' AS table_name, COUNT(*) AS row_count FROM diurnal_curves
UNION ALL
SELECT 'diurnal_peak_prob' AS table_name, COUNT(*) AS row_count FROM diurnal_peak_prob
UNION ALL
SELECT 'ensemble_snapshots' AS table_name, COUNT(*) AS row_count FROM ensemble_snapshots
UNION ALL
SELECT 'ensemble_snapshots_v2' AS table_name, COUNT(*) AS row_count FROM ensemble_snapshots_v2
UNION ALL
SELECT 'execution_fact' AS table_name, COUNT(*) AS row_count FROM execution_fact
UNION ALL
SELECT 'forecast_error_profile' AS table_name, COUNT(*) AS row_count FROM forecast_error_profile
UNION ALL
SELECT 'forecast_skill' AS table_name, COUNT(*) AS row_count FROM forecast_skill
UNION ALL
SELECT 'forecasts' AS table_name, COUNT(*) AS row_count FROM forecasts
UNION ALL
SELECT 'historical_forecasts' AS table_name, COUNT(*) AS row_count FROM historical_forecasts
UNION ALL
SELECT 'historical_forecasts_v2' AS table_name, COUNT(*) AS row_count FROM historical_forecasts_v2
UNION ALL
SELECT 'hko_hourly_accumulator' AS table_name, COUNT(*) AS row_count FROM hko_hourly_accumulator
UNION ALL
SELECT 'hourly_observations' AS table_name, COUNT(*) AS row_count FROM hourly_observations
UNION ALL
SELECT 'market_events' AS table_name, COUNT(*) AS row_count FROM market_events
UNION ALL
SELECT 'market_events_v2' AS table_name, COUNT(*) AS row_count FROM market_events_v2
UNION ALL
SELECT 'market_price_history' AS table_name, COUNT(*) AS row_count FROM market_price_history
UNION ALL
SELECT 'model_bias' AS table_name, COUNT(*) AS row_count FROM model_bias
UNION ALL
SELECT 'observation_instants' AS table_name, COUNT(*) AS row_count FROM observation_instants
UNION ALL
SELECT 'observation_instants_v2' AS table_name, COUNT(*) AS row_count FROM observation_instants_v2
UNION ALL
SELECT 'observations' AS table_name, COUNT(*) AS row_count FROM observations
UNION ALL
SELECT 'opportunity_fact' AS table_name, COUNT(*) AS row_count FROM opportunity_fact
UNION ALL
SELECT 'outcome_fact' AS table_name, COUNT(*) AS row_count FROM outcome_fact
UNION ALL
SELECT 'platt_models' AS table_name, COUNT(*) AS row_count FROM platt_models
UNION ALL
SELECT 'platt_models_v2' AS table_name, COUNT(*) AS row_count FROM platt_models_v2
UNION ALL
SELECT 'position_current' AS table_name, COUNT(*) AS row_count FROM position_current
UNION ALL
SELECT 'position_events' AS table_name, COUNT(*) AS row_count FROM position_events
UNION ALL
SELECT 'probability_trace_fact' AS table_name, COUNT(*) AS row_count FROM probability_trace_fact
UNION ALL
SELECT 'replay_results' AS table_name, COUNT(*) AS row_count FROM replay_results
UNION ALL
SELECT 'rescue_events_v2' AS table_name, COUNT(*) AS row_count FROM rescue_events_v2
UNION ALL
SELECT 'risk_actions' AS table_name, COUNT(*) AS row_count FROM risk_actions
UNION ALL
SELECT 'selection_family_fact' AS table_name, COUNT(*) AS row_count FROM selection_family_fact
UNION ALL
SELECT 'selection_hypothesis_fact' AS table_name, COUNT(*) AS row_count FROM selection_hypothesis_fact
UNION ALL
SELECT 'settlements' AS table_name, COUNT(*) AS row_count FROM settlements
UNION ALL
SELECT 'settlements_v2' AS table_name, COUNT(*) AS row_count FROM settlements_v2
UNION ALL
SELECT 'shadow_signals' AS table_name, COUNT(*) AS row_count FROM shadow_signals
UNION ALL
SELECT 'solar_daily' AS table_name, COUNT(*) AS row_count FROM solar_daily
UNION ALL
SELECT 'strategy_health' AS table_name, COUNT(*) AS row_count FROM strategy_health
UNION ALL
SELECT 'temp_persistence' AS table_name, COUNT(*) AS row_count FROM temp_persistence
UNION ALL
SELECT 'token_price_log' AS table_name, COUNT(*) AS row_count FROM token_price_log
UNION ALL
SELECT 'token_suppression' AS table_name, COUNT(*) AS row_count FROM token_suppression
UNION ALL
SELECT 'token_suppression_history' AS table_name, COUNT(*) AS row_count FROM token_suppression_history
UNION ALL
SELECT 'trade_decisions' AS table_name, COUNT(*) AS row_count FROM trade_decisions
UNION ALL
SELECT 'zeus_meta' AS table_name, COUNT(*) AS row_count FROM zeus_meta
ORDER BY table_name;
