# Data We Have

Updated: `2026-04-01`

## Executive Summary

The data system is now split into two layers:

- `rainstorm.db`: primary operational store for settlement truth, observations, forecast rows, solar data, and DST-safe hourly timing.
- `51 source data`: ensemble workspace for `TIGGE`, `ECMWF Open Data`, raw regional files, city-member vectors, manifests, and scan artifacts.

The automated ingestion loop is active across:

- `TIGGE`
- `ECMWF Open Data`
- `WU`
- `HKO`
- `solar sync`
- `unresolved settlement-source probe`

The main unfinished area is still historical `TIGGE` density across all dates and leads. Everything else is already in a usable state.

## Current Transmission Status

Latest cycle launch:

- `generated_at = 2026-04-01T09:45:54Z`
- `TIGGE` workers launched: `10`
- `ECMWF Open Data` workers launched: `1`
- `WU` workers launched: `1`
- `HKO` workers launched: `1`
- `solar sync` workers launched: `1`
- `source probe` workers launched: `1`

Current selected targets in the latest cycle:

- `TIGGE`: `2026-02-28 -> 2026-03-09`, `step=168`
- `Open Data`: `2026-04-01`, `steps=96/120/144/168`
- `WU`: `Denver, Paris, San Francisco, Los Angeles, Miami, Seoul, Shanghai, Chicago`
- `HKO`: `Hong Kong`

Note:

- `gap_fill_cycle_last_run.json` is current.
- `gap_fill_cycle_supervisor_status.json` still shows an older waiting state, so use `last_run` as the authoritative launch snapshot.

## rainstorm.db

Current primary DB metrics:

| Metric | Value |
| --- | ---: |
| `settlements` | `1652` |
| `settlements.actual_temp_f non-null` | `1629` |
| `settlements.actual_temp_source = wu_daily_observed` | `1627` |
| `settlements.actual_temp_source = hko_daily_extract` | `0` |
| `forecasts` | `171003` |
| `observations` | `240234` |
| `hourly observations` | `219519` |
| `daily observations` | `20715` |
| `solar_times` | `31198` |
| `observation_instants` | `219483` |
| `wu_daily_observed rows` | `4136` |
| `hko_daily_extract rows` | `296` |

### Forecast Sources in DB

| Source | Rows |
| --- | ---: |
| `ecmwf_previous_runs` | `35518` |
| `gfs_previous_runs` | `35518` |
| `openmeteo_previous_runs` | `34939` |
| `icon_previous_runs` | `30502` |
| `ukmo_previous_runs` | `30148` |
| `noaa_forecast_archive` | `4370` |
| `noaa_ndfd_historical_forecast` | `8` |

### Observation Sources in DB

| Source | Rows |
| --- | ---: |
| `openmeteo_archive_hourly` | `114168` |
| `meteostat_hourly` | `105351` |
| `noaa_cdo_ghcnd` | `6520` |
| `iem_asos` | `4410` |
| `openmeteo_archive` | `4370` |
| `wu_daily_observed` | `4136` |
| `meteostat_daily_max` | `906` |
| `hko_daily_extract` | `296` |
| `noaa_observed` | `64` |
| `openmeteo_archive_harvester` | `13` |

### Settlement Truth Sources

| Source | Settlement Rows |
| --- | ---: |
| `wu_daily_observed` | `1627` |
| `iem_asos` | `2` |
| `NULL` | `23` |

The `NULL` settlement rows are the remaining unresolved truth promotions in `settlements`, not the absence of truth data overall.

## Exact Truth Coverage by City

Current `WU/HKO` truth depth by city:

| City | Exact Truth Rows |
| --- | ---: |
| `London` | `664` |
| `NYC` | `658` |
| `Austin` | `301` |
| `Dallas` | `298` |
| `Houston` | `298` |
| `Hong Kong` | `296` |
| `Tokyo` | `288` |
| `Shenzhen` | `210` |
| `Munich` | `206` |
| `Seattle` | `135` |
| `Atlanta` | `124` |
| `Shanghai` | `119` |
| `Seoul` | `118` |
| `Wellington` | `115` |
| `Buenos Aires` | `107` |
| `Chicago` | `104` |
| `Miami` | `100` |
| `Los Angeles` | `96` |
| `San Francisco` | `96` |
| `Denver` | `55` |
| `Paris` | `44` |

