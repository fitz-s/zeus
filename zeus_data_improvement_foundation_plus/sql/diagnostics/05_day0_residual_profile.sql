-- Day0 residual 学习集的小时画像
SELECT
    CAST(oi.local_hour AS INT) AS local_hour_bucket,
    COUNT(*) AS n,
    AVG(CASE WHEN s.settlement_value > oi.running_max THEN 1.0 ELSE 0.0 END) AS p_more_upside,
    AVG(s.settlement_value - oi.running_max) AS avg_residual_upside
FROM observation_instants oi
JOIN settlements s
  ON s.city = oi.city
 AND s.target_date = oi.target_date
WHERE oi.running_max IS NOT NULL
  AND oi.local_hour IS NOT NULL
  AND s.settlement_value IS NOT NULL
GROUP BY CAST(oi.local_hour AS INT)
ORDER BY local_hour_bucket;
