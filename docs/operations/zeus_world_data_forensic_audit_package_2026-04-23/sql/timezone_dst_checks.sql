-- timezone_dst_checks.sql
SELECT 'daily_observations_time_fields' AS check_name,
       COUNT(*) AS n,
       SUM(timezone IS NULL OR timezone='') AS timezone_null,
       SUM(utc_offset_minutes IS NULL) AS offset_null,
       SUM(dst_active IS NULL) AS dst_null,
       SUM(is_ambiguous_local_hour IS NULL) AS ambiguous_null,
       SUM(is_missing_local_hour IS NULL) AS missing_null,
       SUM(collection_window_start_utc IS NULL OR collection_window_end_utc IS NULL) AS window_null,
       SUM(collection_window_end_utc <= collection_window_start_utc) AS nonpositive_window
FROM observations;

SELECT 'instants_v2_time_fields' AS check_name,
       COUNT(*) AS n,
       SUM(timezone_name IS NULL OR timezone_name='') AS timezone_null,
       SUM(local_timestamp IS NULL OR local_timestamp='') AS local_ts_null,
       SUM(utc_timestamp IS NULL OR utc_timestamp='') AS utc_ts_null,
       SUM(utc_offset_minutes IS NULL) AS offset_null,
       SUM(is_ambiguous_local_hour NOT IN (0,1)) AS ambiguous_bad,
       SUM(is_missing_local_hour NOT IN (0,1)) AS missing_bad
FROM observation_instants_v2;

SELECT city, target_date, source,
       COUNT(*) AS rows_in_local_day,
       SUM(is_ambiguous_local_hour) AS ambiguous_rows,
       SUM(is_missing_local_hour) AS missing_rows,
       MIN(local_timestamp) AS min_local,
       MAX(local_timestamp) AS max_local
FROM observation_instants_v2
GROUP BY city, target_date, source
HAVING rows_in_local_day NOT BETWEEN 22 AND 26
ORDER BY target_date, city
LIMIT 200;

SELECT city, obs_date, source, obs_hour, COUNT(*) AS duplicate_local_hour_count
FROM hourly_observations
GROUP BY city, obs_date, source, obs_hour
HAVING COUNT(*) > 1
ORDER BY duplicate_local_hour_count DESC
LIMIT 100;
