-- null_and_duplicate_checks.sql
SELECT 'observations_nulls' AS check_name,
       COUNT(*) AS n,
       SUM(high_temp IS NULL) AS high_null,
       SUM(low_temp IS NULL) AS low_null,
       SUM(unit IS NULL) AS unit_null,
       SUM(station_id IS NULL OR station_id='') AS station_null,
       SUM(timezone IS NULL OR timezone='') AS timezone_null,
       SUM(collection_window_start_utc IS NULL OR collection_window_end_utc IS NULL) AS window_null,
       SUM(provenance_metadata IS NULL OR provenance_metadata='' OR provenance_metadata='{}') AS provenance_empty
FROM observations;

SELECT 'observations_duplicates' AS check_name, city, target_date, source, COUNT(*) AS n
FROM observations
GROUP BY city, target_date, source
HAVING COUNT(*) > 1;

SELECT 'observations_high_lt_low' AS check_name, COUNT(*) AS n
FROM observations
WHERE high_temp < low_temp;

SELECT 'settlements_nulls' AS check_name,
       COUNT(*) AS n,
       SUM(market_slug IS NULL OR market_slug='') AS market_slug_null,
       SUM(winning_bin IS NULL OR winning_bin='') AS winning_bin_null,
       SUM(settlement_value IS NULL) AS settlement_value_null,
       SUM(unit IS NULL OR unit='') AS unit_null,
       SUM(temperature_metric IS NULL OR temperature_metric='') AS metric_null,
       SUM(provenance_json IS NULL OR provenance_json='' OR provenance_json='{}') AS provenance_empty
FROM settlements;

SELECT 'settlements_duplicates' AS check_name, city, target_date, COUNT(*) AS n
FROM settlements
GROUP BY city, target_date
HAVING COUNT(*) > 1;

SELECT 'observation_instants_v2_duplicates' AS check_name, city, source, utc_timestamp, COUNT(*) AS n
FROM observation_instants_v2
GROUP BY city, source, utc_timestamp
HAVING COUNT(*) > 1;

SELECT 'hourly_observations_duplicates' AS check_name, city, obs_date, obs_hour, source, COUNT(*) AS n
FROM hourly_observations
GROUP BY city, obs_date, obs_hour, source
HAVING COUNT(*) > 1;
