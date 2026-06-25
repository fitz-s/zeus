# Upstream Forecast Ingest Map
**Produced:** 2026-06-17  
**Scope:** raw_model_forecasts population, model lists, national-met ingestion, cell-distance recording, overlay CSV consumption

---

## 1. What Populates `raw_model_forecasts`

**Table location:** `state/zeus-forecasts.db` (INV-37: single connection; schema defined at `src/state/schema/v2_schema.py:465`)

**Two writers only:**

| Writer | File:Line | Trigger | What it writes |
|--------|-----------|---------|----------------|
| `download_bayes_precision_fusion_extra_raw_inputs()` | `src/data/bayes_precision_fusion_download.py:867` | Forecast-live daemon, publish-time cron (00/06/12/18Z + release_lag) + boot catch-up | All BPF extra models: single_runs (forward) + previous_runs (fixed-lead) |
| `scripts/backfill_bayes_precision_fusion_history_from_b0.py` | operator-invoked backfill script | One-shot operator tool | previous_runs rows seeded from B0 historical dataset |

**Important:** `trade_authority_status = 'SHADOW_ONLY'` and `training_allowed = 0` are enforced by schema CHECK constraint (`schema/v2_schema.py:496-498`). This table is a research-accrual shadow; it does NOT directly feed the live money path. The live anchor (AIFS + IFS9) writes to `ensemble_snapshots` and `deterministic_forecast_anchors` (via `replacement_forecast_materializer.py`), not `raw_model_forecasts`.

**Scheduling:** `src/ingest/forecast_live_daemon.py:923` (`_replacement_forecast_download_job`) calls `_replacement_forecast_download_cycle.__wrapped__()` on a dedicated `replacement_download` executor lane, separate from the `replacement_production` materialize lane. Cron fires at `(cycle_hour + release_lag_hours) % 24` for cycles {0, 6, 12, 18}Z (default 14h lag → 14:00/20:00/02:00/08:00 UTC). `src/ingest/forecast_live_daemon.py:1014` (`_register_replacement_forecast_production_jobs`).

**Gating:** The download is flag-gated: `settings['edli']['replacement_0_1_bayes_precision_fusion_capture_enabled']` (default `False`). `src/data/replacement_forecast_production.py:273`.

---

## 2. Model Set Fetched

**BAYES_PRECISION_FUSION_EXTRA_MODELS** (defined `src/data/bayes_precision_fusion_download.py:242`):

```python
BAYES_PRECISION_FUSION_EXTRA_MODELS = (
    'ecmwf_ifs',                      # anchor (prior); OM previous-runs id 'ecmwf_ifs025'
    'gfs_global',                     # NOAA 0.25° global
    'icon_global',                    # DWD-ICON global
    'gem_global',                     # CMC GDPS ~15km global
    'jma_seamless',                   # JMA global seamless
    'ukmo_global_deterministic_10km', # UKMO 10km global (promoted 2026-06-09)
    'icon_eu',                        # DWD ICON-EU 7km nest
    'ncep_nbm_conus',                 # NCEP NBM CONUS 13km blend (promoted 2026-06-09)
    'icon_d2',                        # DWD ICON-D2 2km EU regional
    'meteofrance_arome_france_hd',    # Météo-France AROME HD France
    'ukmo_uk_deterministic_2km',      # UKMO UKV 2km UK regional (promoted 2026-06-09)
    'gfs_hrrr',                       # NOAA HRRR 3km CONUS (precision-input fix 2026-06-17)
    'gem_hrdps_continental',          # CMC HRDPS 2.5km N-America (precision-input fix 2026-06-17)
    'icon_seamless',                  # alias-dedup probe only; dropped from fusion
)
BAYES_PRECISION_FUSION_CANDIDATE_ACCRUAL_MODELS = ()  # currently empty
```

**Verdict: the download attempts ALL 14 models for EVERY city, then applies polygon/domain gates per-city before issuing HTTP requests.**

### Per-City vs Global Model List

