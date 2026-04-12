-- 看 execution / market microstructure 是否足够进入建模阶段
SELECT
    city,
    COUNT(*) AS price_logs,
    COUNT(DISTINCT target_date) AS target_days,
    COUNT(DISTINCT token_id) AS token_count,
    AVG(CASE WHEN spread IS NOT NULL THEN spread END) AS avg_spread
FROM token_price_log
GROUP BY city
ORDER BY price_logs DESC;
