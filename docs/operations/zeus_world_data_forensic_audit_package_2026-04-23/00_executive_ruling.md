# 00 Executive Ruling — Zeus World Data Forensic Audit

## Ruling

`zeus-world.db` is **not currently fit to serve as Zeus's complete source-of-truth for live trading, causal replay, calibration, or exact settlement reconstruction**. It is **partially structurally sound as an evidence database**: it contains a large hourly/daily observation corpus, a coverage ledger, and meaningful v2 scaffolding. But the populated data does not satisfy the external standard required by a weather-based quantitative trading machine. The current database is best classified as: **useful as evidence plus incomplete v2 scaffolding; semantically unsafe as canonical training/runtime truth until provenance, settlement, source-role, and forecast-causality hardening are applied.**

## Trustworthiness by use case

| Use case | Ruling | Why |
|---|---|---|
| Live trading source-of-truth | **Unsafe now** | Live paths can fall back across WU/IEM/Open-Meteo style sources; DB does not encode enough source-role eligibility to prevent fallback contamination. |
| Replay | **Unsafe now** | `market_events_v2`, `market_price_history`, `probability_trace_fact`, `replay_results`, forecast, ensemble, and calibration tables are empty. |
| Calibration/training | **Unsafe now** | Forecast/ensemble/calibration spine is empty; observation rows lack enough training/causality flags; WU daily provenance is empty for 39,431 rows. |
| Settlement reconstruction | **Partially useful as evidence only** | Legacy `settlements` has 1,561 high-only rows, but `settlements_v2` is empty, `market_slug` is null for all settlement rows, and unique key is city/date rather than city/date/metric/market. |
| Observation evidence | **Partially useful** | `observation_instants_v2` has 1,813,662 rows with non-empty provenance and time fields, but still lacks metric/training/causality fields and mixes primary/fallback sources under the same data version. |

## Top 10 critical findings

1. **Forecast/training/replay spine is empty.** `forecasts`, `historical_forecasts(_v2)`, `ensemble_snapshots(_v2)`, `calibration_pairs(_v2)`, `platt_models(_v2)`, `market_events(_v2)`, `replay_results`, and trading fact tables are all zero rows.
2. **Coverage ledger confirms the missing forecast lane.** `data_coverage` records 200,481 `forecasts` rows as `MISSING` and only `LEGITIMATE_GAP` entries otherwise.
3. **Settlement v2 canonical target is empty.** `settlements_v2` has 0 rows. Legacy `settlements` is high-only and unique on `(city,target_date)`, which is incompatible with dual high/low markets.
4. **Legacy settlements are not exact market replay truth.** All 1,561 settlement rows have `market_slug` null; 92 have no winning bin and 49 have null settlement value.
5. **WU daily observations carry false confidence.** `observations` has 42,749 rows, but 39,431 rows have empty `provenance_metadata` despite most WU rows being stamped `VERIFIED`.
6. **Hourly legacy table is lossy evidence only.** `hourly_observations` has 1,813,568 rows but no timezone, station, UTC timestamp, provenance, authority, data version, ambiguity, or source-role columns.
7. **`observation_instants_v2` is better but still incomplete.** It has 1,813,662 rows with non-empty provenance, but no `temperature_metric`, `physical_quantity`, `observation_field`, `training_allowed`, or `causality_status` columns.
8. **Current hourly view mixes source families.** `observation_instants_current` selects data version `v1.wu-native` and includes 68 sources, including fallbacks, not a single settlement-authority source family.
9. **Settlement/source identity is not join-safe.** Direct source equality between `settlements.settlement_source` URL and `observations.source` tag has zero matches; only station parsing can recover most alignments.
10. **Forecast issue-time causality is not populated.** Code paths for Open-Meteo previous-runs and historical forecast ETL can leave or reconstruct issue/availability times; actual forecast rows are absent, so point-in-time replay cannot be validated from this DB.

## Top 10 structural fixes

1. Add a read-only readiness gate that hard-fails live/replay/calibration when required canonical tables are empty or unsafe.
2. Migrate settlement truth to `settlements_v2` keyed by `(market_id or condition_id, city, target_date, temperature_metric)` with source URL, station, finalization timestamp, revision policy, unit, bin rule, and provenance hash.
3. Retrofit `observations` and `observation_instants_v2` with explicit `source_role`, `temperature_metric`, `physical_quantity`, `observation_field`, `training_allowed`, and `causality_status`.
4. Split primary settlement authority, model/fallback evidence, and gridded/model data into separate eligibility views.
5. Populate forecast/ensemble v2 tables only with authoritative `issue_time`, `available_at`, `fetch_time`, model version, member unit/precision, and local-day extraction metadata.
6. Add downstream guards so calibration/replay cannot use legacy `hourly_observations` or fallback source rows unless explicitly allowed.
7. Rework backfill scripts to dry-run by default, produce completeness manifests, and reject silent partial windows.
8. Add per-market station/source registry rather than deriving market semantics from city/date.
9. Store raw source payload hashes and parsing version for all backfilled settlement/observation rows.
10. Add regression tests that seed unsafe rows and prove consumers reject them, not merely that tables exist.

## What is acceptable

- The DB schema contains serious v2 intent: `ensemble_snapshots_v2`, `calibration_pairs_v2`, `settlements_v2`, source/time/authority fields, and provenance-aware writer code.
- `observation_instants_v2` is materially stronger than the legacy hourly table: it records UTC/local time, timezone, offset, DST flags, station/source, data version, authority, and non-empty provenance.
- `data_coverage` is useful because it records missingness as first-class evidence rather than pretending row absence is unknowable.
- `settlements` is useful as high-temperature settlement evidence for some markets, not as exact market replay truth.

## What is dangerous

- Treating any populated row family as canonical without checking source role, metric, authority, data version, provenance, causality, and market rule identity.
- Rebuilding calibration from legacy or fallback rows because “some observation exists.”
- Using `hourly_observations` for anything other than compatibility/evidence.
- Treating HKO decimal observation data and Polymarket integer settlement outcomes as the same semantic object.
- Treating Open-Meteo, Meteostat, or Ogimet fallback data as settlement-authority truth unless the market explicitly says so.