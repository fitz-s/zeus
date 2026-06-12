# Uncovered Cities Regional Model Survey
**Date:** 2026-06-09  
**Methodology:** previous-runs API only (fixed lead-1), settlement-graded MAE/bias  
**Authority basis:** open-meteo previous-runs API probes + zeus-forecasts.db settlements  

---

## 1. Current Coverage Gaps

54 cities total in `config/cities.json`. Regional experts currently active:
- **icon_d2** (2km, Central-EU box, lead≤1)
- **icon_eu** (7km, lat 29–71 / lon −24..45, lead≤3)
- **meteofrance_arome_france_hd** (1.3km, France+Po valley, lead≤1)

**Cities with NO regional expert** (39 cities across 7 clusters):

| Cluster | Cities |
|---------|--------|
| CONUS/US | Atlanta, Austin, Chicago, Dallas, Denver, Houston, Los Angeles, Miami, NYC, San Francisco, Seattle |
| Canada | Toronto |
| East Asia / Korea | Tokyo, Seoul, Busan, Taipei |
| China | Beijing, Chengdu, Chongqing, Guangzhou, Jinan, Qingdao, Shanghai, Shenzhen, Wuhan, Zhengzhou |
| SE Asia / Pacific | Hong Kong, Jakarta, Kuala Lumpur, Manila, Singapore, Wellington, Auckland |
| MENA / S-Asia | Ankara, Istanbul, Jeddah, Karachi, Lucknow, Tel Aviv |
| LatAm / Africa | Buenos Aires, Cape Town, Lagos, Mexico City, Panama City, Sao Paulo |

(Europe cities Amsterdam, Helsinki, London, Madrid, Milan, Moscow, Munich, Paris, Warsaw already have icon_eu/icon_d2/arome coverage or are at incumbent parity — see §5 below.)

---

## 2. Candidate Model Survey — Previous-Runs API Availability

All models probed at representative city coordinates. URL pattern used:
```
https://previous-runs-api.open-meteo.com/v1/forecast?
  latitude=..&longitude=..&hourly=temperature_2m_previous_day1
  &models=<id>&past_days=200&timezone=<tz>&forecast_days=1
```

| Model ID | Domain | Previous-Runs Available | History Start | n_days (200-day probe) | Notes |
|----------|--------|------------------------|---------------|------------------------|-------|
| `ncep_nbm_conus` | CONUS + southern Canada | **YES** | 2025-11-21 | 201 | Covers all US cities + Toronto |
| `gem_hrdps_continental` | Canada + northern US border | **YES** | 2025-11-21 | 200 | Toronto, NYC, Chicago, Seattle; NOT Houston/Dallas/Denver/Miami/Austin/SF |
| `jma_msm` | Japan (5km) | **YES** | 2025-11-22 | 201 | Tokyo only |
| `kma_seamless` | Korea | **YES but DISCONTINUED** | 2025-11-22 | 142 | Archive STOPPED 2026-04-12; live forecast still works |
| `kma_ldps` | Korea (1.5km) | **YES but DISCONTINUED** | 2025-11-22 | 69 | 63-day gap Dec–Feb; archive stopped 2026-04-02 |
| `cma_grapes_global` | Global (China NWP) | **YES** | 2025-11-22 | 188 | |
| `bom_access_global` | Australia/NZ | **NO — all nulls** | — | 0 | Returns time array but all null values; unusable |
| `ukmo_uk_deterministic_2km` | UK only | **YES (UK only)** | 2025-11-21 | 201 | Out-of-domain cities return non-JSON 400 |
| `ukmo_global_deterministic_10km` | Global | **YES** | 2025-11-21 | 201 | Covers all cities |
| `knmi_harmonie_arome_netherlands` | Netherlands | **YES** | 2025-11-21 | 201 | |
| `dmi_harmonie_arome_europe` | Europe (Denmark/Nordic focus) | **YES** | 2025-11-21 | 201 | |
| `metno_nordic` | Nordic | **YES** | 2025-11-21 | 201 | |
| `italia_meteo_arpae_icon_2i` | Italy | **YES** | 2025-11-21 | ~200 | Already inside icon_d2 box |
| `meteoswiss_icon_ch1` | Switzerland/Alps | **YES** | 2025-11-21 | ~200 | Already inside icon_d2 box |