The initial model set is **GLOBAL** (same 14 models for all 54 cities). However, **domain-gated models** are filtered per-city coordinate before the HTTP call is made:

- `src/data/bayes_precision_fusion_download.py:331` — `_DOMAIN_GATED_MODELS` = `REGIONAL_MODELS | {icon_eu, ncep_nbm_conus, ukmo_uk_deterministic_2km}` 
- `src/data/bayes_precision_fusion_download.py:340` — `_model_in_domain(model, lat, lon, lead_days)` → checks `config/model_domain_polygons.yaml` per model
- `src/forecast/model_selection.py:129` — `_REGIONAL_DOMAIN_KEY` defines polygons for: icon_d2, meteofrance_arome_france_hd, icon_eu, ncep_nbm_conus, ukmo_uk_deterministic_2km, gfs_hrrr, gem_hrdps_continental

**Globals** (gfs_global, icon_global, gem_global, jma_seamless, ukmo_global_deterministic_10km) — worldwide; never domain-gated. Every city gets these.

**Domain-gated models** (effective per-city subset examples):
- Paris: ecmwf_ifs + globals + icon_eu + icon_d2 + arome + ukmo_uk (in-France polygon)
- NYC: ecmwf_ifs + globals + icon_eu + ncep_nbm_conus + gfs_hrrr + gem_hrdps_continental
- Moscow: ecmwf_ifs + globals (icon_d2 out-of-polygon; icon_eu in-polygon)
- Tokyo: ecmwf_ifs + globals (no CONUS or EU regionals)

The `icon_seamless` model is fetched only when `icon_d2` is also in-domain (for the alias-dedup test), then always dropped from the fusion (`model_selection.py:332`).

**Provider-family single-rep rule** (`model_selection.py:120`) further reduces the FUSION set at materialization time (e.g., icon_d2 in-domain → icon_eu and icon_global suppressed), but the download still accrues history for all domain-eligible members of each family.

### Key API Facts

- **Single-runs endpoint:** `OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"` (`src/data/day0_hourly_vectors.py:50`); BPF uses `SINGLE_RUNS_FORECAST_URL` from `openmeteo_ecmwf_ifs9_anchor.py`
- **Previous-runs endpoint:** `PREVIOUS_RUNS_URL` from `src/data/openmeteo_client.py`
- **Batched fetch (2026-06-13 API-COLLAPSE):** ONE single_runs call per (city, target_date, cycle) with `models=[list]` — OM returns `temperature_2m_<om_id>` per model. ONE previous_runs call per city+date covers all models. `bayes_precision_fusion_download.py:455` and `:541`.
- `gem_global` is `SINGLE_RUNS_UNSERVABLE_MODELS` — its current value is served from previous_runs only (`bayes_precision_fusion_download.py:283`).

---

## 3. National Met Service Forecasts

**Verdict: NO national met service forecasts (NWS/NDFD, MGM Turkey, HKO, CWA Taipei, JMA, AEMET, etc.) are ingested into the forecast path today.**

Evidence:
- No file in `src/data/` or `scripts/` contains any INSERT into `raw_model_forecasts`, `forecasts`, `ensemble_snapshots`, or `deterministic_forecast_anchors` from NWS, HKO forecast API, CWA forecast API, MGM, AEMET, or any national met service.
- `scripts/hko_ingest_tick.py` writes only to `hko_hourly_accumulator` (observation table), not forecast tables.
- `docs/evidence/per_city_source/city_data_sources_overlay.csv` records NWS/HKO/CWA/MGM as expected in the `forecast_stack` column of `docs/polyweather_city_source_overlay_verified.csv` (e.g., Atlanta: "NWS forecast", Hong Kong: "HKO forecast") — but these are documentation-only; no code consumes these CSVs (see §5).
- The `forecast_source_registry.py` `OPENMETEO_PREVIOUS_RUNS_MODEL_SOURCE_MAP` (`src/data/forecast_source_registry.py:154`) lists only Open-Meteo API model IDs; no national met API endpoints appear anywhere in the ingest pipeline.
- JMA appears only as `jma_seamless` — the JMA GSM/seamless model served **through Open-Meteo**'s multi-model API, not from JMA's own dissemination.

