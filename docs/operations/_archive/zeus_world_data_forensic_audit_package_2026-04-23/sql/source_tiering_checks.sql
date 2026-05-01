-- source_tiering_checks.sql
SELECT source, station_id, authority, data_source_version, unit,
       COUNT(*) AS n, COUNT(DISTINCT city) AS cities, MIN(target_date) AS min_date, MAX(target_date) AS max_date
FROM observations
GROUP BY source, station_id, authority, data_source_version, unit
ORDER BY n DESC;

SELECT source, station_id, authority, data_version, temp_unit,
       COUNT(*) AS n, COUNT(DISTINCT city) AS cities, MIN(target_date) AS min_date, MAX(target_date) AS max_date
FROM observation_instants_v2
GROUP BY source, station_id, authority, data_version, temp_unit
ORDER BY n DESC;

SELECT city, target_date, COUNT(DISTINCT source) AS sources_on_day,
       GROUP_CONCAT(DISTINCT source) AS source_list
FROM observation_instants_v2
GROUP BY city, target_date
HAVING COUNT(DISTINCT source) > 1
ORDER BY sources_on_day DESC, city, target_date
LIMIT 200;

-- Fallback-like sources present in current v2 family.
SELECT source, COUNT(*) AS n, MIN(target_date) AS min_date, MAX(target_date) AS max_date
FROM observation_instants_v2
WHERE source LIKE 'meteostat_%' OR source LIKE 'ogimet_%' OR source LIKE 'openmeteo_%'
GROUP BY source
ORDER BY n DESC;