**KEY FINDING: `bom_access_global` is NOT usable** — the previous-runs API returns a non-null hourly array but all values are `null` for both Wellington/Auckland (Pacific/Auckland) and Sydney test coordinates. Wellington and Auckland have no viable regional model on this API.

---

## 3. Settlement-Graded MAE/Bias Comparisons

All comparisons use `temperature_2m_previous_day1` (fixed lead-1 day, previous-run API), daily max computed from full 24-hour local-time window (>=18 valid hours required). Settlements converted F->C before residuals. Dates must overlap between forecast archive and settlement records.

### 3.1 CONUS / US Cities: `ncep_nbm_conus` vs Incumbents

n=69–116 per city (settlements from zeus-forecasts.db, dates 2025-12-04 to 2026-06-07).  
All values in **°C**.

| City | n | MAE_nbm | bias_nbm | MAE_ecmwf | MAE_icon | Best |
|------|---|---------|----------|-----------|----------|------|
| Atlanta | 112 | **1.132** | −0.258 | 1.189 | 1.331 | nbm |
| Chicago | 113 | **1.427** | −0.414 | 1.717 | 1.578 | nbm |
| Dallas | 113 | **1.187** | −0.018 | 1.518 | 1.199 | nbm |
| Miami | 113 | **0.828** | −0.504 | 1.061 | 0.878 | nbm |
| NYC | 116 | 1.517 | −0.474 | 1.972 | **1.307** | icon |
| Denver | 69 | 1.373 | −0.355 | 1.496 | **1.187** | icon |
| Seattle | 114 | **1.109** | −0.234 | 1.214 | 1.276 | nbm |
| Los Angeles | 69 | **1.040** | −0.310 | 1.429 | 1.219 | nbm |
| Houston | 69 | **0.904** | +0.057 | 1.057 | 1.000 | nbm |
| Austin | 70 | 1.305 | +0.053 | 1.292 | **1.091** | icon |
| San Francisco | 71 | 1.262 | −0.572 | **1.184** | 2.926 | ecmwf |
| **POOLED** | **1029** | **1.193** | | 1.395 | 1.339 | **nbm** |

**POOLED NBM vs ECMWF: −0.201°C MAE improvement (−14.4%)**  
**POOLED NBM vs ICON: −0.146°C MAE improvement (−10.9%)**

**Anomaly — San Francisco icon_global:** MAE=2.926, bias=−2.415°C. ICON_global badly resolves the SFO coastal microclimate; NBM (1.262) and ECMWF (1.184) are substantially better. This is a known ICON limitation for coastal fog zones.

**NBM CORRELATION CONCERN:** NBM is a statistical blend of NCEP models (GFS, NAM, HREF, etc.) — it is NOT decorrelated from GFS, which is already the `gfs_global` member in the current BAYES_PRECISION_FUSION ensemble. Adding NBM to the ensemble as a 5th decorrelated global would violate the decorrelation assumption. **Recommended role: anchor replacement only** (replace or supplement `ecmwf_ifs` as anchor for CONUS, not as an additive ensemble member), OR use as a standalone per-city signal post-Platt, not as a decorrelated fusion member.

### 3.2 Canada: Toronto

`gem_hrdps_continental` (2.5km) vs `ncep_nbm_conus` vs incumbents (n=115–116):

| Model | n | MAE | bias |
|-------|---|-----|------|
| gem_hrdps_continental | 115 | 1.441 | +0.702 |
| ncep_nbm_conus | 116 | **1.439** | −0.475 |
| ecmwf_ifs | 116 | 1.621 | −0.355 |
| icon_global | 116 | 1.513 | −0.675 |

