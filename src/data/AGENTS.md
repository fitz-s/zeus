# src/data AGENTS

Data binds real-world feeds to the correct Zeus truth planes: settlement daily
observations, current/Day0 observations, historical hourly features, forecast
snapshots, forecast-skill rows, and market data.

## Read this before editing

- Module book: `docs/reference/modules/data.md`
- Machine registry: `architecture/module_manifest.yaml`
- Source-role law and current facts: `docs/authority/zeus_current_architecture.md`,
  `architecture/city_truth_contract.yaml`, `config/cities.json`,
  `docs/operations/current_source_validity.md`, `docs/operations/current_data_state.md`

## Top hazards

- endpoint availability is not source correctness
- settlement daily, Day0 current, historical hourly, and forecast-skill truth
  must not collapse into one feed family
- quota/proxy workarounds must not silently change semantic provider selection
- hourly aggregation and v2 migration paths can destroy extrema or source tags

## Canonical truth surfaces

- `daily_obs_append.py`
- `observation_client.py`
- `hourly_instants_append.py`
- `observation_instants_v2_writer.py`
- `tier_resolver.py`
- `forecasts_append.py`
- `market_scanner.py`

## High-risk files

| File | Role |
|------|------|
| `daily_obs_append.py` | settlement-adjacent daily observation writer |
| `observation_client.py` | current observation chain |
| `hourly_instants_append.py` | legacy hourly path |
| `observation_instants_v2_writer.py` | v2 hourly write contract |
| `tier_resolver.py` | source-tier routing |
| `forecasts_append.py` | forecast write family |
| `market_scanner.py` | venue discovery path |
| `polymarket_client.py` | market data client |

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

## Do not assume

- a 200 response means the source/station is semantically correct
- current source facts can be inferred from code comments alone
- Open-Meteo or another grid source is safe for settlement-adjacent logic
- legacy and v2 write families can coexist without explicit version/source tags

## Planning lock

Any change to source routing, fallback order, city/provider mapping, observation
or forecast writers, or v2 write contracts requires a packet and planning-lock
evidence.
