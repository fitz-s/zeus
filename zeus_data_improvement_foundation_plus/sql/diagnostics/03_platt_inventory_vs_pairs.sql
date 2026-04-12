-- active Platt inventory 与当前 calibration_pairs 是否对齐
WITH pair_buckets AS (
  SELECT
      cluster || '_' || season AS bucket_key,
      COUNT(*) AS pair_rows,
      COUNT(DISTINCT city || '|' || target_date || '|' || forecast_available_at) AS decision_groups
  FROM calibration_pairs
  GROUP BY 1
)
SELECT
    m.bucket_key,
    m.n_samples,
    m.brier_insample,
    m.fitted_at,
    COALESCE(p.pair_rows, 0) AS pair_rows_now,
    COALESCE(p.decision_groups, 0) AS decision_groups_now
FROM platt_models m
LEFT JOIN pair_buckets p USING (bucket_key)
WHERE m.is_active = 1
ORDER BY m.bucket_key;