GEM HRDPS and NBM are statistically tied for Toronto (MAE difference < 0.002°C). GEM HRDPS has a +0.702°C warm bias vs NBM's −0.475°C cold bias. GEM HRDPS covers NYC, Chicago, Seattle in addition to Toronto, but shows large warm biases for those border cities (Chicago +1.136, Seattle +0.909) making NBM strongly preferred for US cities.

**GEM HRDPS coverage:** Confirmed data for Toronto, NYC, Chicago, Seattle. Returns error for Houston, San Francisco, Austin, Miami, Denver, Dallas (those are outside its southern domain boundary approximately 40–41°N).

### 3.3 Japan: Tokyo — `jma_msm`

n=84 (settlements 2026-03-10 to 2026-06-08):

| Model | n | MAE | bias |
|-------|---|-----|------|
| jma_msm | 84 | 1.485 | **−1.213** |
| ecmwf_ifs | 84 | **1.094** | −0.658 |
| icon_global | 84 | **1.020** | +0.106 |

**JMA MSM REFUTED for Tokyo.** MAE 1.485 is 36% worse than ICON (1.020) and 36% worse than ECMWF (1.094). The −1.213°C systematic cold bias is large and consistent. ECMWF and ICON both beat JMA MSM for Haneda airport settlement. **NO REGIONAL UPGRADE AVAILABLE for Japan on the previous-runs API.**

### 3.4 Korea: Seoul / Busan — `kma_seamless`, `kma_ldps`

**Critical API status issue:** Both KMA models have stopped archiving on the previous-runs API:
- `kma_seamless`: last archived date 2026-04-12 (no data Apr 13 onwards)
- `kma_ldps`: last archived date 2026-04-02 + major 63-day gap Dec 2025–Feb 2026

Seoul comparison (on overlapping period only, n=61 for seamless, n=42 for LDPS):

| Model | n | MAE | bias | Note |
|-------|---|-----|------|------|
| kma_ldps | 42 | **1.412** | −0.402 | Archive stopped 2026-04-02 |
| kma_seamless | 61 | 1.808 | −0.149 | Archive stopped 2026-04-12 |
| ecmwf_ifs (same dates) | 61 | 1.752 | −1.523 | Severe cold bias on overlap period |
| icon_global (same dates) | 61 | 2.321 | — | Worst |

On the overlap dates, KMA LDPS (1.412) was the best available model — but the archive has stopped. **KMA models CANNOT be added to fusion as new members without a self-archiving pipeline.** The previous-runs API no longer provides fresh daily data for either KMA model.

**Note on Seoul ECMWF cold bias:** Over the full settlement window (n=112), ECMWF shows bias=−1.755°C for Seoul. This is large for a mid-latitude city — likely Gimpo airport urban heat island not captured at the ECMWF grid cell. Busan is much better (ECMWF bias=−0.003). The Platt de-biaser absorbs this but the raw signal is poor.

### 3.5 China: `cma_grapes_global`

n=37–81 per city:

| City | n | MAE_cma | bias_cma | MAE_ecmwf | MAE_icon | Best |
|------|---|---------|----------|-----------|----------|------|
| Beijing | 74 | 2.504 | −1.918 | 1.682 | **1.423** | icon |
| Shanghai | 81 | 1.969 | −1.663 | **1.252** | 1.822 | ecmwf |
| Chengdu | 74 | 3.042 | −2.926 | 1.630 | 1.974 | ecmwf |
| Chongqing | 74 | 1.708 | −0.954 | 1.723 | **1.281** | icon |
| Guangzhou | 49 | 1.773 | −1.018 | 1.822 | 1.788 | cma (marginal) |
| Shenzhen | 74 | 1.380 | −0.653 | **1.173** | 1.959 | ecmwf |
| Wuhan | 74 | 1.657 | −0.370 | 1.430 | **1.154** | icon |
| Qingdao | 37 | **1.516** | −0.732 | 1.659 | 1.157 | icon |
| **POOLED** | **537** | **1.981** | | 1.522 | 1.591 | ecmwf |

