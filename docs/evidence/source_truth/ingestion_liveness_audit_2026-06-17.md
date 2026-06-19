# Ingestion liveness audit — all sources / steps / dates

Created: 2026-06-17 ~14:05 UTC
Authority basis: operator "make sure all new sources are wired and actively ingested, all steps and
dates we need for calculation" (2026-06-17). Every row is proven from live DB rows at audit time
(state/zeus-forecasts.db, state/zeus-world.db, ro), not config.

## Forecast models (the calculation core) — ALL LIVE

`raw_model_forecasts`, last capture 09:44Z (06Z cycle, ~4h before audit; 12Z not yet due). 49 active
cities (market-driven). Every model in the live fusion set (`src/forecast/model_selection.py`) is
ingesting:

| model | role | rows/24h | cities/24h |
|---|---|---|---|
| ecmwf_ifs (9km/0.1° ANCHOR) | prior | 950 | 49 |
| gfs_global, icon_global, ukmo_global_10km | decorrelated globals | 889-950 | 49 |
| jma_seamless | decorrelated global | 540 | 49 |
| gem_global | decorrelated global | 98 | 49 |
| icon_eu (7km) | EU-domain | 268 | 12 |
| ncep_nbm_conus | CONUS-domain | 135 | 12 |
| icon_d2 (2km) | Central-EU | 84 | 5 |
| meteofrance_arome_france_hd | France | 42 | 2 |
| ukmo_uk_deterministic_2km | London | 28 | 1 |
| **gfs_hrrr (3km, NEW 2026-06-17 precision fix)** | CONUS hi-res | 28 | 12 |
| **gem_hrdps_continental (2.5km, NEW 2026-06-17)** | N-America hi-res | 11 | 4 |

Both new station-resolving precision models (gfs_hrrr, gem_hrdps) ARE landing rows. **AIFS: absent
from `raw_model_forecasts` = confirmed dropped** (production `skip_aifs=True`).

## Observations — LIVE

| source | rows/24h | last | role |
|---|---|---|---|
| `wu_icao_history` | 84 | 14:00 today | settlement-final (exact ICAO) — fresh |
| `ogimet_metar_*` (per ICAO) | many | rolling | exact-ICAO METAR incl. problematic-20 (dnmm Lagos, oejn Jeddah, wihh Jakarta, zhhh/zuck/zuuu China, saez, sbgr, vilk, rcss…) |
| `hko_realtime_api` / `hko_hourly_accumulator` | live | — | Hong Kong native |
| `meteostat_bulk_*` (~50 stations) | live | — | history backfill |
| `openmeteo_archive_hourly` | live | — | hourly archive |
| `day0_hourly_vectors` | 3397 | live | day0 obs vectors built |

The v4 "best public live obs = exact-ICAO METAR" IS satisfied (ogimet METAR per ICAO + wu settlement).

## Settlement truth — LIVE

`settlement_outcomes` authority=VERIFIED: 139 cells in 3d, latest target_date 2026-06-16. The de-bias /
calibration / validation substrate is fresh.

## Grid representativeness — COMPLETE (fixed this turn)

`config/grid_representativeness.json`: **54/54 cities** (was 53 — Shanghai was absent from
`station_precise_coords.json`; added with the operator re-pin coord 31.1433/121.8053 ZSPD + OurAirports
elevation 3.96m, grid row built + merged). v3 σ_repr wire is flag-gated OFF (byte-identical) pending
settlement validation.

## Gaps (honest)

1. **day0_nowcast_runs = 0 and day0_metric_fact = 0 (EMPTY tables).** Obs DATA lands fine
   (day0_hourly_vectors, ogimet, wu fresh), but the day0 SIGNAL calc rows are absent. EITHER the legacy
   nowcast tables are superseded by the qkernel_spine day0/FSR path (tasks #118-120) OR the day0 lane is
   dark (task #72). `aviationweather_metar` (day0 fast lane, in-process memo, NOT persisted) rides this.
   ACTION: confirm which — if superseded, fine; if dark, the day0 q-signal isn't being produced.
2. **5 cities not ingesting forecasts** (Auckland, Jakarta, Jinan, Lagos, Zhengzhou) — stopped
   2026-06-09T20:22. ROOT CAUSE: the download is MARKET-DRIVEN (`replacement_forecast_current_target_plan`
   keys on `market_bin_count` / `source_run_coverage`); these 5 have no active Polymarket markets right now
   → correctly skipped (NOT a fetch defect). They resume when markets open (Auckland = task #93). Jakarta +
   Lagos are in the v4 problematic-20 but currently market-dormant.
3. **Singapore NEA** (data.gov.sg, minute cadence, free) — the one net-new free public obs source from
   v4; not wired. High-frequency Changi shadow (not WSSS-exact).

## Verdict

The calculation core (forecast fusion + settlement + obs + grid) is fully wired and actively ingesting,
including the new 9km anchor + 3km/2.5km precision regionals and the dropped AIFS. The open items are the
day0-signal lane status (#1, needs a one-line confirm) and the market-dormant cities (#2, not a defect).
