-- causality_checks.sql
SELECT 'forecasts' AS table_name, COUNT(*) AS n, SUM(forecast_issue_time IS NULL OR forecast_issue_time='') AS issue_time_null FROM forecasts
UNION ALL SELECT 'historical_forecasts', COUNT(*), NULL FROM historical_forecasts
UNION ALL SELECT 'historical_forecasts_v2', COUNT(*), SUM(available_at IS NULL OR available_at='') FROM historical_forecasts_v2
UNION ALL SELECT 'ensemble_snapshots', COUNT(*), NULL FROM ensemble_snapshots
UNION ALL SELECT 'ensemble_snapshots_v2', COUNT(*), SUM(issue_time IS NULL OR issue_time='') FROM ensemble_snapshots_v2
UNION ALL SELECT 'calibration_pairs', COUNT(*), NULL FROM calibration_pairs
UNION ALL SELECT 'calibration_pairs_v2', COUNT(*), SUM(forecast_available_at IS NULL OR forecast_available_at='') FROM calibration_pairs_v2
UNION ALL SELECT 'market_events', COUNT(*), NULL FROM market_events
UNION ALL SELECT 'market_events_v2', COUNT(*), NULL FROM market_events_v2
UNION ALL SELECT 'replay_results', COUNT(*), NULL FROM replay_results;

SELECT causality_status, training_allowed, authority, data_version, COUNT(*) AS n
FROM ensemble_snapshots_v2
GROUP BY causality_status, training_allowed, authority, data_version;

SELECT causality_status, training_allowed, authority, data_version, COUNT(*) AS n
FROM calibration_pairs_v2
GROUP BY causality_status, training_allowed, authority, data_version;

-- Rows that should not enter canonical training if any appear later.
SELECT *
FROM ensemble_snapshots_v2
WHERE training_allowed != 1 OR causality_status != 'OK' OR authority != 'VERIFIED'
LIMIT 100;
