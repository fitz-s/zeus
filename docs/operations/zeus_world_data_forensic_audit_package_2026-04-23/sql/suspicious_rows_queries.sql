-- suspicious_rows_queries.sql
-- Verified daily observations with empty provenance.
SELECT city, target_date, source, station_id, high_temp, low_temp, unit, authority
FROM observations
WHERE authority='VERIFIED'
  AND (provenance_metadata IS NULL OR provenance_metadata='' OR provenance_metadata='{}')
ORDER BY target_date, city
LIMIT 500;

-- Nonpositive collection windows.
SELECT city, target_date, source, station_id, collection_window_start_utc, collection_window_end_utc, timezone
FROM observations
WHERE collection_window_end_utc <= collection_window_start_utc
LIMIT 100;

-- Settlement rows that cannot settle a replayable market.
SELECT id, city, target_date, market_slug, winning_bin, settlement_value, settlement_source, unit, temperature_metric
FROM settlements
WHERE market_slug IS NULL OR market_slug='' OR winning_bin IS NULL OR winning_bin='' OR settlement_value IS NULL
ORDER BY target_date, city
LIMIT 500;

-- Fallback-like v2 sources with VERIFIED authority.
SELECT city, target_date, source, station_id, authority, data_version, local_timestamp, utc_timestamp, temp_current, running_max, running_min
FROM observation_instants_v2
WHERE authority='VERIFIED'
  AND (source LIKE 'meteostat_%' OR source LIKE 'ogimet_%' OR source LIKE 'openmeteo_%')
ORDER BY target_date, city, source
LIMIT 500;

-- Legacy hourly rows around DST-like duplicate-risk hours; inspect manually with v2.
SELECT city, obs_date, source, COUNT(*) AS rows_on_day, MIN(obs_hour) AS min_hour, MAX(obs_hour) AS max_hour
FROM hourly_observations
GROUP BY city, obs_date, source
HAVING rows_on_day NOT BETWEEN 22 AND 26
ORDER BY obs_date, city
LIMIT 500;
