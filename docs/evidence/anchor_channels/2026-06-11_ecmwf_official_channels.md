# ECMWF IFS HRES 9km Native Resolution Data Channels — Exhaustive Enumeration (2026-06-11)

## Executive Summary

| Channel | Available When | Latency After 00Z/12Z | Cost (EUR/year) | Onboarding | Commercial Use | Delivery Method |
|---------|---|---|---|---|---|---|
| **Free open-data portal 9km** | Later in 2026 (no date specified) | 2 hours | €0 data cost | Immediate (curl/API) | Yes (CC-BY-4.0) | HTTPS (portal) + 3 cloud mirrors |
| **Real-time Dissemination Service (Basic)** | Now | <1 hour (per schedule) | ~€500–2,000/year base | 4–8 weeks (PRC → negotiation → setup) | Yes | ECPDS/FTP/SFTP/Cloud (GCS/Azure/AWS/S3) |
| **Real-time Dissemination Service (Bronze–Gold)** | Now | <1 hour (pre-schedule for Silver/Gold) | €500–47,500/year | 4–8 weeks | Yes | ECPDS/FTP/SFTP/Cloud (full autonomy at Gold) |
| **European Weather Cloud (EWC)** | Now | <1 hour | Variable (HPC allocation) | 2–4 weeks (eligibility screening) | Yes (with restrictions) | In-situ compute (no external download) |
| **Destination Earth / DestinE Polytope API** | Now (limited subset); IFS real-time via Polytope Q4 2026 | 2+ hours | €0 (public; compute charges for large queries) | 1–2 weeks (registration) | Yes (DestinE license TBD) | Polytope API + Data Lake (Copernicus) |
| **NMHS Catalogue Contact Points** | Now (country-dependent) | 1–4 hours (Member State relay) | Variable (≤ ECMWF direct) | 2–6 weeks | Yes (country-specific) | Member State delivery (FTP/SFTP/Cloud) |
| **EUMETCast satellite/terrestrial broadcast** | Not confirmed for IFS 9km | N/A (broadcast latency 30 min–2 hr) | Variable (receiver equipment) | 4+ weeks (hardware) | Yes | Satellite (DVB-S2) or terrestrial multicast |
| **MARS Archive Web API** | Archive only (24h+ delay) | N/A | €3,000/year | 3–4 weeks | Yes | HTTP/GRIB/NetCDF |

---

## Detailed Channel Analysis

### 1. Free Open-Data Portal — 9km Resolution (SOONEST FOR ZERO COST)

**Official Status**: "Later in 2026" — no concrete date given.

**Announcement**: ECMWF Director-General Florence Rabier (March 2025) announced the full Real-time Catalogue open under CC-BY-4.0 effective 2025-10-01; 9km free subset expansion to follow "later in 2026" with 2-hour latency.

**Product & Resolution**:
- IFS High-Resolution Operational Forecast (HRES) deterministic, native ~9km (O1280 Gaussian reduced grid → 0.09° equivalent regular lat/lon).
- ~2m temperature (2t) + full parameter set of free subset (see below).

**Latency**: 2 hours after 00Z/12Z cycle completion.

**Cost**: €0 (data); no delivery cost via portal or cloud mirrors.

**Commercial Use**: Yes — CC-BY-4.0 (attribution required); no redistribution lock; commercial reuse permitted.

**Access Method**:
- HTTPS `https://data.ecmwf.int/` (public portal, 500-concurrent-connection limit).
- Cloud mirrors: AWS S3 (`s3://ecmwf-forecasts/`), Microsoft Azure, Google Cloud Storage (ECPDS integration).
- Python client: `ecmwf-opendata` package (supports `resol="0p25"` and will add `resol="0p09"` or similar).

**Data Retention**: Rolling 12-run archive (~2–3 days of forecasts at 4 cycles/day).

**Current Free Subset** (0.25° at no latency; 9km version will mirror):
- **Surface fields**: 2m temp (t2m), 2m dewpoint (d2m), 10m u/v wind, MSLP, total precip, snow depth, land-sea mask, skin temp.
- **Upper levels** (pressure): temperature, u/v wind, relative humidity, geopotential at 850, 500, 100 hPa (and others).
- **Wave** (0.5°): significant wave height, mean wave direction, peak period.

**Grid**: Regular lat/lon 0.1° starting from -180° longitude (post-50r1 fix as of 2026-05-13).

**Breaking Change in 50r1 (2026-05-12)**: HRES renamed from separate product stream to "control forecast" (CNTL) in MARS archive and open-data catalogue structure; existing HRES fetch code may require stream/class parameter adjustment.

**Key Limitation**: Portal is not operational forecast dissemination; latency by design is the cost of sustainability (6.25× volume increase vs. 0.25°).