**CMA GRAPES REFUTED.** Pooled MAE 1.981 vs ECMWF 1.522 (+30% worse). Systematic cold bias across all cities (−0.4 to −2.9°C). Only Guangzhou shows marginal CMA advantage (by 0.05°C, n=49, not significant). **NO REGIONAL UPGRADE for China.**

Jinan and Zhengzhou have no settlements yet (n=0) — cannot be evaluated.

### 3.6 SE Asia, Pacific, LatAm, Africa: `ukmo_global_deterministic_10km`

n=35–114 per city:

| City | n | MAE_ukmo | bias_ukmo | MAE_ecmwf | MAE_icon | Best |
|------|---|---------|----------|-----------|----------|------|
| Singapore | 81 | **0.911** | −0.230 | 1.551 | 1.012 | ukmo |
| Manila | 49 | **0.880** | +0.129 | 0.963 | 2.906 | ukmo |
| Taipei | 77 | **1.670** | — | 1.884 | 2.460 | ukmo |
| Hong Kong | 61 | **1.113** | −0.746 | 1.520 | 1.661 | ukmo |
| Kuala Lumpur | 61 | **1.139** | −0.648 | 1.672 | 1.180 | ukmo |
| Jakarta | 46 | 1.265 | −0.730 | 2.367 | **1.222** | icon |
| Mexico City | 64 | **0.930** | −0.352 | 1.166 | 1.392 | ukmo |
| Buenos Aires | 114 | 1.368 | −0.905 | 1.618 | **1.353** | icon |
| Wellington | 112 | **0.936** | −0.432 | 1.146 | 1.121 | ukmo |
| Toronto | 116 | 1.451 | −0.204 | 1.621 | 1.513 | ukmo |
| Cape Town | 55 | 1.025 | −0.425 | 1.205 | **0.936** | icon |
| Lagos | 35 | **1.523** | +0.003 | 1.820 | 1.529 | ukmo |
| Karachi | 50 | 1.814 | +1.726 | **1.104** | 1.508 | ecmwf |
| Lucknow | 90 | 1.730 | +1.250 | **1.254** | 1.253 | icon |
| Panama City | 60 | 1.308 | −0.688 | 1.365 | **0.938** | icon |
| Sao Paulo | 105 | 1.477 | −1.180 | **1.134** | 1.049 | icon |
| **POOLED** | **1099** | **1.266** | | 1.411 | 1.326 | ukmo |

**POOLED UKMO vs ECMWF: −0.145°C MAE improvement (−10.3%)**  
**POOLED UKMO vs ICON: −0.060°C MAE improvement (−4.5%)**

UKMO global is particularly strong for Singapore (−0.640 vs ECMWF), Hong Kong (−0.407), Kuala Lumpur (−0.533), Mexico City (−0.236), and Wellington (−0.210).

UKMO is weak for: Karachi (+0.710 worse than ECMWF, large warm bias +1.726°C) and Lucknow (+0.476 worse, warm bias +1.250°C) — South Asian monsoon zone representativeness issue.

### 3.7 European Gap Cities: KNMI/DMI/MetNo/UKMO UK

These cities are covered by icon_eu but tested for sub-regional upgrades:

| City | Model tested | n | MAE_new | MAE_ecmwf | MAE_icon | Result |
|------|-------------|---|---------|-----------|----------|--------|
| Amsterdam | knmi_harmonie_arome_netherlands | 63 | 2.521 | **0.938** | 1.505 | REFUTED (bias=+2.340°C) |
| Helsinki | metno_nordic | 63 | 1.249 | **0.897** | 0.917 | REFUTED |
| Helsinki | dmi_harmonie_arome_europe | 63 | 1.070 | **0.897** | 0.917 | REFUTED |
| London | ukmo_uk_deterministic_2km | 112 | **0.919** | 1.039 | 0.994 | RECOMMEND |
| Warsaw | dmi_harmonie_arome_europe | 80 | 1.241 | 0.931 | **0.882** | REFUTED |

