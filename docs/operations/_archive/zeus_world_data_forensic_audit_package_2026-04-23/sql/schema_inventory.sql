-- schema_inventory.sql
SELECT type, name, tbl_name, sql
FROM sqlite_master
WHERE name NOT LIKE 'sqlite_%'
ORDER BY type, tbl_name, name;

-- Table columns for critical tables; run each PRAGMA in sqlite shell or via a client.
PRAGMA table_info(observations);
PRAGMA table_info(settlements);
PRAGMA table_info(settlements_v2);
PRAGMA table_info(observation_instants_v2);
PRAGMA table_info(hourly_observations);
PRAGMA table_info(forecasts);
PRAGMA table_info(historical_forecasts_v2);
PRAGMA table_info(ensemble_snapshots_v2);
PRAGMA table_info(calibration_pairs_v2);
PRAGMA table_info(data_coverage);

PRAGMA index_list(observations);
PRAGMA index_list(settlements);
PRAGMA index_list(settlements_v2);
PRAGMA index_list(observation_instants_v2);
PRAGMA index_list(hourly_observations);
PRAGMA index_list(ensemble_snapshots_v2);
PRAGMA index_list(calibration_pairs_v2);