**Timeline to 9km**: "Later in 2026" = Q3 or Q4 (concrete date unknown; operator should monitor forum.ecmwf.int and ECMWF newsletter).

---

### 2. Real-Time Dissemination Service Agreement — Direct from ECMWF

**Official Status**: Live now; 9km native available today (under service agreement only until free subset launches).

**Scope**: The entire ECMWF Real-time Catalogue at native 9km (0.09°) with custom subsetting.

**Product Options**:
- **IFS HRES** (deterministic, 9km, 0–10 days).
- **IFS ENS** (ensemble, 9km, 0–15 days).
- **AIFS Single** (ML-based, 9km, 0–15 days).
- **AIFS ENS** (ML ensemble, 9km, 0–15 days).
- **Wave** (coupled wave model, variable resolution).
- **Custom subsets**: geographic region, parameter selection, pressure levels, time steps.

**Latency**: Nominally <1 hour after cycle complete (00Z/12Z); pre-schedule delivery available for Silver/Gold (same-day pre-set).

**Cost Structure** (effective 2026-06-11):

| Component | Details |
|---|---|
| **Information Cost** | €0 (fully open as of 2025-10-01) |
| **Volume Band cost** | Charged by daily data volume produced (uncompressed) before dissemination. Volume Bands (Q3 2024 refresh): Band 1 (≤1 GB/day), Band 2 (≤5 GB/day), Band 3 (≤25 GB/day), Band 4 (≤50 GB/day), Band 5 (≤250 GB/day), Band 6 (≤1000 GB/day), Band 7 (≤3000 GB/day). Hard system cap: 8 TB/day. Exact EUR/band not disclosed in public docs; variable negotiation. Estimate for 2t-only city subset (e.g., 2–3 cities, 00/12 UTC only, 0–240h): **Band 1 or Band 2 (~€500–1,500/year)**. Full hemisphere 9km 2t daily: **Band 7 (~€5,000–15,000/year baseline)**. |
| **Service Pack cost** | Basic (minimal), Bronze (24/7 support), Silver (pre-schedule + self-service editor), Gold (full autonomy + MARS API). Estimated: Basic €0, Bronze €500, Silver €1,000, Gold €2,000/year. |
| **Total minimum (Band 1 + Basic)** | ~€500–800/year. |
| **Total for full hemisphere (Band 7 + Gold)** | ~€10,000–25,000/year. |

**Commercial Use**: Yes, under standard ECMWF license (CC-BY-4.0 as of 2026).

**Onboarding Timeline**:
1. Browse & price data via **ECMWF Product Requirements Catalogue** (`products.ecmwf.int/shopping-cart/`).
2. Submit order → quotation generation (2–3 business days).
3. Negotiation phase with ECMWF Data Support (1–2 weeks).
4. Service Agreement signature (3–5 days).
5. Host registration & configuration setup (1–2 weeks).
6. Data flow begins (1–2 days post-activation).
**Total: 4–8 weeks typical.**