**KNMI AROME REFUTED** for Amsterdam: +2.340°C warm bias, MAE 2.521 (2.7x worse than ECMWF). The KNMI model resolves inland grid cells while EHAM airport sits on coastal polder terrain — severe station representativeness mismatch.

**UKMO UK 2km RECOMMENDED for London:** MAE 0.919 vs ECMWF 1.039 (−11.5% improvement), near-zero bias (+0.001°C). Domain is UK-only; fails with non-JSON error for all non-UK cities.

---

## 4. Verdicts by Cluster

### CONUS / US (11 cities)
**VERDICT: RECOMMEND `ncep_nbm_conus` as anchor supplement/replacement**

- Model ID: `ncep_nbm_conus`
- Cities: Atlanta, Austin, Chicago, Dallas, Denver, Houston, Los Angeles, Miami, NYC, San Francisco, Seattle + Toronto
- Pooled MAE improvement: −0.201°C vs ECMWF (−14.4%), −0.146°C vs ICON (−10.9%)
- History: starts 2025-11-21, continuous, 201 days available
- Domain polygon: approximately 23°N–50°N, 125°W–66°W (confirmed: covers all US cities including Toronto; fails for Beijing, Tokyo)
- Max lead: Previous-runs API confirmed lead-1; live API supports longer leads

**CRITICAL CONSTRAINT:** NBM is a statistical blend of NCEP models including GFS. Adding NBM as a decorrelated global violates BAYES_PRECISION_FUSION's decorrelation assumption (GFS already present as `gfs_global`). **Recommended deployment role:**
1. **Anchor replacement for CONUS:** Substitute NBM for ECMWF IFS as the deterministic anchor on CONUS cities
2. **OR:** Post-Platt as a separate channel signal, treating it as GFS-correlated
3. **Do NOT add as a decorrelated fusion member** alongside gfs_global

Individual city exceptions: SF prefers ECMWF (1.184) over NBM (1.262); NYC and Denver prefer icon_global.

### Canada (Toronto)
**VERDICT: NO BETTER OPTION — NBM already covers Toronto**

NBM and GEM HRDPS are essentially tied (MAE 1.439 vs 1.441). NBM already covers Toronto as part of its CONUS domain. GEM HRDPS warm bias (+0.702) is worse than NBM cold bias (−0.475). No advantage to adding GEM HRDPS separately.

### Japan (Tokyo)
**VERDICT: NO-BETTER-OPTION — JMA MSM REFUTED**

JMA MSM MAE=1.485, bias=−1.213°C vs ICON_global 1.020 (incumbent best). No regional model improves on current incumbents for Tokyo. Remains with ECMWF+ICON+GFS.

### Korea (Seoul, Busan)
**VERDICT: INSUFFICIENT DATA — KMA archive discontinued**

KMA seamless and KMA LDPS both stopped archiving on the previous-runs API (last dates 2026-04-12 and 2026-04-02). Cannot be used for forward calibration. On the available overlap window, KMA LDPS showed promising MAE=1.412 vs ECMWF=1.802 (Seoul, n=42), but data is no longer available.

Flag for monitoring. Seoul ECMWF cold bias (−1.755°C) is a calibration concern — Platt de-biaser should absorb this.

### China (10 cities)
**VERDICT: NO-BETTER-OPTION — CMA GRAPES REFUTED**

CMA GRAPES pooled MAE=1.981 vs ECMWF 1.522 (+30% worse). Systematic cold biases −0.4 to −2.9°C. No Chinese regional model offers improvement. Jinan and Zhengzhou have zero settlement records — cannot evaluate.

