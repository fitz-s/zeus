-- bias / mae / mse 立方体，给 bias correction / EMOS 用
SELECT
    city,
    season,
    source,
    lead_days,
    COUNT(*) AS n,
    AVG(error) AS bias,
    AVG(ABS(error)) AS mae,
    AVG(error * error) AS mse
FROM forecast_skill
GROUP BY city, season, source, lead_days
ORDER BY city, source, lead_days, season;