**The forecast lane is purely Open-Meteo (multi-model API + ECMWF open-data AIFS ensemble).** National met mentions in comments (`temporal_provenance.py:81`: `"HKO"`, `"NOAA"`) refer to the **observation/settlement** lane, not forecast ingest.

---

## 4. Coordinate Used and Cell-Distance Recording

### Coordinate Requested

- Cities config: `config/cities.json` — all 54 cities carry `lat`/`lon` at airport level. The header `_coord_note` (line 2) states: *"All lat/lon coordinates correspond to the airport weather station used by Weather Underground for Polymarket settlement."*
- US cities also have a `noaa` sub-object with matching `lat`/`lon` (verified: all 11 US cities show `top == noaa` coordinates; e.g., Atlanta top=(33.62972,-84.44223), noaa=(33.62972,-84.44223)).
- `src/config.py:313-393` (`load_cities_from_json`): US cities read `noaa.lat`/`noaa.lon`; international read top-level `lat`/`lon`. Both resolve to the same airport coordinate.
- `src/data/replacement_forecast_production.py:324`: `latitude=float(city_cfg.lat), longitude=float(city_cfg.lon)` — the airport coordinate is passed to `BayesPrecisionFusionDownloadTarget`.

### What Is Stored in `raw_model_forecasts`

From `_RMF_INSERT_COLUMNS` (`bayes_precision_fusion_download.py:633`) and schema (`v2_schema.py:465`):

| Column | Content |
|--------|---------|
| `latitude_requested` | Airport lat (the coordinate sent to OM API) |
| `longitude_requested` | Airport lon |
| `cell_selection` | `"nearest"` — OM nearest-gridpoint policy |
| `elevation_param` | `"requested"` |
| `downscaling_policy` | `"none"` |

**What is NOT stored:** The actual grid cell latitude/longitude returned by the Open-Meteo API for each model is NOT recorded in `raw_model_forecasts`. There is no `latitude_returned`, `latitude_cell`, or `cell_distance_km` column. The `cell_selection = "nearest"` tells us the policy (OM snaps to nearest gridpoint), but the resulting cell coordinates and the distance from that cell to the airport are not persisted per-model.

**Exception — ECMWF IFS9 anchor only:** `src/data/openmeteo_ecmwf_ifs9_precision_guard.py` computes `nearest_grid_distance_km` and stores it in `OpenMeteoIfs9PrecisionMetadata` (an in-memory dataclass, not a DB column). `openmeteo_ecmwf_ifs9_bucket_transport.py:201` computes `dist_km = _haversine_km(latitude, lon360, grid_lat, grid_lon)` for the AIFS O1280 grid only. This is guard logic for the anchor; the BPF multi-model lane has no equivalent.

**Summary:** Per-model cell-distance-to-airport is NOT recorded anywhere for the 13 non-anchor models in `raw_model_forecasts`. The coarse globals (jma_seamless, gfs_global, icon_global, ukmo_global, gem_global at ~14-28km resolution) snap to their nearest gridpoint, but the actual snap distance is unknown in the database. This is the gap the operator's design law ("finest resolution closest to airport") identifies as critical: a jma_seamless cell could be 11-16km from the airport and the system has no stored evidence of this per-city.

---

## 5. Overlay CSV Consumption

**Verdict: NEITHER overlay CSV is consumed by any ingest, forecast, or calibration code.**

Grep evidence (both files searched across `src/` and `scripts/`):

```
grep -rn "city_data_sources_overlay\|polyweather_city_source_overlay" src/ scripts/
(no output)
```

- `/Users/leofitz/zeus/docs/evidence/per_city_source/city_data_sources_overlay.csv` (54 rows, 21 columns including `forecast_primary_source`, `ecmwf_release_delay_min`, `forecast_shadow_source`, `forecast_backfill_source`, `observation_primary_source`, `settlement_icao_or_key`, `meteostat_wmo_id`) — documentation only.
- `/Users/leofitz/zeus/docs/polyweather_city_source_overlay_verified.csv` (50 rows, 15 columns including `forecast_stack`, `settlement_source`, `observation_stack`, `settlement_stack`) — documentation only.