### SE Asia / Pacific / LatAm / Africa
**VERDICT: RECOMMEND `ukmo_global_deterministic_10km` as 5th decorrelated global**

UKMO is a distinct NWP system (UK Met Office dynamical core), decorrelated from current 4 globals. Can serve as either decorrelated 5th member or as city-specific anchor for SE Asia/Pacific/Mexico.

- **Strong wins:** Singapore (−0.640°C vs ECMWF), KL (−0.533°C), HK (−0.407°C), Wellington (−0.210°C), Mexico City (−0.236°C), Taipei (−0.214°C), Lagos (−0.297°C)
- **Loses:** Karachi (+0.710°C), Lucknow (+0.476°C), Sao Paulo (+0.343°C) — do NOT use as anchor for South Asia
- **Jakarta:** icon_global (1.222) remains best; ECMWF catastrophic here (2.367)
- **BOM ACCESS unavailable** for Wellington/Auckland — no NZ-specific model

### Europe gaps (Amsterdam, Helsinki, London, Warsaw)
**VERDICT: RECOMMEND `ukmo_uk_deterministic_2km` for London only; all others NO-BETTER-OPTION**

London: UKMO UK 2km (0.919) beats ECMWF (1.039) and icon_global (0.994). KNMI/DMI/MetNo all refuted for their respective cities.

---

## 5. Model Maturity for MIN_TRAIN=25 Walk-Forward De-bias

| Model | Archive start | n_settlement_overlap | Mature? |
|-------|--------------|---------------------|---------|
| ncep_nbm_conus | 2025-11-21 | 69–172 (city-dep.) | YES all US cities |
| ukmo_global_deterministic_10km | 2025-11-21 | 35–172 | YES all tested cities (Auckland n=0 — no settlements yet) |
| ukmo_uk_deterministic_2km | 2025-11-21 | 112 (London) | YES |
| jma_msm | 2025-11-22 | 84 | YES (but refuted) |
| kma_seamless | stopped 2026-04-12 | ~61 | BLOCKED — archive stopped |
| kma_ldps | stopped 2026-04-02 | ~42 | BLOCKED — archive stopped |
| cma_grapes_global | 2025-11-22 | 37–81 | YES (but refuted) |

---

## 6. Priority Recommendations

### Priority 1 — RECOMMEND

**A. `ncep_nbm_conus` — CONUS anchor**
- Pooled MAE gain: −0.201°C vs ECMWF (n=1029, settlement-graded)
- Role: Anchor replacement for CONUS (not decorrelated ensemble member)
- Caveats: GFS-correlated; SF exception (ECMWF preferred); NYC/Denver/Austin prefer icon_global

**B. `ukmo_global_deterministic_10km` — Global 5th member**
- Pooled MAE gain: −0.145°C vs ECMWF across 16 non-European cities (n=1099)
- Role: Decorrelated 5th global member; strongest benefit for SE Asia/Pacific/LatAm
- Caveats: Karachi and Lucknow UKMO performs worse (+0.7, +0.5°C vs ECMWF) — exclude from anchor role for South Asia

**C. `ukmo_uk_deterministic_2km` — London specialist**
- MAE gain: −0.120°C vs ECMWF (n=112), near-zero bias
- Role: Regional expert for London (parallel to icon_d2 role)
- Domain strictly UK-only

### Priority 2 — MONITOR

**`kma_seamless` / `kma_ldps` — Korea (if archive resumes)**
- KMA LDPS showed MAE=1.412 vs ECMWF=1.802 for Seoul (n=42 overlap)
- Blocked: archive discontinued April 2026
- Action: check open-meteo monthly; do not add until MIN_TRAIN=25 satisfied on fresh data

### Priority 3 — REFUTED (do not add)

