# src/data AGENTS — Zone K3 (Math/Data)

## WHY this zone matters

Data clients fetch external inputs: ECMWF ensemble forecasts, Polymarket prices, Weather Underground observations. These are Zeus's eyes — if data is stale, missing, or corrupted, every downstream decision is wrong.

Data availability is **first-class truth** (INV-09). Missing or rate-limited data must be represented explicitly, never silently skipped.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `ecmwf_open_data.py` | ECMWF ENS 51-member fetch + DB storage | HIGH — primary forecast source |
| `ensemble_client.py` | Ensemble data retrieval from DB | MEDIUM |
| `polymarket_client.py` | CLOB API: prices, orderbook, positions | HIGH — market data + execution |
| `market_scanner.py` | Market discovery and scanning | MEDIUM |
| `wu_daily_collector.py` | Weather Underground daily observations | HIGH — settlement source |
| `observation_client.py` | Observation data retrieval | MEDIUM |
| `openmeteo_quota.py` | Open-Meteo rate limit management | LOW |

## Domain rules

- ENS data has 51 members × 7 step hours (24–168h) per cycle — verify member count on ingest
- Rate limiting (429 errors) is common for ECMWF/Open-Meteo — must handle gracefully, not crash
- WU observations are settlement truth — data quality here directly impacts P&L
- All data fetches should record `availability_fact` entries (INV-09)
- External scripts live in `51 source data/scripts/` (sibling directory, not in repo)

## Common mistakes

- Silently returning empty arrays on fetch failure → downstream thinks "no data" instead of "data unavailable"
- Not recording availability facts → learning pipeline can't distinguish "no opportunity" from "data gap"
- Hardcoding API endpoints or credentials → use config
- Ignoring timezone conversion when matching ENS forecast hours to local target dates
