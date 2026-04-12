-- 真正的 calibration 有效样本量（按 decision group，而不是 pair rows）
WITH g AS (
  SELECT
      cluster,
      season,
      COUNT(*) AS pair_rows,
      COUNT(DISTINCT city || '|' || target_date || '|' || forecast_available_at) AS decision_groups,
      COUNT(DISTINCT city || '|' || target_date) AS settlement_days,
      SUM(outcome) AS positive_rows
  FROM calibration_pairs
  GROUP BY cluster, season
)
SELECT
    cluster,
    season,
    pair_rows,
    decision_groups,
    settlement_days,
    positive_rows,
    ROUND(1.0 * pair_rows / NULLIF(decision_groups, 0), 2) AS rows_per_group
FROM g
ORDER BY decision_groups DESC;
