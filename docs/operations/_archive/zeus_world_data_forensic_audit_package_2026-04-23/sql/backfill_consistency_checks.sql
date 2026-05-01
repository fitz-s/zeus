-- backfill_consistency_checks.sql
SELECT data_table, status, COUNT(*) AS n, COUNT(DISTINCT city) AS cities, COUNT(DISTINCT data_source) AS sources,
       MIN(target_date) AS min_date, MAX(target_date) AS max_date
FROM data_coverage
GROUP BY data_table, status
ORDER BY data_table, status;

-- Coverage says written but no physical daily observation row.
SELECT dc.*
FROM data_coverage dc
LEFT JOIN observations o
  ON dc.data_table='observations'
 AND dc.city=o.city
 AND dc.target_date=o.target_date
 AND dc.data_source=o.source
WHERE dc.data_table='observations'
  AND dc.status='WRITTEN'
  AND o.id IS NULL
LIMIT 200;

-- Physical daily observation with no written coverage row.
SELECT o.city, o.target_date, o.source, o.station_id, o.authority
FROM observations o
LEFT JOIN data_coverage dc
  ON dc.data_table='observations'
 AND dc.city=o.city
 AND dc.target_date=o.target_date
 AND dc.data_source=o.source
 AND dc.status='WRITTEN'
WHERE dc.city IS NULL
LIMIT 200;

-- Forecast coverage holes.
SELECT *
FROM data_coverage
WHERE data_table='forecasts' AND status IN ('MISSING','FAILED')
ORDER BY target_date, city, data_source
LIMIT 500;

-- Hourly v2 local-day row-count anomalies.
SELECT city, target_date, source, COUNT(*) AS n,
       SUM(temp_current IS NULL AND running_max IS NULL AND running_min IS NULL) AS no_temp_rows
FROM observation_instants_v2
GROUP BY city, target_date, source
HAVING n NOT BETWEEN 22 AND 26 OR no_temp_rows > 0
ORDER BY target_date, city
LIMIT 500;
