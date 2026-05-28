# Zeus Weather Data Sources Audit
**Date:** 2026-05-28 | **Operator:** Fitz | **Scope:** Raw/initial dataset inventory

## Data Source Ingestion Summary

Zeus ingests ensemble forecasts from two primary sources and observations from Weather Underground. The system uses a three-stage pipeline: download → extract/format → ingest into canonical DB schema.

### Forecast Sources

| Source | Model Version | Variables | Temporal Step | Download Path | Extract Path | Data Version(s) | Row Count (forecasts.db) | Status |
|--------|---------------|-----------|---------------|---------------|--------------|-----------------|-------------------------|--------|
| **TIGGE MARS** | ecmwf_ens | mx2t6, mn2t6 (6h aggregations) | 6-hourly | `51 source data/raw/` (via ecmwfapi SDK) | `51 source data/scripts/extract_tigge_mx2t6_localday_max.py` & `extract_tigge_mn2t6_localday_min.py` | `tigge_mx2t6_local_calendar_day_max_v1` | 385,139 | ARCHIVED (48h embargo) |
|  |  |  |  |  |  | `tigge_mn2t6_local_calendar_day_min_v1` | 384,202 |  |
|  |  |  |  |  |  | `tigge_mn2t6_local_calendar_day_min_contract_window_v2` | 348,706 |  |
| **ECMWF OpenData** | ecmwf_ens | mx2t3, mn2t3 (3h native) | 3-hourly → 6h envelope | in-process parallel ThreadPoolExecutor (src/data/ecmwf_open_data.py:_fetch_one_step) | `51 source data/scripts/extract_open_ens_localday.py` | `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` | 11,418 | LIVE (6-8h latency) |
|  |  |  |  |  |  | `ecmwf_opendata_mn2t3_local_calendar_day_min_v1` | 9,314 |  |
|  |  |  |  | (max_workers=5, per-step concatenation) | (conforms to TiggeSnapshotPayload contract) | `ecmwf_opendata_mx2t6_local_calendar_day_max_v1` (legacy) | 1,342 |  |
|  |  |  |  |  |  | `ecmwf_opendata_mn2t6_local_calendar_day_min_v1` (legacy) | 508 |  |

### Observation (Settlement Truth) Sources

| Source | Metric | Fetch Endpoint | Temporal Schedule | Write Path | DB Target | Row Count | Status |
|--------|--------|----------------|-------------------|------------|-----------|-----------|--------|
| **Weather Underground Daily** | high_temp, low_temp (daily extrema) | WU v1 API: `location/{ICAO}:9:{CC}/observations/historical.json` | Per-city local time (peak_hour + 4h) via `WuDailyScheduler` | `scripts/backfill_wu_daily_all.py` (K1 atom schema) | `zeus-world.db::observations` (K1 schema) | ~52 cities, 90d+ | LIVE |

### Calibration Pairs (Derived from Forecasts × Observations)

| Data Version | Source Forecast | Observations | Training Pairs | Status |
|--------------|-----------------|--------------|-----------------|--------|
| `tigge_mx2t6_local_calendar_day_max_v1` | TIGGE HIGH (6h) | WU daily high | 38,247,506 | TRAINING (archive) |
| `tigge_mn2t6_local_calendar_day_min_v1` | TIGGE LOW (6h) | WU daily low | 7,137,176 | TRAINING (archive) |
| `tigge_mn2t6_local_calendar_day_min_contract_window_v2` | TIGGE LOW (6h, contract window) | WU daily low | 2,159,338 | TRAINING (contract-scoped) |
| `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` | ECMWF OpenData HIGH (3h) | WU daily high | 592,950 | LIVE (recent) |
| `ecmwf_opendata_mn2t3_local_calendar_day_min_v1` | ECMWF OpenData LOW (3h) | WU daily low | 7,858 | LIVE (recent) |

## Critical Data Paths

