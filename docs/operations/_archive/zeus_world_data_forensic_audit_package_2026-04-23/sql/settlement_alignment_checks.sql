-- settlement_alignment_checks.sql
SELECT 'settlement_metric_distribution' AS check_name, temperature_metric, physical_quantity, observation_field, unit, COUNT(*) AS n
FROM settlements
GROUP BY temperature_metric, physical_quantity, observation_field, unit;

SELECT 'settlement_nulls' AS check_name,
       COUNT(*) AS n,
       SUM(market_slug IS NULL OR market_slug='') AS market_slug_null,
       SUM(settlement_value IS NULL) AS settlement_value_null,
       SUM(winning_bin IS NULL OR winning_bin='') AS winning_bin_null,
       SUM(pm_bin_lo IS NULL) AS bin_lo_null,
       SUM(pm_bin_hi IS NULL) AS bin_hi_null
FROM settlements;

-- Exact source equality is expected to fail because settlement_source is a URL and observations.source is a tag.
SELECT COUNT(*) AS exact_source_join_matches
FROM settlements s
JOIN observations o
  ON s.city=o.city
 AND s.target_date=o.target_date
 AND s.settlement_source=o.source;

SELECT s.city, s.target_date, s.settlement_source, s.settlement_value, s.unit,
       o.source AS obs_source, o.station_id, o.high_temp, o.low_temp, o.unit AS obs_unit
FROM settlements s
LEFT JOIN observations o
  ON s.city=o.city AND s.target_date=o.target_date
WHERE o.id IS NULL
LIMIT 100;

-- Heuristic station-in-source check. This is not a substitute for a registry.
SELECT COUNT(*) AS station_url_match_count
FROM settlements s
JOIN observations o
  ON s.city=o.city
 AND s.target_date=o.target_date
 AND o.station_id IS NOT NULL
 AND s.settlement_source LIKE '%' || o.station_id || '%';

SELECT s.city, s.target_date, s.settlement_source, s.settlement_value, s.unit,
       o.station_id, o.high_temp, o.low_temp, o.source
FROM settlements s
JOIN observations o
  ON s.city=o.city AND s.target_date=o.target_date
WHERE s.temperature_metric='high'
  AND s.settlement_value IS NOT NULL
  AND o.high_temp IS NOT NULL
  AND ABS(s.settlement_value - o.high_temp) > 0.49
ORDER BY s.city, s.target_date
LIMIT 200;
