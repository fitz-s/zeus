-- 用 settlement anomaly 估一个最朴素的城市相关 seed
WITH base AS (
  SELECT
      city,
      target_date,
      CAST(substr(target_date, 6, 2) AS INTEGER) AS month_num,
      settlement_value
  FROM settlements
  WHERE settlement_value IS NOT NULL
),
monthly_mean AS (
  SELECT city, month_num, AVG(settlement_value) AS month_mean
  FROM base
  GROUP BY city, month_num
),
anomaly AS (
  SELECT
      b.city,
      b.target_date,
      b.settlement_value - m.month_mean AS anomaly
  FROM base b
  JOIN monthly_mean m
    ON m.city = b.city
   AND m.month_num = b.month_num
)
SELECT * FROM anomaly
ORDER BY target_date, city;
