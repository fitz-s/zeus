-- 统一审计 probability truth surface
SELECT
    'opportunity_fact' AS table_name,
    COUNT(*) AS rows,
    SUM(CASE WHEN p_raw IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_raw,
    SUM(CASE WHEN p_cal IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_cal,
    SUM(CASE WHEN p_market IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_market,
    SUM(CASE WHEN alpha IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_alpha
FROM opportunity_fact
UNION ALL
SELECT
    'trade_decisions' AS table_name,
    COUNT(*) AS rows,
    SUM(CASE WHEN p_raw IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_raw,
    SUM(CASE WHEN p_calibrated IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_cal,
    SUM(CASE WHEN p_posterior IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_p_market,
    NULL AS nonnull_alpha
FROM trade_decisions
UNION ALL
SELECT
    'shadow_signals' AS table_name,
    COUNT(*) AS rows,
    SUM(CASE WHEN p_raw_json IS NOT NULL AND trim(p_raw_json) <> '' THEN 1 ELSE 0 END) AS nonnull_p_raw,
    SUM(CASE WHEN p_cal_json IS NOT NULL AND trim(p_cal_json) <> '' THEN 1 ELSE 0 END) AS nonnull_p_cal,
    SUM(CASE WHEN edges_json IS NOT NULL AND trim(edges_json) <> '' THEN 1 ELSE 0 END) AS nonnull_p_market,
    NULL AS nonnull_alpha
FROM shadow_signals;
