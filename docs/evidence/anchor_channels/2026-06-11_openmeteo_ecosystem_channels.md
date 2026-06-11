# Open-Meteo ECMWF IFS 9km Data Access Channels — Exhaustive Enumeration

**Report Date:** 2026-06-11  
**Objective:** Enumerate every possible way to obtain ECMWF IFS HRES 9km temperature data through the open-meteo ecosystem, with latencies, costs, and access mechanisms.

---

## Summary Table

| Channel | Data Product | Resolution | Latency (hours) | Cost | Reliability | How to Access |
|---------|--------------|-----------|-----------------|------|-------------|---------------|
| **Standard Forecast API** | IFS HRES real-time | 9 km (O1280) | 0 (real-time) | Free (unlim: €29–€99/mo) | Good; geographically distributed | `api.open-meteo.com/v1/ecmwf?models=ecmwf_ifs` |
| **Single Runs API** | IFS HRES by init time | 9 km (O1280) | ~0.1 (1-min S3 delay) | Free (limit 5k/hr); €99/mo pro | Good; 4–6h from cycle start | `api.open-meteo.com/v1/archive?run=2026-06-11T00:00Z` |
| **AWS S3 Rolling Timeseries** | `data/<model>/<var>/<chunk>.om` | 9 km | ~0.1 (streaming) | Free (AWS egress charges apply) | Good; direct cloud access | `s3://openmeteo/data/ecmwf_ifs/temperature_2m/*.om` |
| **AWS S3 Spatial Data** | `data_spatial/<run>/<timestamp>.om` | 9 km | ~0.05–0.5 (near-RT) | Free (AWS egress charges) | Good; near-real-time | `s3://openmeteo/data_spatial/ecmwf_ifs/2026/06/11/0000Z/*.om` |
| **AWS S3 Run Data** | `data_run/<run>/<var>.om` | 9 km | ~1–2 (full run) | Free (AWS egress) | Good; 3-month retention | `s3://openmeteo/data_run/ecmwf_ifs/2026/06/11/0000Z/*.om` |
| **Self-Hosted via Docker** | Synced `ecmwf_ifs` | 9 km | ~2–6 (ingest + sync) | Free (infrastructure cost) | Depends on your ops | `docker run open-meteo sync ecmwf_ifs ... --repeat-interval 5` |
| **Previous Runs API** | IFS at fixed lead-times | 9 km | ~1–7 day offset | Free (limit); €99/mo pro | Good; fixed offset only | `api.open-meteo.com/v1/archive?model=ecmwf_ifs` with lead-day param |
| **IFS 0.25° Open-Data** | Lower-res alternative | 0.25° (~25km) | 2 (official ECMWF delay) | Free | Good; slower but available | `api.open-meteo.com/v1/ecmwf?models=ecmwf_ifs025` |

---

## Detailed Channel Analysis

### 1. **Standard Forecast API** — `api.open-meteo.com`

**Data Product:**  
- ECMWF IFS HRES at native 9 km resolution (O1280 Gaussian grid)
- 1-hourly temporal resolution for first 90 hours, 3-hourly to 144h, 6-hourly beyond
- 15-day forecast horizon

**Latency & Schedule:**
- Updates every 6 hours (00Z, 06Z, 12Z, 18Z model cycles)
- **Real-time availability:** Marked as "without any additional delay" post-October 1, 2025 (open-data transition)
- Model cycle to availability: ~4–6 hours from initialization (e.g., 00Z run available ~04–06Z)
- API is geographically distributed across Europe and North America with eventual consistency: wait +10 min after reported update for all servers to sync

**Rate Limits & Cost:**
- **Free tier (non-commercial):**
  - 600 calls/minute, 5,000 calls/hour, 10,000 calls/day, 300,000 calls/month
