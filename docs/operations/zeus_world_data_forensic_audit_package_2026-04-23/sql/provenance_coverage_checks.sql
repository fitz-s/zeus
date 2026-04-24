-- provenance_coverage_checks.sql
SELECT source, station_id, unit, authority,
       COUNT(*) AS n,
       MIN(target_date) AS min_date,
       MAX(target_date) AS max_date,
       SUM(provenance_metadata IS NULL OR provenance_metadata='' OR provenance_metadata='{}') AS provenance_empty
FROM observations
GROUP BY source, station_id, unit, authority
ORDER BY provenance_empty DESC, n DESC;

SELECT settlement_source_type, unit, authority, temperature_metric, data_version,
       COUNT(*) AS n,
       SUM(provenance_json IS NULL OR provenance_json='' OR provenance_json='{}') AS provenance_empty,
       SUM(market_slug IS NULL OR market_slug='') AS market_slug_null
FROM settlements
GROUP BY settlement_source_type, unit, authority, temperature_metric, data_version
ORDER BY n DESC;

SELECT authority, data_version,
       COUNT(*) AS n,
       COUNT(DISTINCT source) AS sources,
       COUNT(DISTINCT city) AS cities,
       SUM(provenance_json IS NULL OR provenance_json='' OR provenance_json='{}') AS provenance_empty,
       SUM(station_id IS NULL OR station_id='') AS station_null
FROM observation_instants_v2
GROUP BY authority, data_version
ORDER BY n DESC;

-- Canonical readiness should require this query to return zero.
SELECT *
FROM observations
WHERE authority='VERIFIED'
  AND (provenance_metadata IS NULL OR provenance_metadata='' OR provenance_metadata='{}')
LIMIT 100;