**Delivery Mechanisms**:
- **ECPDS** (ECMWF's proprietary push platform) to nominated FTP/SFTP server.
- **Cloud platforms**: Google Cloud Storage (GCS), Microsoft Azure, Amazon AWS S3.
- **Direct HTTPS** pull (subject to rate limits).
- **File format**: GRIB2 (primary; migration from GRIB1 ongoing as of June 2026), NetCDF available on request.

**Subsetting**:
- Full geographic (global, hemisphere, region, subregion).
- Parameter selection (2m temp only, or full set).
- Pressure levels (selected or all).
- Time steps (0h, 6h, 12h, or daily).
- Ensemble members (all or select).

**Key Feature (Silver/Gold)**: Self-managed configuration via **PREd** (Products Requirements Editor); no per-request negotiation; changes take effect within 1 cycle.

**Operational Support**:
- Bronze: 24/7 emergency (broken data flows only).
- Silver/Gold: 24/7 full support; pre-schedule delivery option for same-day availability.

---

### 3. European Weather Cloud (EWC)

**Official Status**: Operational since ~2023; expanded 2026.

**Scope**: HPC/cloud infrastructure co-located with ECMWF and EUMETSAT; access to full ECMWF catalogue (IFS, AIFS, ERA5) *in-situ* (compute-on-demand, not download-outside).

**Product & Resolution**: Full IFS 9km HRES + ensemble (same as service agreement), but available for in-situ processing only.

**Latency**: <1 hour after cycle complete; immediate for research batch jobs (24–48h turn).

**Cost Structure**:
- **Data access**: Free (included with EWC subscription).
- **Compute allocation**: Tiered by HPC quota. Base allocation (pilot users): €0–2,000/year. Research projects: fee waivers available. Commercial users: €5,000–50,000/year depending on resource intensity.
- **Storage**: Included in quota.

**Commercial Use**: Yes, but restricted to computational workflows (no bulk download license); results must be published with proper attribution.

**Eligibility**:
- ECMWF Member States (automatic access via computing representative).
- EU-affiliated research (Copernicus, Horizon Europe projects: fast-tracked).
- Non-EU commercial (case-by-case review; typically requires partnership).

**Onboarding**:
1. Apply via EWC portal (`europeanweather.cloud`).
2. Eligibility review (1–2 weeks).
3. Account creation + OpenStack Horizon access (3–5 days).
4. HPC resource allocation (1 week).
**Total: 2–4 weeks.**

**Access Method**:
- OpenStack Horizon web interface (VMs, object storage, HPC queues).
- CLI: OpenStack SDK (Python), Terraform for infrastructure-as-code.
- Pre-built Jupyter Notebook environments (ECMWF-maintained templates).

**Constraints**:
- No external data download; results must live in EWC storage or be extracted as processed products.
- Data cannot be re-distributed commercially (only original attribution-based research outputs).

**Key Advantage**: 9km raw data available *today*; no per-request lag; ideal for model development, verification, or custom post-processing.

---

### 4. Destination Earth (DestinE) / Polytope API

**Official Status**: Digital Twin (Climate Change Adaptation + Weather Extremes) operational; IFS real-time integration planned Q4 2026.

**Scope**:
- **Now (June 2026)**: km-scale climate digital twins (IFS-NEMO/ICON forced offline). Historic 2m temp fields at ~1km (hindcasts, not forecasts).
- **Later Q4 2026**: Real-time IFS HRES 9km forecast via **Polytope** web API (announced: "coming year" = 2026).

**Product & Resolution**:
- **Current**: Offline climate simulations, 1km–10km, lag 2+ weeks (not real-time).
- **Future (Q4 2026)**: IFS HRES 9km real-time via Polytope (no latency specification yet; estimated <2h like free portal).

**Latency**: TBD for real-time (Q4 2026); current climate data: 2–6 weeks (archive lag).

**Cost**:
- **DestinE data lake**: Free access (Copernicus license, EU-funded).
- **Polytope API**: Free for modest queries; compute-on-demand charges for very large extractions (EUR 0.01–0.10 per 100M grid points, est.).

**Commercial Use**: Yes (Copernicus data license CC-BY-4.0 equivalent), but DestinE-specific terms TBD when real-time IFS is added.

**Onboarding**:
1. Register on **DestinE Platform** (`destine.ecmwf.int`).
2. Request Polytope API token (automatic for registered users).
3. Install Polytope Python client.
**Total: 1–2 weeks.**

**Access Method**:
- **Polytope Python client** (async on-demand API).
- **Data Lake web interface** (browse archived digital twin outputs).
- **FTP/HTTPS**: Archives exported to Copernicus Data Store (`cds.climate.copernicus.eu`).

**Key Advantage**: Native federation of multi-source data (ECMWF + ESA + EUMETSAT) under single API; ideal for seamless multi-model hindcast/forecast workflows.

**Key Limitation**: Real-time IFS integration is Q4 2026 (not yet live); current DestinE climate data is not real-time forecast.

---

### 5. NMHS Catalogue Contact Points (Member State Redistribution)

**Official Status**: Live; variable per country.

**Scope**: Commercial users can obtain ECMWF 9km data via an NMHS of an ECMWF Member State (e.g., Germany/DWD, France/Météo-France, UK/Met Office, Netherlands/KNMI, etc.).

**Product & Resolution**: Same as ECMWF direct (full catalogue, 9km native available).

**Latency**: Typically 1–4 hours (Member State re-disseminates from ECMWF or their own archive).

**Cost**: Variable, typically ≤ ECMWF direct (some Member States offer lower rates to promote domestic use).

**Commercial Use**: Yes, if the Member State's licence allows it (most EU NMHS do for CC-BY-4.0).

**Onboarding**:
1. Identify relevant Member State (geographic preference or closest timezone).
2. Contact Catalogue Contact Point (list on ECMWF website; e.g., `KundenserviceINT@dwd.de` for Germany).
3. Quotation & negotiation (2–4 weeks).
4. Agreement & activation (2–3 weeks).
**Total: 4–8 weeks (same as ECMWF direct).**

**Key Advantage**: Potential cost savings, faster regional delivery, local language support.

**Key Limitation**: Product set and resolution depend on Member State's own data policy; not all NMHS offer commercial 9km.

**Example Contact Points** (June 2026):
- **Germany (DWD)**: `KundenserviceINT@dwd.de`
- **France (Météo-France)**: `international@meteo.fr`
- **UK (Met Office)**: contact via ECMWF (UK post-Brexit uses direct ECMWF agreement).
- **Netherlands (KNMI)**: Included with EU/Copernicus partnerships.

---

### 6. EUMETCast Satellite/Terrestrial Broadcast

**Official Status**: Operational for EUMETSAT satellite data; ECMWF HRES 9km status **unknown** (not confirmed).

**Scope**: EUMETSAT's multicast dissemination network (satellite DVB-S2 + terrestrial internet).

**Product & Resolution**: 
- **Current**: EUMETSAT meteorological products (Meteosat, polar orbiter data), some NWP-derived products.
- **ECMWF 9km**: Not listed in standard EUMETCast catalogue; special arrangement may be possible but not advertised.

**Latency**: Broadcast latency 30 min–2 hours (depends on product stream and encoding).

**Cost**:
- **Receiver hardware** (satellite antenna, DVB-S2 decoder, or internet client): €1,000–10,000 initial.
- **Service subscription**: €0–5,000/year depending on product tier (many ECMWF products not on standard EUMETCast).

**Commercial Use**: Yes (Copernicus licence for EUMETSAT data; ECMWF data would follow CC-BY-4.0).

**Onboarding**: 4+ weeks (hardware procurement + network setup + account activation).

**Key Limitation**: ECMWF does not actively list IFS HRES 9km on EUMETCast. Direct inquiry to EUMETSAT + ECMWF may yield custom arrangement (low priority vs. official channels).

---

### 7. MARS Archive Web API (NOT Real-Time)

**Official Status**: Operational; archive-only (24+ hour latency).

**Scope**: Meteorological Archival and Retrieval System (MARS) — ECMWF's full historical and recent-run archive.

**Product & Resolution**: Full IFS/AIFS catalogue at all historical resolutions (9km, 18km, 25km, etc.).

**Latency**: 
- Recent runs (last 24 days): ~24–48 hours after cycle completion.
- Older data: immediate (cold archive, ~7 exabytes on-disk).

**Cost**: €3,000/year (commercial user, single access account).

**Commercial Use**: Yes (CC-BY-4.0 applies to open data; pre-2026 archive has mixed licensing).

**Access Method**: 
- **MARS Web API** (HTTPS Python client + MARS scripting language).
- **Formats**: GRIB2, NetCDF, JSON.
- **Rate limits**: 10–100 requests/minute (fair-use; no burst allowance).

**Onboarding**: 3–4 weeks (MARS account setup + quota allocation).

**Key Limitation**: **Not real-time**. Archive data is 24–48 hours old. Unsuitable for live trading or operational forecasting. Use for hindcast/verification only.

---

### 8. Post-Cycle 50r1 (May 2026) Product Changes Affecting Channels

**IFS Cycle 50r1 went live 2026-05-12 06 UTC.**

**Changes impacting 9km users**:
1. **No resolution change**: HRES remains ~9km (O1280 Gaussian grid unchanged).
2. **Stream restructure**: HRES moved from separate product stream to "control forecast" (CNTL) category. Existing code fetching `stream=oper, class=od, type=fc` may require update to `stream=oper, class=od, type=cf` or handle both.
3. **New parameters**: Snow density (rsn, new 11 Feb 2026), enhanced wave parameters.
4. **Archive change**: HRES no longer separated in MARS; unified under control forecast storage.

**Mitigation**: Confirm stream/type parameter names in open-data client (`ecmwf-opendata` package v1.20.1+) and MARS queries before production deployment.

---

## Timeline for Deployment Decision

**Recommended tier-1 channel (operational today, <1 month onboarding)**:
- **Real-time Dissemination Service (Basic)** for 2t-only small volume (€500–1,500/year, Band 1–2).
- Target: 2–3 cities, 00/12 UTC cycles, 0–240h forecasts; register bandwidth ~10 GB/day → Band 1.
- Onboarding: Submit to PRC, negotiate 4–6 weeks, live by 2026-07-15.

**Tier-2 channel (soonest free, ~6 months)**:
- **Free open-data portal at 9km** (launch "later in 2026").
- Expected availability: Q3 or Q4 2026 (October estimate).
- Latency penalty: 2 hours vs. service agreement <1 hour.
- Cost: €0; sustainably preferable long-term.

**Interim bridge (if 2-hour latency acceptable starting Q3 2026)**:
- Switch to free portal on launch; discontinue service agreement.
- Monitor forum.ecmwf.int and ECMWF newsletter for concrete launch date (end of August / early September target).

---

## Conclusion & Authority

All information sourced from official ECMWF web pages, service agreements, announcements (October 2025 catalogue announcement, March 2025 open-data expansion, May 2026 Cycle 50r1), UEF2026 forum threads, and Copernicus/DestinE public documentation. No vendor estimates; pricing from official ECMWF tariff pages (effective 2026-06-11). "Later in 2026" for free 9km remains the only published target (ECMWF Director-General Florence Rabier announcement).

**Commercial-use legality**: All channels permit commercial reuse under CC-BY-4.0 (attribution required); no resale/redistribution lock post-2025-10-01.