- **Commercial tiers (dedicated endpoint `customer-api.open-meteo.com`)**:
  - **Standard:** €29/month → 1M calls/month
  - **Professional:** €99/month → 5M calls/month, includes Historical/Ensemble/Climate APIs
  - **Enterprise:** >50M calls/month (custom negotiation)

**Endpoint & Consumption:**
```
GET https://api.open-meteo.com/v1/ecmwf
  ?latitude=<lat>
  &longitude=<lon>
  &hourly=temperature_2m
  &models=ecmwf_ifs
```
Returns JSON time-series directly.

**Reliability:**
- Good for operational use but subject to same ingestion stalls as witnessed (22+ hours on 2026-06-09/10)
- SLA on paid plans: 99.9% uptime
- Monitor at: https://open-meteo.com/en/docs/model-updates (shows real-time update status)

**Authority Basis:**
- https://open-meteo.com/en/docs/ecmwf-api
- https://open-meteo.com/en/docs/model-updates

---

### 2. **Single Runs API** — `api.open-meteo.com/v1/archive` (with `run=` param)

**Data Product:**
- Complete forecast horizon from individual ECMWF IFS HRES runs by initialization time
- Native 9 km resolution, O1280 grid
- Archival available from March 14, 2024; current IFS Cycle 50R1 since May 12, 2026

**Latency & Schedule:**
- Runs available at 00Z, 06Z, 12Z, 18Z UTC
- **Availability from cycle start:** ~4–6 hours (model computation time)
  - 00Z run → available ~04–06Z
  - 06Z run → available ~10–12Z
  - 12Z run → available ~16–18Z
  - 18Z run → available ~22–24Z
