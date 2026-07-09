-- Standing probe (re-runs every tick): settled outcomes, last 5 days.
-- Feeds the tick's settlement-grading step (JOURNAL cursor = max recorded_at
-- already graded). VERIFIED authority only — the ground-truth side of the
-- decision-certificate × settlement join.
SELECT settlement_id, city, target_date, temperature_metric, market_slug,
       winning_bin, settlement_value, settled_at, recorded_at, authority
FROM forecasts.settlement_outcomes
WHERE authority = 'VERIFIED'
  AND recorded_at >= datetime('now', '-5 days')
ORDER BY recorded_at DESC