### TIGGE MARS Pipeline
- **Auth:** `~/.ecmwfapirc` (ECMWF SDK credential file)
- **Orchestrator:** `src/data/tigge_pipeline.py` (daily cycle runner)
- **Download Script:** `51 source data/scripts/tigge_mx2t6_download_resumable.py` + `tigge_mn2t6_download_resumable.py`
- **Extract Scripts:** `51 source data/scripts/extract_tigge_mx2t6_localday_max.py` + `extract_tigge_mn2t6_localday_min.py`
- **Ingest Entry:** `scripts/ingest_grib_to_snapshots.ingest_track()` (canonical write)
- **Data Staging:** `51 source data/raw/` (GRIB files on disk; ~48h hold before public)

### ECMWF OpenData Pipeline
- **Auth:** ECMWF OpenData public API (no credentials required)
- **Orchestrator:** `src/data/ecmwf_open_data.py` (live cycle runner, ~6-8h latency)
- **Download:** In-process parallel fetch (`ThreadPoolExecutor`, per-step GRIB2 files)
- **Cache:** `50 source data/open_ens_YYYYMMDD_HHz_steps_*_params_*.grib2` (per-step intermediate files)
- **Extract Script:** `51 source data/scripts/extract_open_ens_localday.py`
- **Ingest Entry:** Reuses `scripts/ingest_grib_to_snapshots.ingest_track()` (same contract)

### WU Daily Observations Pipeline
- **Scheduler:** `src/data/wu_scheduler.py` (per-city local-time triggers: peak_hour + 4h)
- **Fetch:** `scripts/backfill_wu_daily_all.py` (ICAO API via requests)
- **Validation:** `src/data/ingestion_guard.py` (per-atom bounds check, K1 schema)
- **Write:** `src/data/daily_observation_writer.py::write_daily_observation_with_revision()` (K1 atoms)
- **DB Target:** `zeus-world.db::observations` (canonical truth table)

## Data Flows into Trading Engine

1. **Live Forecast Selection** (`src/data/executable_forecast_reader.py`)
   - Reads `ensemble_snapshots_v2` with data_version priority list
   - Prefers ECMWF OpenData (3h native, 6-8h latency) when available
   - Falls back to TIGGE archive if OpenData unavailable
   - Extracts 51 ensemble members per target_date

2. **Daily Observation Truth** (`src/engine/monitor_refresh.py`, `src/signal/day0_signal.py`)
   - Reads `observations` table (WU HIGH for day0, then peak confirmation)
   - Used for settlement verification and day-0 signal bias correction

3. **Calibration Flow** (`src/calibration/manager.py`, `src/calibration/store.py`)
   - Reads `calibration_pairs_v2` (pairs of forecast p_raw + settled observation)
   - Routes to Platt model fitter per city/metric/data_version
   - Produces `model_bias_ens_v2` rows for entry probability adjustment

## Data Version Canonical Truth

Per `src/contracts/ensemble_snapshot_provenance.py`:
- **CANONICAL_ENSEMBLE_DATA_VERSIONS** = {
  - `tigge_mx2t6_local_calendar_day_max_v1` (TIGGE, 6h steps, HIGH)
  - `tigge_mn2t6_local_calendar_day_min_v1` (TIGGE, 6h steps, LOW)
  - `tigge_mn2t6_local_calendar_day_min_contract_window_v2` (TIGGE, contract window, LOW)
  - `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` (OpenData, 3h native, HIGH)
  - `ecmwf_opendata_mn2t3_local_calendar_day_min_v1` (OpenData, 3h native, LOW)
  - Deprecated (legacy): `ecmwf_opendata_mx2t6_*`, `ecmwf_opendata_mn2t6_*`
}

## Observations
- TIGGE rows (769,156 + 116 null-source) represent archive spanning ~868 unique dates × 52 cities
- ECMWF OpenData rows (22,582) represent recent data (~29 dates) with live refresh every 6-8 hours
- All forecast data_versions follow `{source}_{variable}_{temporal_logic}_{aggregation}_v{N}` naming
- Calibration pairs are sparse for OpenData (recent ingest start) but dense for TIGGE (years of archive)
- No GFS or other external ensemble sources currently in production (forecast_source_registry lists them as diagnostic-only)