Interpretation:

- `London`, `NYC`, `Austin`, `Dallas`, `Houston`, `Tokyo`, `Hong Kong` are already deep enough to support serious replay and calibration work.
- `Denver`, `Paris`, `Los Angeles`, `San Francisco`, `Miami`, `Chicago`, `Atlanta`, `Seattle` still need more seasonal depth.

## Solar and DST Layer

This is now fully materialized in SQL.

| Metric | Value |
| --- | ---: |
| `solar_times rows` | `31198` |
| `cities covered` | `38` |
| `date range` | `2024-01-01 -> 2026-03-31` |

The solar layer includes:

- `sunrise_local`
- `sunset_local`
- `sunrise_utc`
- `sunset_utc`
- `timezone_name`
- `utc_offset_minutes`
- `dst_active`

The hourly DST-safe layer is now partially solved via `observation_instants`.

| Metric | Value |
| --- | ---: |
| `observation_instants rows` | `219483` |
| `cities covered` | `13` |
| `date range` | `2025-01-12 -> 2026-03-29` |
| `rows with dst_active=1` | `118984` |
| `ambiguous local hours flagged` | `20` |

Meaning:

- New and backfilled hourly data now has explicit local/UTC timestamps and DST state.
- This does **not** yet mean every consumer is fully rewritten to use UTC-first semantics, but the time layer now exists in the DB.

## 51 Source Data Workspace

Current raw workspace inventory:

| Dataset | Files | Size |
| --- | ---: | ---: |
| `tigge_ecmwf_ens` | `15976` | `133M` |
| `tigge_ecmwf_ens_regions` | `0` | `0B` |
| `ecmwf_open_ens` | `727` | `502M` |
| `solar` | `2` | `14M` |

Interpretation:

- `tigge_ecmwf_ens` still holds the existing per-city extracted member vectors.
- `tigge_ecmwf_ens_regions` is the new acceleration lane; it has been wired in, but the current launched worker set is still early enough that no retained region files are visible yet.
- `ecmwf_open_ens` is actively growing.

## TIGGE Coverage Status

The system has now switched from the old `81` anchor-date scan to a **daily** scan.

Current scan scope:

- `dates = 821`
- `steps = 7`
- `coverage_slots = 5747`

Current TIGGE status:

| Metric | Value |
| --- | ---: |
| `complete slots` | `56` |
| `gap slots` | `5691` |
| `completion` | `0.97%` |

This low completion ratio is expected because the scan scope is now:

- every day from `2024-01-01` to `2026-03-31`
- across `day1..day7`

So the denominator changed dramatically. The system is no longer measuring against a small anchor-date universe.

### TIGGE What Is Already Optimized

The following optimizations are already implemented:

- region batching
- multi-step batching
- multi-date batching
- gap-only city targeting
- incremental scan caching
- priority scheduling by liquidity / recency / lead
- faster supervisor cadence

This means the current TIGGE path is no longer the old `date -> step -> city` structure.

## ECMWF Open Data Status

Current scan snapshot:

| Metric | Value |
| --- | ---: |
| `coverage slots` | `7` |
| `complete slots` | `0` |
| `gap slots` | `7` |

This is a near-real-time rolling source. It is not a historical replay source.

Current role:

- bridge for current `day1..day7`
- near-real-time ensemble updates

## WU Coverage Status

Current WU seasonal scan covers `20` cities.

Cities with missing seasonal coverage include:

- `Atlanta`
- `Buenos Aires`
- `Chicago`
- `Denver`
- `Los Angeles`
- `Miami`
- `Munich`
- `Paris`
- `San Francisco`
- `Seattle`

Cities with weak seasonal coverage include:

- `Atlanta`
- `Austin`
- `Buenos Aires`
- `Chicago`
- `Dallas`
- `Houston`
- `Los Angeles`
- `Miami`
- `Munich`
- `Paris`

The current WU worker is correctly targeting thin seasonal cities, not randomly expanding already-strong cities.

## HKO Coverage Status

`Hong Kong` is the only HKO city.

Current HKO seasonal state:

- no missing seasons
- `spring` is still weak

This is a good state. HKO is now mostly a thickness problem, not a connectivity problem.

## What These Data Can Be Used For

### Option 1: Production Trading Base Layer

Use:

- `WU/HKO truth`
- `hourly observations`
- `observation_instants`
- `solar_times`
- `Open Data day1..day7`

Purpose:

- improve current production entry/exit
- stabilize day0/day1 decisions
- fix DST and solar alignment problems in live trading logic

### Option 2: Day0 Decay Rebuild

Use:

- `hourly observations`
- `observation_instants`
- `solar_times`
- `exact truth`
- `ensemble forecast ladder`

Purpose:

- replace simple local-hour decay with:
  - solar-time-aware decay
  - DST-safe decay
  - city/season-specific decay
  - ensemble-disagreement-aware decay

This is the highest-value modeling direction.

### Option 3: Lead Term-Structure Model

Use:

- `TIGGE`
- `ECMWF Open Data`
- existing forecast ladder in `rainstorm.db`

Purpose:

- build `day1..day7` error surfaces
- lead-aware bias and variance curves
- lead-aware regime detection

This directly improves pricing and hold/exit logic.

### Option 4: Feature Factory

Use:

- keep raw ensemble data in `51 source data`
- materialize only summary features back into the trading stack

Features to derive:

- member mean
- member std
- p10 / p50 / p90
- skew / tail thickness
- spread slope across leads
- lead-to-lead drift
- solar/DST features

This is the cleanest engineering pattern.

### Option 5: Portfolio / Regime Model

Use:

- cross-city `51` member vectors
- truth and seasonal replay

Purpose:

- identify correlated city regimes
- limit duplicated risk
- support multi-city portfolio sizing

This is useful after the single-city model stack is stable.

## Recommended Priority

1. Finish `WU/HKO` seasonal depth.
2. Continue `TIGGE` historical density buildout with the new accelerated path.
3. Use `observation_instants + solar_times` to rebuild the day0 decay model.
4. Materialize ensemble summary features back into the trading/replay stack.

## Bottom Line

We are no longer in a data-shortage state.

We now have:

- a thick truth layer
- a DST-safe hourly time layer
- a full solar layer
- a full near-real-time ensemble layer
- a historical `TIGGE` system that is now running on an accelerated architecture

The main unfinished work is not architecture discovery anymore. It is density completion, especially for:

- `TIGGE` historical daily coverage
- `WU/HKO` seasonal depth

## Data Utilization Status

### Fully load-bearing (no longer "unused")

| Asset | Status |
|-------|--------|
| `observation_instants` | Formal time-semantic main chain |
| `solar_daily` | Formal time-semantic main chain |
| hourly observations | Consumed via `observation_instants` → `diurnal_curves` / `diurnal_peak_prob` |
| `settlements` | Calibration / replay / truth main chain |
| daily `observations` | Persistence / truth logic |
| `forecast_skill` | Fully imported |
| `token_price_log` | Monitor velocity, PnL hindsight, partial replay audit |

### Still partially unused

1. **Historical forecast coverage**: `28,006 / 171,003` imported — ~84% still missing. Limits long-term alpha / model skill statistics.
2. **Historical replay compatibility**: 1,385 settlements total but only 26 vector-compatible snapshots. Core gap is old `p_raw_json` shape, old label normalization, and missing decision-time references — not raw volume.
3. **Token-price path as replay prior**: Runtime monitor and hindsight audit use it, but full historical replay prior reconstruction is incomplete.

### Not the main problem anymore

DST / sunrise / sunset, hourly observation timing, Day0 time semantics, and semantic snapshot spine are all in the main chain now. Do not describe these as "unused data."
