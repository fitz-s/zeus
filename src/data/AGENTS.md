# src/data AGENTS — Zone K2/K3 (Data Ingestion)

Module book: `docs/reference/modules/data.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

Data is the **truth-binding layer** — where physical atmospheric reality enters
Zeus as tradeable information. Every feed serves exactly one truth plane
(settlement, Day0, hourly, forecast-skill). Collapsing these roles is the
#1 catastrophic failure class in Zeus, because the system will silently trade
on wrong source truth with no runtime error.

The money path starts here: if the source is wrong, every downstream
computation (P_raw, calibration, edge, sizing) is economically worthless
regardless of mathematical correctness.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `daily_obs_append.py` | Settlement-adjacent daily observation writer | CRITICAL — settlement truth enters here |
| `observation_client.py` | Current observation chain (Day0 monitoring) | HIGH — Day0 truth source |
| `tier_resolver.py` | Source-tier routing (which provider for which role) | HIGH — wrong tier = wrong truth plane |
| `hourly_instants_append.py` | Legacy hourly observation path | HIGH — extrema preservation |
| `observation_instants_v2_writer.py` | V2 hourly write contract | HIGH — version/source tagging |
| `forecasts_append.py` | Forecast write family (TIGGE ENS) | HIGH — signal source |
| `market_scanner.py` | Venue discovery path (Polymarket markets) | MEDIUM |
| `polymarket_client.py` | Market data client (book, prices) | MEDIUM |

## Domain rules

- **Feed roles are non-fungible.** Settlement daily source ≠ Day0 monitoring
  source ≠ historical hourly source ≠ forecast-skill source. Each serves a
  distinct truth plane defined in `zeus_current_architecture.md` §4–§6.
- **Endpoint availability is not source correctness.** A 200 response from
  an API does not prove the station, product, date mapping, or settlement
  semantics are correct for the target city.
- **Provider selection is audit-bound, not code-inferred.** The correct
  settlement source for a city comes from `current_source_validity.md` and
  `city_truth_contract.yaml`, not from what endpoint is reachable.
- **Hourly aggregation must preserve the extrema** required by the target
  metric: daily max for high track, daily min for low track. Averaging or
  sampling destroys the signal.
- **Quota/proxy workarounds must not silently change semantic provider.**
  If a rate limit forces a fallback, it must be recorded as a degradation,
  not treated as equivalent source truth.
- **WU/HKO settlement observations settle on integers.** All settlement-path
  writes must flow through `SettlementSemantics` (INV-06).

## Common mistakes

- Using an airport station code as the city settlement station without proving
  WU uses that station for the city's displayed temperature
- Treating Open-Meteo (grid reanalysis) as settlement-adjacent — it is never
  a settlement source, only a calibration/feature backup
- Collapsing Day0 monitoring source with final daily settlement source — they
  may differ in station, timing, and aggregation method
- Aggregating hourly observations without checking whether the peak/trough
  falls in the correct local calendar day (DST case study in domain model §15)
- `config/cities.json` provides runtime config seeds, NOT current source
  validity. Always cross-reference with `current_source_validity.md`
- Adding new data sources without updating `city_truth_contract.yaml` and
  `source_rationale.yaml` — makes the source invisible to future agents

## Required tests

- `tests/test_audit_city_data_readiness.py`
- `tests/test_cities_config_authoritative.py`
- `tests/test_ensemble_client.py`
- `tests/test_backfill_openmeteo_previous_runs.py`
- `tests/test_etl_forecasts_v2_from_legacy.py`
- `tests/test_backfill_scripts_match_live_config.py`
- `tests/test_tier_resolver.py`
- `tests/test_obs_v2_writer.py`
- `tests/test_hk_rejects_vhhh_source.py`
- `tests/test_hourly_clients_parse.py`

## Planning lock

Any change to source routing, fallback order, city/provider mapping, observation
or forecast writers, or v2 write contracts requires a packet and planning-lock
evidence.
