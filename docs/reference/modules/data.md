# Data Module Authority Book

**Recommended repo path:** `docs/reference/modules/data.md`
**Current code path:** `src/data`
**Authority status:** Dense module reference for source routing, ingestion, observation/forecast/market data capture, and source-discipline enforcement.

## 1. Module purpose
Bind the real-world feeds Zeus depends on to the correct semantic roles: settlement daily observations, day0/current observations, historical hourly features, forecast snapshots, forecast-skill rows, market book data, and ingestion health.

## 2. What this module is not
- Not the owner of contract semantics or runtime lifecycle law.
- Not a license to pick whichever provider responds first.
- Not a place where a successful HTTP 200 can stand in for source correctness.

## 3. Domain model
- Settlement daily observations (WU/HKO/Ogimet-proxy).
- Current/Day0 observation client chain.
- Historical hourly observation truth and v2 migration.
- Forecast appenders and forecast-skill/backfill paths.
- Market scanner/Polymarket compatibility client and ingestion health/quota/proxy behavior.

## 4. Runtime role
Feeds the rest of Zeus with source-bound real-world data and continuously mediates among provider differences, quotas, fallback behavior, and city-specific truth contracts.

## 5. Authority role
Data does not set durable law, but it is the place where semantic category errors become real rows. The current architecture law explicitly warns that source role, station/product/date mapping, and truth-plane collapse are among the most expensive failures Zeus can have.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/authority/zeus_current_architecture.md` truth planes and feed-role matrix
- `docs/operations/current_source_validity.md` for current routing facts
- `architecture/city_truth_contract.yaml` and `config/cities.json`
- `src/data/daily_obs_append.py`, `observation_client.py`, `hourly_instants_append.py`, `forecasts_append.py`, `market_scanner.py`, new v2 hourly client/writer files

### Non-authority surfaces
- Provider availability by itself
- Historical fallback code paths that survived migrations but are no longer current routing truth
- Open-Meteo or generic grid feeds used as semantic proof of settlement truth

## 7. Public interfaces
- Daily observation writers and backfill entrypoints
- Current observation client APIs
- Hourly historical clients/writers for `observation_instants_v2`
- Forecast append/backfill surfaces
- Market/venue data clients. `polymarket_client.py` is now a compatibility
  wrapper for callers that still patch/use `PolymarketClient`; live
  placement/cancel/order queries delegate to `src/venue/polymarket_v2_adapter.py`.
  R3 A2-selected order types must pass through this wrapper unchanged so
  maker/taker policy reaches live adapter submit calls.
  pUSD balances now return through CollateralLedger snapshots rather than raw
  USDC.e floats.
- Proxy/quota/validator helpers

## 8. Internal seams
- Settlement daily vs current/day0 vs historical hourly vs forecast-skill truth
- Client fetchers vs typed writers
- Quota/proxy behavior vs source-correctness logic
- Legacy vs v2 observation and forecast surfaces

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `daily_obs_append.py` | Settlement-adjacent daily observation writer; one of the highest-risk data files. |
| `observation_client.py` | Current observation chain for runtime/Day0 context. |
| `hourly_instants_append.py` | Legacy hourly path; needs explicit status in rehydrated docs. |
| `wu_hourly_client.py / ogimet_hourly_client.py / observation_instants_v2_writer.py / tier_resolver.py` | New same-source-as-settlement hourly migration stack. |
| `forecast_source_registry.py / forecast_ingest_protocol.py` | R3 F1 typed forecast-source registry, dormant operator gates, and source-stamped bundle protocol. |
| `release_calendar.py` | PR45b read-only source release-calendar query layer; answers safe-fetch/backfill-only decisions from `config/source_release_calendar.yaml` without live side effects. |
| `tigge_client.py` | R3 F3 dormant TIGGE ingest adapter; construction is safe with gate closed, and open-gate fetch reads only an operator-approved local JSON payload configured by constructor, `ZEUS_TIGGE_PAYLOAD_PATH`, or `payload_path:` in the decision artifact. |
| `forecasts_append.py / ensemble_client.py / ecmwf_open_data.py / openmeteo_client.py` | Forecast and forecast-support ingest family; new forecast rows stamp `source_id`, `raw_payload_hash`, `captured_at`, and `authority_tier`. |
| `market_scanner.py / polymarket_client.py` | Venue/executable-context inputs; live order side effects route through the V2 venue adapter; balance compatibility configures CollateralLedger with pUSD. `polymarket_client.py` preserves A2-selected `order_type` on the adapter boundary. |
| `proxy_health.py / openmeteo_quota.py / ingestion_guard.py / rebuild_validators.py / hole_scanner.py` | Operational safety/guardrail surfaces. |

## 10. Relevant tests
- tests/test_audit_city_data_readiness.py
- tests/test_cities_config_authoritative.py
- tests/test_ensemble_client.py
- tests/test_backfill_openmeteo_previous_runs.py
- tests/test_etl_forecasts_v2_from_legacy.py
- tests/test_backfill_scripts_match_live_config.py
- tests/test_tier_resolver.py
- tests/test_obs_v2_writer.py
- tests/test_hk_rejects_vhhh_source.py
- tests/test_hourly_clients_parse.py
- tests/test_release_calendar.py

## 11. Invariants
- Settlement source is not inferred from endpoint availability or nearest station convenience.
- Hourly aggregation must preserve extrema needed by the target metric.
- Hong Kong HKO truth must not be silently rewritten as VHHH/WU airport truth.
- Current observation fallback chains must not reintroduce ghost-trade source errors.
- Legacy and v2 data families must be explicitly versioned.

## 12. Negative constraints
- Never make Open-Meteo or another grid source a hidden default for settlement-adjacent logic.
- Never promote archives or old packet notes into current source routing without current audit.
- Never let quota/proxy workarounds change semantic provider selection.
- Never assume `config/cities.json` alone is the full current-routing truth; combine it with current-source audits and city truth contract.

## 13. Known failure modes
- HTTP success from the wrong station/provider is mistaken for semantic correctness.
- Sub-hourly extrema are lost by snap-to-hourly logic, poisoning historical features and day0 thinking.
- Fallbacks or old scripts continue writing stale source families after routing migrations.
- Current fact docs go stale and agents trust them as timeless truth.
- City-specific station exceptions (Hong Kong, noaa-proxy cities, multi-airport cities) are forgotten.

## 14. Historical failures and lessons
- [Archive evidence] `docs/archives/audits/legacy_audit_truth_surfaces.md` and other data audits show how stale/forked data surfaces accumulate if lifecycle and source discipline are weak.
- [Archive evidence] `docs/archives/data-rebuild-2026-04-13/zeus-understanding-settlement.md` and related materials reinforce that data rebuild success is not the same as semantic certification.
- [Archive evidence] The recent HK/VHHH category-error episode is a live reminder: correct provider family is not enough; correct station/product identity matters.

## 15. Code graph high-impact nodes
- `src/data/daily_obs_append.py` and `observation_client.py` — semantically central and likely high-degree.
- `src/data/hourly_instants_append.py` plus the new v2 hourly stack — current migration hotspot.
- `src/data/forecasts_append.py` and `ensemble_client.py` — bridge forecast/training/runtime data.

## 16. Likely modification routes
- Source-routing change: read current architecture, current_source_validity, city_truth_contract, and relevant packet history first.
- Hourly/data-version change: review signal, calibration, current_data_state, and tests together.
- Fallback-chain change: prove both capability-present and capability-absent behavior.

## 17. Planning-lock triggers
- Any change to source routing, settlement provider mapping, fallback order, observation/forecast writers, or current-fact surfaces that gate data work.
- Any addition/removal of a city/provider/station mapping.
- Any change to observation_instants_v2 / historical_forecasts_v2 write contracts.

## 18. Common false assumptions
- A 200 response means the source is correct.
- Forecast-skill truth, historical hourly truth, day0 truth, and settlement truth are the same feed with different timestamps.
- Open-Meteo/grid sources are safe if calibration can learn the bias.
- Current data/source docs can be manually maintained forever.

## 19. Do-not-change-without-checking list
- Current observation fallback order
- Daily settlement writer source routing
- Hourly v2 source-tier mapping and write antibodies
- Any source-tag strings consumed by tests/writers/manifests
- Forecast-source registry IDs consumed by `forecasts_append.py`,
  `ensemble_client.py`, and `hole_scanner.py`

## 20. Verification commands
```bash
pytest -q tests/test_audit_city_data_readiness.py tests/test_cities_config_authoritative.py tests/test_backfill_scripts_match_live_config.py
pytest -q tests/test_tier_resolver.py tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_hourly_clients_parse.py
pytest -q tests/test_ensemble_client.py tests/test_etl_forecasts_v2_from_legacy.py
python -m py_compile src/data/*.py scripts/backfill_obs_v2.py scripts/audit_observation_instants_v2.py
```

## 21. Rollback strategy
Rollback data packets with write-contract, source-tag, and test updates together. If rows were written, either quarantine by data_version/source tag or provide explicit cleanup SQL.

## 22. Open questions
- Which legacy data files are now compatibility-only and should be marked that way in source_rationale/module manifest?
- Should current-data/current-source docs become generated summaries to avoid hidden stale bombs?

## 23. Future expansion notes
- Add per-city/date source-routing appendices generated from `city_truth_contract.yaml`.
- Expose graph-derived hub/bridge info for data writers and their test coverage.

## 24. Rehydration judgement
This book is the dense reference layer for data. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
