-- 找出“不完整 bin set”的 calibration group
SELECT
    city,
    target_date,
    forecast_available_at,
    COUNT(*) AS n_rows,
    SUM(outcome) AS positives
FROM calibration_pairs
GROUP BY city, target_date, forecast_available_at
HAVING n_rows <> 11 OR positives <> 1
ORDER BY city, target_date, forecast_available_at;
