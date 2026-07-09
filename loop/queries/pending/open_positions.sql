-- Standing probe (re-runs every tick): current open positions snapshot.
-- Orientation only (what is at risk right now); never evidence for an
-- edge claim (runtime-derived numbers are banned as evidence — ledger law).
SELECT position_id, city, target_date, temperature_metric, direction,
       bin_label, size_usd, entry_price, phase, realized_pnl_usd, updated_at
FROM trades.position_current
WHERE phase IN ('active', 'day0_window', 'pending_exit')
ORDER BY updated_at DESC
LIMIT 50