| Model | Reason |
|-------|--------|
| `ncep_hrrr_conus` | Previously refuted: fixed-lead MAE worst among US models (prior evidence §CRITICAL) |
| `jma_msm` | MAE=1.485 vs ICON=1.020 for Tokyo; −1.213°C cold bias |
| `cma_grapes_global` | Pooled MAE +30% worse than ECMWF; −0.4 to −2.9°C cold bias |
| `knmi_harmonie_arome_netherlands` | MAE=2.521 vs ECMWF=0.938 for Amsterdam; +2.340°C warm bias |
| `dmi_harmonie_arome_europe` | Worse than incumbents for Helsinki and Warsaw |
| `metno_nordic` | Worse than ECMWF for Helsinki |
| `bom_access_global` | Previous-runs API returns all-null values; unusable for NZ/AU |
| `gem_hrdps_continental` | Large warm biases for US border cities; tied with NBM for Toronto |
| `kma_ldps` | Archive discontinued 2026-04-02 |
| `kma_seamless` | Archive discontinued 2026-04-12 |

---

## 7. Anomalies Flagged

1. **icon_global at San Francisco:** MAE=2.926, bias=−2.415°C. ICON poorly resolves SFO coastal fog zone. NBM (1.262) or ECMWF (1.184) should be preferred for SF anchor.

2. **icon_global at Jeddah:** MAE=4.918, bias=−4.468°C. ICON catastrophically wrong for hot desert. ECMWF (1.311) is the only viable incumbent for Jeddah. Consider removing ICON from the Jeddah fusion weight entirely.

3. **ECMWF cold bias cluster for tropical airports:** Jakarta −2.298°C, KL −1.495°C, Singapore −1.370°C, HK −1.267°C, Seoul −1.755°C. These large biases are partially absorbed by Platt but UKMO global substantially reduces raw error for this cluster.

4. **Auckland has zero settlement records** — cannot evaluate any model for Auckland. No NZ regional model is available anyway (BOM ACCESS null).

---

## Appendix: API URLs Tested

```
# NBM CONUS (Atlanta) — confirmed 201 days
https://previous-runs-api.open-meteo.com/v1/forecast?latitude=33.630&longitude=-84.442&hourly=temperature_2m_previous_day1&models=ncep_nbm_conus&past_days=200&timezone=America%2FNew_York&forecast_days=1

# UKMO Global (Singapore) — confirmed 201 days
https://previous-runs-api.open-meteo.com/v1/forecast?latitude=1.368&longitude=103.982&hourly=temperature_2m_previous_day1&models=ukmo_global_deterministic_10km&past_days=200&timezone=Asia%2FSingapore&forecast_days=1

# UKMO UK 2km (London) — confirmed 201 days
https://previous-runs-api.open-meteo.com/v1/forecast?latitude=51.505&longitude=0.055&hourly=temperature_2m_previous_day1&models=ukmo_uk_deterministic_2km&past_days=200&timezone=Europe%2FLondon&forecast_days=1

# JMA MSM (Tokyo) — confirmed 201 days
https://previous-runs-api.open-meteo.com/v1/forecast?latitude=35.553&longitude=139.781&hourly=temperature_2m_previous_day1&models=jma_msm&past_days=200&timezone=Asia%2FTokyo&forecast_days=1

# KMA seamless (Seoul) — DISCONTINUED (last archived 2026-04-12)
https://previous-runs-api.open-meteo.com/v1/forecast?latitude=37.469&longitude=126.451&hourly=temperature_2m_previous_day1&models=kma_seamless&past_days=200&timezone=Asia%2FSeoul&forecast_days=1

# BOM ACCESS global (Wellington) — ALL NULLS, unusable
https://previous-runs-api.open-meteo.com/v1/forecast?latitude=-41.331&longitude=174.806&hourly=temperature_2m_previous_day1&models=bom_access_global&past_days=10&timezone=Pacific%2FAuckland&forecast_days=1
```

*Report generated 2026-06-09. Settlements from zeus-forecasts.db (read-only). All API calls to previous-runs-api.open-meteo.com. No DB writes performed.*