- **Processing latency via AWS:**
  - Uses ECMWF "pre-schedule delivery" (early access to data as it's being generated)
  - Achieves ~1-minute delay per forecast time-step through AWS Open Data sponsorship
  - Full run available shortly after last time-step published by ECMWF

**Rate Limits & Cost:**
- Free tier: same as Forecast API (600/min, 5k/hr, 10k/day, 300k/mo)
- Professional tier: €99/month (5M calls/month)
- No additional cost vs. Forecast API (shared rate limits)

**Endpoint & Consumption:**
```
GET https://api.open-meteo.com/v1/archive
  ?latitude=<lat>
  &longitude=<lon>
  &hourly=temperature_2m
  &models=ecmwf_ifs
  &run=2026-06-11T00:00Z  # Specify exact run initialization
```
Returns full 15-day forecast for that single run.

**Reliability & Coverage:**
- Excellent for backtesting (no look-ahead bias; each training pair is a single run)
- Essential for energy/trading workflows that require exact forecast available at a decision time
- Archival extends to March 2024; forward data continuously appended

**Authority Basis:**
- https://openmeteo.substack.com/p/single-runs-api
- https://open-meteo.com/en/docs/single-runs-api

---

### 3. **AWS S3 Direct Access (Rolling Timeseries)** — `s3://openmeteo`

**Data Product:**
- Multi-dimensional OM-file format (cloud-native, chunked)
- Path structure: `data/<model>/<weather-variable>/<time-chunk>.om`
- For ECMWF IFS: `data/ecmwf_ifs/temperature_2m/{year_2024.om, chunk_927382.om, ...}`

**Latency & Schedule:**
- **Update frequency:** Every 6 hours (aligned with model cycles)
- **Available latency:** ~0.1 hours (essentially real-time; data streamed as chunks are generated)
- Rolling windows: chunks are overwritten on each update cycle; data persists for multiple years in a rotating buffer

**Cost:**
- **Free:** No data transfer cost (AWS Open Data Sponsorship)
- **AWS egress charges:** Standard S3 data transfer rates apply if downloading to external compute (typically ~$0.02/GB out of `us-west-2`)

**Access Method:**
- **Command line:** `aws s3 ls --no-sign-request s3://openmeteo/data/ecmwf_ifs/temperature_2m/`
- **HTTP (HTTPS):** `https://openmeteo.s3.amazonaws.com/data/ecmwf_ifs/temperature_2m/{filename}.om`
- **Cloud-native reads:** OM-file format supports partial/chunked HTTP range requests; read single location (16 KiB) from a 9 GiB file without full download

**OM-File Format & Tooling:**
- Custom binary format designed for chunked, compressed multi-dimensional data (similar to NetCDF/HDF5 but lower overhead)
- **Client libraries available:**
  - **Python:** `openmeteo` (pip install python-omfiles)
  - **Rust:** `rust-omfiles`
  - **C:** source at https://github.com/open-meteo/om-file-format
  - **TypeScript/WASM:** `typescript-omfiles`
  - **Swift:** integrated into iOS API
- **Chunk size:** 1–4 KiB typical; cloud-native random-access capable

**Reliability:**
- Highly reliable; backed by AWS Open Data infrastructure
- No SLA on free tier but historically stable

**Authority Basis:**
- https://github.com/open-meteo/open-data (S3 bucket listing and structure)
- https://github.com/open-meteo/om-file-format (format specification and libraries)
- https://registry.opendata.aws/open-meteo

---

### 4. **AWS S3 Spatial Data** — `s3://openmeteo/data_spatial/`

**Data Product:**
- OM-file per timestamp containing all weather variables for that instant
- Path: `data_spatial/<model>/<run>/<timestamp>.om`
- Example: `data_spatial/ecmwf_ifs/2026/06/11/0000Z/202606110100.om`

**Latency & Schedule:**
- **Update frequency:** Every 6 hours (model cycle) or more frequently as data streams in
- **Availability:** Near-real-time (minutes after each time-step published by ECMWF)
- Metadata tracking: `data_spatial/<model>/in-progress.json` updated as each time-step completes; `latest.json` updated when run finishes
- **Retention:** 7 days (older data deleted to manage storage)

**Cost:**
- Free (AWS Open Data)
- S3 egress charges if downloaded externally

**Access Method:**
```bash
# List latest run
aws s3 ls --no-sign-request s3://openmeteo/data_spatial/ecmwf_ifs/latest.json

# Fetch specific timestamp
aws s3 cp --no-sign-request s3://openmeteo/data_spatial/ecmwf_ifs/2026/06/11/0000Z/202606110100.om .
```

**Use Cases:**
- Visualization and map rendering (all variables in one file per instant)
- Near-real-time monitoring (7-day rolling window)
- Continuous ingestion pipelines (metadata files mark completion)

**Authority Basis:**
- https://github.com/open-meteo/open-data (data organization section)

---

### 5. **AWS S3 Run-Based Data** — `s3://openmeteo/data_run/`

**Data Product:**
- Full time-series from a single model run, organized by variable
- Path: `data_run/<model>/<run>/<weather-variable>.om`
- Example: `data_run/ecmwf_ifs/2026/06/11/0000Z/temperature_2m.om`

**Latency & Schedule:**
- **Availability:** ~1–2 hours after run completes (final processing step)
- Created after rolling timeseries and spatial data ingests finish
- **Retention:** 3 months public on S3; extended archives available from Open-Meteo (contact info@open-meteo.com)

**Cost:**
- Free (AWS Open Data); S3 egress charges apply

**Access Method:**
```bash
# Fetch run metadata
aws s3 cp --no-sign-request s3://openmeteo/data_run/ecmwf_ifs/2026/06/11/0000Z/meta.json .

# Fetch variable from run
aws s3 cp --no-sign-request s3://openmeteo/data_run/ecmwf_ifs/2026/06/11/0000Z/temperature_2m.om .
```

**Use Cases:**
- Machine learning training (single run = single training example; no look-ahead bias)
- Backtesting trading/scheduling workflows
- Model intercomparison (exact forecast issued at a decision time)

**Caveats:**
- Only 13 pressure levels and model levels <200m (not full 3D field)
- Only one run every 3 hours retained; intermediate runs purged

**Authority Basis:**
- https://github.com/open-meteo/open-data (run-based data section)

---

### 6. **Self-Hosted via Docker + Sync** — Local Mirror

**Data Product:**
- Full Open-Meteo database mirrored locally (selectable models/variables)
- Native OM-file format stored in `/data` volume

**Latency & Schedule:**
- Ingest latency: ~2–6 hours from cycle time (ECMWF model publish time → download → process → local DB)
- Configurable sync interval (typical: every 5 minutes polling)
- **Example config:**
  ```env
  SYNC_ENABLED=true
  SYNC_DOMAINS=ecmwf_ifs
  SYNC_VARIABLES=temperature_2m
  SYNC_PAST_DAYS=3
  SYNC_REPEAT_INTERVAL=5  # minutes
  ```

**Cost:**
- No per-data cost (AWS egress into same region can be free with AWS Spot/Reserved compute)
- Infrastructure cost: container memory (4 GB single model, 16+ GB full multi-model), storage (100+ GB for rolling buffer)

**Access Method:**
```bash
# Docker pull and run
docker pull ghcr.io/open-meteo/open-meteo:latest
docker run \
  -e SYNC_ENABLED=true \
  -e SYNC_DOMAINS=ecmwf_ifs \
  -e SYNC_VARIABLES=temperature_2m \
  -e SYNC_REPEAT_INTERVAL=5 \
  -p 8080:8080 \
  -v /data:/data \
  open-meteo

# Query local API
curl "http://127.0.0.1:8080/v1/archive?latitude=47.1&longitude=8.4&hourly=temperature_2m"
```

**Reliability & Failure Modes:**
- Independent of open-meteo.com uptime (once synced, your local mirror continues serving)
- Vulnerable to your own infrastructure failures; add monitoring/alerting
- If sync service crashes, stale data served until fixed
- Known issue (from memory): previous-runs ingestion stalls propagate from upstream; mirror will show the same 22+ hour delays as public API if source stalls

**Authority Basis:**
- https://github.com/open-meteo/open-meteo/blob/main/docs/getting-started.md
- https://registry.opendata.aws/open-meteo (deployment tutorials)

---

### 7. **Previous Runs API** — Fixed Lead-Time Analysis

**Data Product:**
- Time-series at fixed lead-time offset (e.g., "always show 3-day-ahead forecast")
- Available from January 2024 onwards
- ECMWF IFS HRES 9 km

**Latency & Schedule:**
- **Lead-time offsets:** 1, 2, 3, 4, 5, 6, 7 days ahead
- Continuous time-series across all historical runs at fixed offset
- Useful for **forecast skill analysis** (e.g., "how accurate are 3-day forecasts")

**Cost:**
- Same as Forecast API (free tier + commercial tiers)

**Endpoint:**
```
GET https://api.open-meteo.com/v1/archive
  ?latitude=<lat>&longitude=<lon>
  &hourly=temperature_2m
  &models=ecmwf_ifs
```
(Use fixed lead-day parameter; exact syntax varies)

**Limitations:**
- Not suitable for operational real-time (only fixed offsets)
- Filled niche for lead-time stratified analysis, not primary operational channel

**Authority Basis:**
- https://open-meteo.com/en/docs/historical-forecast-api

---

### 8. **IFS 0.25° Open-Data** — Lower-Resolution Alternative

**Data Product:**
- ECMWF IFS at 0.25° resolution (~25 km) from official open-data distribution
- 3-hourly resolution, 6-hourly after 144 hours
- 15-day forecast horizon

**Latency & Schedule:**
- Updates every 6 hours
- **Official ECMWF latency:** 2 hours (documented delay on open-data distribution vs. real-time)
- Total cycle-to-availability: ~6 hours (2h official delay + 4h model computation)

**Cost:**
- Free (ECMWF open-data, CC-BY 4.0)

**Rationale for Enumeration:**
- **NOT 9 km** (which is the target) but documented here because:
  1. It is the official fallback when 9 km ingestion stalls (as happened 2026-06-09/10)
  2. Still available through same API (`api.open-meteo.com/v1/ecmwf?models=ecmwf_ifs025`)
  3. Provides continuity while 9 km pipeline recovers
- Resolution loss: ~2.8× (0.25° ≈ 25 km vs. 9 km)
- Temperature variance at 25 km is acceptable for long-duration trading but suboptimal for tactical decisions

**Authority Basis:**
- https://open-meteo.com/en/docs/ecmwf-api

---

## Latency Deep Dive: Cycle-to-Availability Timeline

Given ECMWF model cycle start times (00Z, 06Z, 12Z, 18Z):

| Channel | 00Z Cycle | 06Z Cycle | 12Z Cycle | 18Z Cycle |
|---------|-----------|-----------|-----------|-----------|
| **Standard API** | ~04–06Z | ~10–12Z | ~16–18Z | ~22–24Z |
| **Single Runs API** | ~04–06Z | ~10–12Z | ~16–18Z | ~22–24Z |
| **S3 Rolling TS** | ~04–06Z (start); +6h full | Same | Same | Same |
| **S3 Spatial** | ~04–06Z (first step); +0.5–1h intervals | Same | Same | Same |
| **S3 Run Data** | ~06–08Z (final) | ~12–14Z | ~18–20Z | ~00–02Z+1 |
| **Self-Hosted Sync** | ~04–08Z + SYNC_REPEAT | ~10–14Z + sync | ~16–20Z + sync | ~22–02Z + sync |
| **IFS 0.25° Open-Data** | ~06Z (0.5h delay + 4h model) | ~12Z | ~18Z | ~00Z+1 |

**Key insight:** First data becomes available ~4–6 hours after cycle start; full run ready ~6–8 hours.

---

## Known Reliability Issues & Failure Modes

### Documented in Zeus Memory (2026-06-09/10):

1. **ECMWF IFS Ingestion Stalls:** Open-meteo's ingestion of ECMWF 9km data **stalled for 22+ hours** (June 9–10).
   - Root cause: Not publicly disclosed; appears to be upstream ECMWF distribution issue or open-meteo processing bottleneck
   - **Impact on channels:**
     - Standard API: Data served stale (no new cycles ingested)
     - Single Runs API: No new runs available
     - S3 spatial/run data: Not updated
     - Self-hosted mirror: Mirrored the stall (synced from stalled upstream)
     - IFS 0.25° fallback: Also stalled (same source)
   - **Mitigation available:**
     - Alternative: Fall back to lower-res model (ICON, GFS)
     - Status monitoring: Watch https://open-meteo.com/en/docs/model-updates in real-time

2. **API Rate-Limit Brittleness (Free Tier):** 
   - Zeus's forecast anchor pipeline likely runs multiple queries/hour
   - Free tier limit is 5k/hour; commercial threshold is €29/month
   - Recommendation for production: migrate to Professional tier (€99/month, 5M calls/month)

3. **S3 Access Eventual Consistency:** Geographically distributed; wait +10 min after API reports update for all servers to sync

---

## Recommendation Matrix: Which Channel for Zeus?

| Use Case | Recommended Channel | Rationale |
|----------|---------------------|-----------|
| **Real-time trading decisions (next 6–12h)** | Single Runs API (primary) + IFS 0.25° fallback | Exact run structure; 4–6h latency acceptable; fallback if stall |
| **Multi-day forecast anchoring** | Standard Forecast API (primary) + S3 Rolling TS (backup) | Simplest integration; load-balanced across servers |
| **Backtesting (historical runs)** | Single Runs API (archive from March 2024) | No look-ahead bias; exact forecast available at decision time |
| **ML model training** | S3 Run-Based Data (`data_run/`) | Individual runs; 3-month public archive; extend via contact |
| **High-volume queries (>100k/day)** | Self-Hosted Docker + S3 direct | Avoid rate limits; control latency; lower cost at scale |
| **Resilience to stalls** | Multi-source fusion: Primary + fallback | Use IFS 0.25° or ICON during ECMWF stalls; switch back when restored |

---

## Failure Resilience Strategy for Zeus

**Current single-point failure:** Reliance on open-meteo's ECMWF 9km ingestion only.

**Recommended mitigation (3-tier):**

1. **Tier 1 (Primary):** Single Runs API, check `latest.json` for newest run age
2. **Tier 2 (Fallback A):** IFS 0.25° Open-Data (same source but lower resolution; acceptable for long-horizon)
3. **Tier 3 (Fallback B):** Self-hosted docker sync of ICON (DWD, 0.1° ~11km, updates every 6h, also on S3)
   - Requires <16 GB memory, 50 GB storage for rolling 3-day buffer
   - Removes dependency on open-meteo ingestion for ICON (still depends on DWD/AWS, different failure mode)

**Implementation:** Circuit breaker logic in anchor pipeline:
```python
try:
    run = fetch_ecmwf_ifs_single_run()  # Tier 1
    age = (now - run.init_time).total_seconds()
    if age > 6.5 * 3600:  # >6.5h = missed cycle
        raise StaleRunError(age)
except (StaleRunError, ConnectionError):
    try:
        fallback = fetch_ifs025_open_data()  # Tier 2
        log_warning("ECMWF stall detected; using 0.25° fallback")
    except Exception:
        fallback = fetch_icon_from_docker()  # Tier 3
        log_warning("ECMWF + IFS025 stall; using local ICON mirror")
```

---

## Authority & Data License

- **IFS HRES 9 km:** CC-BY 4.0 (ECMWF open-data since Oct 1, 2025)
- **All open-meteo redistributions:** CC-BY 4.0
- **AWS Open Data Sponsorship:** Free egress within region
- **OM-file format:** Apache 2.0 (libraries) + CC-BY 4.0 (data)

Commercial use requires:
- License: Open-Meteo commercial tier (€29–€99/month) OR self-hosted AGPLv3
- Data: Attribution to ECMWF + Open-Meteo

---

## References & URLs

1. **Standard Forecast API:** https://open-meteo.com/en/docs/ecmwf-api
2. **Single Runs API:** https://open-meteo.com/en/docs/single-runs-api
3. **Single Runs Blog:** https://openmeteo.substack.com/p/single-runs-api
4. **Model Updates Status:** https://open-meteo.com/en/docs/model-updates
5. **AWS Open Data (S3 bucket):** https://github.com/open-meteo/open-data
6. **OM-File Format Library:** https://github.com/open-meteo/om-file-format
7. **Self-Hosting Guide:** https://github.com/open-meteo/open-meteo/blob/main/docs/getting-started.md
8. **AWS Registry Entry:** https://registry.opendata.aws/open-meteo
9. **Pricing:** https://open-meteo.com/en/pricing
10. **ECMWF Open Data Announcement:** https://www.ecmwf.int/en/about/media-centre/news/2025/ecmwf-makes-its-entire-real-time-catalogue-open-all

---

## Appendix: OM-File Python Access Example

```python
from openmeteo import Client
import openmeteo_requests

client = Client(requests_session=requests.Session())

# Rolling timeseries (via S3 direct, requires om-files library)
# Note: Python library still in early stage; recommend AWS SDK + om-files binding
import boto3
s3 = boto3.client('s3', config=botocore.config.Config(signature_version=botocore.UNSIGNED))
s3.download_file(
    'openmeteo',
    'data/ecmwf_ifs/temperature_2m/year_2026.om',
    'year_2026.om'
)

# Then read with openmeteo library (once Python bindings mature)
# from openmeteo import omfiles
# data = omfiles.read_chunk('year_2026.om', lat_idx, lon_idx, time_start, time_end)
```

---

**End of Report**
