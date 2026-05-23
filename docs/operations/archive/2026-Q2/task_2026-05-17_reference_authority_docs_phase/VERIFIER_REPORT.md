# Reality Contract Verifier Report

Generated: 2026-05-17T04:52:04+00:00

**Summary**: 2 PASS | 0 FAIL | 23 UNMAPPED_NEEDS_OPERATOR

## PASS (last_verified updated)

- `data.yaml::NOAA_TIME_SCALE`: Open-Meteo timezone='GMT' — UTC confirmed
  - old: `2026-04-06T00:00:00+00:00` → new: `2026-05-17T04:52:04+00:00`
- `protocol.yaml::WEBSOCKET_REQUIRED`: TCP connection to ws-subscriptions-clob.polymarket.com:443 succeeded
  - old: `2026-04-06T00:00:00+00:00` → new: `2026-05-17T04:52:04+00:00`

## Addendum 2026-05-17 (verifier strengthening)

Strengthened verifier executed on 2026-05-17 with real WS handshake (websockets library) + NOAA time-suffix check:

- `protocol.yaml::WEBSOCKET_REQUIRED`: WS handshake succeeded for wss://ws-subscriptions-clob.polymarket.com/ws/market — last_verified update RETAINED.
- `data.yaml::NOAA_TIME_SCALE`: FAIL — Open-Meteo returns time strings without Z/+00:00 suffix (e.g. '2026-05-17T00:00'); timezone field is 'GMT' (OK) but suffix check fails. last_verified update REVERTED to pre-PR value '2026-04-06T00:00:00+00:00'. Operator must re-verify once Open-Meteo suffix behavior is confirmed or verification_method is updated.

## UNMAPPED_NEEDS_OPERATOR

These contracts cannot be verified by automated read-only API calls.
Operator must re-verify manually and update `last_verified` in the YAML.

- `data.yaml::SETTLEMENT_SOURCE_NYC`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_CHICAGO`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_ATLANTA`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_MIAMI`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_DALLAS`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_AUSTIN`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_HOUSTON`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_SEATTLE`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_LOS_ANGELES`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_SAN_FRANCISCO`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_DENVER`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_LONDON`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_PARIS`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_SEOUL`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_SHANGHAI`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::SETTLEMENT_SOURCE_TOKYO`: UNMAPPED_NEEDS_OPERATOR: requires live Polymarket market resolution lookup with specific settled market ID — operator must check WU citation in market settlement page
- `data.yaml::GAMMA_CLOB_PRICE_CONSISTENCY`: UNMAPPED_NEEDS_OPERATOR: requires active token_id for current weather market — operator must compare Gamma vs CLOB prices for an active market
- `economic.yaml::FEE_RATE_WEATHER`: UNMAPPED_NEEDS_OPERATOR: requires active token_id for GET /fee-rate — operator must query per-market feeSchedule with live token
- `economic.yaml::MAKER_REBATE_RATE`: UNMAPPED_NEEDS_OPERATOR: requires active token_id for GET /fee-rate — operator must query per-market feeSchedule with live token
- `execution.yaml::TICK_SIZE_STANDARD`: UNMAPPED_NEEDS_OPERATOR: requires active token_id for GET /book — operator must query per-market tick_size/min_order_size with live token
- `execution.yaml::MIN_ORDER_SIZE_SHARES`: UNMAPPED_NEEDS_OPERATOR: requires active token_id for GET /book — operator must query per-market tick_size/min_order_size with live token
- `protocol.yaml::RATE_LIMIT_BEHAVIOR`: UNMAPPED_NEEDS_OPERATOR: rate limit verification requires observing 429 responses under load or checking py-clob-client changelog — not automatable read-only
- `protocol.yaml::RESOLUTION_TIMELINE`: UNMAPPED_NEEDS_OPERATOR: resolution timeline verification requires manual review of docs.polymarket.com/trading/resolution and recent market resolution timestamps