Both files were last modified 2026-06-17 (today) and contain correct per-city source intent, but no `import`, `open()`, `read_csv()`, or path reference to either file appears in any `.py` file in `src/` or `scripts/`.

---

## 6. Ingest Job Inventory Summary

| Job | File:Line | Lane | Writes To | Models |
|-----|-----------|------|-----------|--------|
| BPF extra-model download (flag-gated) | `src/data/replacement_forecast_production.py:262` → `src/data/bayes_precision_fusion_download.py:867` | `replacement_download` executor (forecast-live daemon) | `raw_model_forecasts` (SHADOW_ONLY) | 14 models; domain-gated per city |
| AIFS ensemble + IFS9 anchor download | `src/data/replacement_forecast_production.py:185` → `scripts/download_replacement_forecast_current_targets.py` | `replacement_download` executor | `ensemble_snapshots`, `deterministic_forecast_anchors` | ECMWF AIFS 51-member ENS + IFS9 0.1° deterministic |
| BPF materializer (light, interval) | `src/data/replacement_forecast_production.py` / `replacement_forecast_materializer.py` | `replacement_production` executor | `forecast_posteriors` | Reads `raw_model_forecasts` + anchor data |
| Legacy forecasts appender (daily_tick) | `src/data/forecasts_append.py:456` | `ingest_main.py` | `forecasts` | `DEFAULT_MODELS` = best_match, gfs_global, ecmwf_ifs025, icon_global, ukmo_global — all via OM previous-runs |
| Day0 hourly vectors | `src/data/day0_hourly_vectors.py` | live engine | `day0_hourly_vectors` | icon_d2, meteofrance_arome_france_hd, ukmo_uk_deterministic_2km, ncep_nbm_conus (domain-gated) |
| B0 backfill (one-shot) | `scripts/backfill_bayes_precision_fusion_history_from_b0.py:159` | operator-invoked | `raw_model_forecasts` (previous_runs seed) | Same BPF model set |

---

## Key Findings

1. **`raw_model_forecasts` has exactly one live writer**: `bayes_precision_fusion_download.py`. The live anchor (AIFS) writes `ensemble_snapshots`, not `raw_model_forecasts`.

2. **Model list is a global base set (14 models) with per-city polygon domain-gating**. All cities attempt the same 14 models; domain-gated ones (5 regionals + icon_eu + ncep_nbm + ukmo_uk) are skipped when the city is out of their polygon. The `select_models()` function in `model_selection.py` further applies provider-family single-rep deduplication at fusion time.

3. **No national met service forecasts (NWS/NDFD, MGM, HKO, CWA, JMA native) are ingested into any forecast table.** The JMA contribution is via Open-Meteo `jma_seamless`. National met services appear only in the observation/settlement lane (HKO API → `hko_hourly_accumulator`, NOAA API → settlement truth).

4. **Coordinates used are airport coordinates** from `config/cities.json` (`_coord_note` makes this explicit). The OM API is called with `cell_selection=nearest`, meaning it snaps to the nearest model gridpoint. The actual snapped cell coordinates and distance-to-airport are **not stored** per model in `raw_model_forecasts` (columns `latitude_requested`/`longitude_requested` record what was SENT, not what the model actually used). Cell distance tracking exists only for the ECMWF IFS9 anchor via `openmeteo_ecmwf_ifs9_precision_guard.py` (in-memory only, not persisted to DB).

5. **Both overlay CSVs are documentation-only**. Neither `docs/evidence/per_city_source/city_data_sources_overlay.csv` nor `docs/polyweather_city_source_overlay_verified.csv` is referenced by any code in `src/` or `scripts/`. The `forecast_primary_source` column in the operational overlay shows `ecmwf_open_data` for all 54 cities — confirming the AIFS is the declared primary, but this declaration is not enforced/consumed programmatically.
