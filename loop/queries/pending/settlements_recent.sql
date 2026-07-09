-- Standing probe (re-runs every tick): VERIFIED settlements joined to real
-- held terminal positions and their immutable decision-q certificates.
-- This is the loop's grading substrate: decision certificate × settlement join.
SELECT
  so.settlement_id,
  so.city,
  so.target_date,
  so.temperature_metric,
  so.market_slug AS settlement_market_slug,
  so.winning_bin,
  so.settlement_value,
  so.settlement_unit,
  so.settled_at,
  so.recorded_at,
  pc.position_id,
  pc.phase,
  pc.condition_id,
  pc.direction,
  pc.bin_label,
  pc.entry_price,
  pc.shares,
  pc.cost_basis_usd,
  pc.realized_pnl_usd,
  pc.strategy_key,
  pc.updated_at AS position_updated_at,
  MIN(pe.occurred_at) AS entry_occurred_at,
  ela.audit_id,
  ela.avg_fill_price,
  ela.filled_size,
  ela.fees,
  ela.pnl_usd AS audit_pnl_usd,
  ela.expected_edge_source_certificate_hash,
  dc.certificate_id AS decision_certificate_id,
  dc.certificate_hash AS decision_certificate_hash,
  dc.decision_time AS decision_certificate_time,
  dc.payload_json AS decision_payload_json
FROM trades.position_current pc
JOIN forecasts.market_events me
  ON me.condition_id = pc.condition_id
JOIN forecasts.settlement_outcomes so
  ON so.city = me.city
 AND so.target_date = me.target_date
 AND so.temperature_metric = me.temperature_metric
 AND so.authority = 'VERIFIED'
LEFT JOIN trades.position_events pe
  ON pe.position_id = pc.position_id
 AND pe.event_type IN ('POSITION_OPEN_INTENT', 'ENTRY_ORDER_POSTED', 'ENTRY_ORDER_FILLED')
LEFT JOIN world.edli_live_profit_audit ela
  ON ela.condition_id = pc.condition_id
 AND ela.direction = pc.direction
 AND ela.expected_edge_source_certificate_hash IS NOT NULL
 AND ela.expected_edge_source_certificate_hash <> ''
LEFT JOIN world.decision_certificates dc
  ON dc.certificate_hash = ela.expected_edge_source_certificate_hash
 AND dc.certificate_type = 'ActionableTradeCertificate'
 AND dc.mode = 'LIVE'
 AND dc.verifier_status = 'VERIFIED'
WHERE pc.phase IN ('settled', 'economically_closed', 'admin_closed')
  AND pc.entry_price IS NOT NULL
  AND pc.direction IS NOT NULL
  AND pc.condition_id IS NOT NULL
  AND so.recorded_at >= datetime('now', '-5 days')
GROUP BY pc.position_id, so.settlement_id, ela.audit_id, dc.certificate_id
ORDER BY so.recorded_at DESC, pc.position_id
