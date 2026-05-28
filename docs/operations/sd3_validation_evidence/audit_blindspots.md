# Zeus Data Blindspot Audit — 2026-05-28

---

## 1. ensemble_snapshots_v2: Cycle, Data Version, Lead Hours, Temperature Metric

### Cycle presence (issue_time)
All ECMWF OpenData snapshots are ingested from **both 00z and 12z** runs:

| issue_time | data_version | count |
|---|---|---|
| 00:00 | ecmwf_opendata_mx2t3_local_calendar_day_max_v1 | 5,930 |
| 12:00 | ecmwf_opendata_mx2t3_local_calendar_day_max_v1 | 5,488 |
| 00:00 | ecmwf_opendata_mn2t3_local_calendar_day_min_v1 | 4,190 |
| 12:00 | ecmwf_opendata_mn2t3_local_calendar_day_min_v1 | 5,124 |

TIGGE snapshots have no `issue_time` populated (NULL). The `calibration_pairs_v2.cycle` column shows 00z vs 12z stratification exists: **43.4M 00z pairs vs 4.7M 12z pairs**. The 12z TIGGE pairs are calibrated (267 Platt models at cycle=12, all VERIFIED/active) but 00z dominates heavily.

**06z/18z: NOT present.** ECMWF ENS only runs 00z and 12z operationally; no 06z/18z data exists in this pipeline.

### Data versions in DB

| data_version | rows |
|---|---|
| tigge_mx2t6_local_calendar_day_max_v1 | 397,457 |
| tigge_mn2t6_local_calendar_day_min_v1 | 384,202 |
| tigge_mn2t6_local_calendar_day_min_contract_window_v2 | 348,706 |
| ecmwf_opendata_mx2t3_local_calendar_day_max_v1 | 11,418 |
| ecmwf_opendata_mn2t3_local_calendar_day_min_v1 | 9,314 |
| ecmwf_opendata_mx2t6_local_calendar_day_max_v1 (OLD) | 1,342 |
| ecmwf_opendata_mn2t6_local_calendar_day_min_v1 (OLD) | 508 |

The two `*_v6*` rows (1,342 + 508 total) are deprecated — these were from before the ECMWF API switched from 6h to 3h aggregation windows (~2026-05-07). They appear in neither active Platt models nor active calibration paths.

### Lead hours
- Min: 0h, Max: ~252h (10.5 days); buckets by 24h:
  - Lead 0–6d: ~142,500–142,900 rows each (54 cities × ~2.6k snapshots per lead bucket)
  - Lead 7d: 140,184 rows (slightly smaller — some cities missing latest run)
  - Lead 8–10d: 370–370–285 rows — these are the **old ecmwf_opendata_mn2t6/mx2t6 legacy rows** only; they have no active calibration and are not consumed

### Temperature metric coverage

| temperature_metric | rows |
|---|---|
| low | 742,730 |
| high | 397,899 |

LOW has ~1.86× more snapshot rows than HIGH. The contract_window_v2 data_version is LOW-only (348,706 rows, zero HIGH equivalent).

---

## 2. City Coverage Matrix

| Table | Cities |
|---|---|
| ensemble_snapshots_v2 | 54 |
| settlements_v2 | 51 |
| calibration_pairs_v2 | 52 |
| model_bias_ens_v2 | 51 |

### Cities with snapshots but NO settlements
- **Auckland** — has 22,198 snapshots; no settlement_v2 row
- **Jinan** — 42 snapshots (partial onboard)
- **Zhengzhou** — 42 snapshots (partial onboard)

### Cities with snapshots but NO calibration
- **Jinan**, **Zhengzhou** — both partial-onboard, never completed calibration ingest

### Cities with snapshots but NO bias model
- **Auckland**, **Jinan**, **Zhengzhou**

**Auckland is the critical gap**: has 22k snapshots, Platt models (LOW cycle=00 and cycle=12, is_active=1, VERIFIED), and calibration_pairs_v2 rows — but zero settlements_v2 and zero model_bias_ens_v2 rows. It is calibrated but never settled/traded.

All 51 settled cities have calibration pairs. All 51 settled cities have a bias model. No gaps at the settled-city level.

---

## 3. Raw Files: Downloaded but Not Ingested

### open_ens_mn2t6_localday_min / open_ens_mx2t6_localday_max
These directories contain **23,918 JSON files across 55 cities** (10,893 LOW + 13,025 HIGH). The files cover leads 0–10 days per city per run date. **Zero rows in ensemble_snapshots_v2 carry an open_ens data_version.** The `ingest_backend` column shows all 1.14M rows as `'unknown'` — the open_ens ingest backend was never plumbed into the DB writer. The files are downloaded by `src/data/ecmwf_open_data.py` (`_run_opendata_track`) but the per-city JSON files in `open_ens_*/` go to disk and stop there.

The ingested ECMWF OpenData rows in the DB (11,418 + 9,314) come from a separate ingest path (real-time API → DB writer), not these JSON dumps.

### tigge_ecmwf_ens_regions_mn2t6 / tigge_ecmwf_ens_regions_mx2t6
Regional (non-city) TIGGE files in 4 geographic subfolders (americas, asia, europe_africa, oceania). These are referenced only by `scripts/extract_tigge_mx2t6_localday_max.py` and `scripts/extract_tigge_mn2t6_localday_min.py` — extraction scripts, not ingest paths. Data sits in raw files; it is consumed by the extract scripts to build city-level snapshots, so this is the *source* material, not a blindspot per se.

### Variables beyond mx2t/mn2t: NONE PRESENT
The raw dir contains only temperature products (mn2t6, mx2t6, mn2t3, mx2t3). No precipitation, wind, 2m hourly temperature (2t), or solar files were found in `51 source data/raw/`. The `solar/` subdirectory exists but contains no files matching GRIB weather variables — it appears to be solar irradiance for diurnal curve seeding only.

---

## 4. Data Versions Present in DB but Not Referenced in Code

| data_version | DB rows | Code reference |
|---|---|---|
| ecmwf_opendata_mx2t6_local_calendar_day_max_v1 | 1,342 | Job name keeps `mx2t6` slug for back-compat; actual writer now produces `mx2t3_*` — these 1,342 rows are stale legacy |
| ecmwf_opendata_mn2t6_local_calendar_day_min_v1 | 508 | Same — stale legacy rows, no active Platt or bias model consumption |

All other data_versions (`tigge_mx2t6_local_calendar_day_max_v1`, `tigge_mn2t6_*_v1`, `tigge_mn2t6_*_contract_window_v2`, `ecmwf_opendata_mx2t3_*`, `ecmwf_opendata_mn2t3_*`) are referenced in `src/types/metric_identity.py`, `src/calibration/manager.py`, and/or `src/contracts/snapshot_ingest_contract.py`.

---

## 5. Leads: Calibration vs Platt vs Trading

### calibration_pairs_v2 lead distribution

| lead_days | rows (approx) |
|---|---|
| 0.0 | 6,500,572 |
| 1.0 | 6,552,258 |
| 2.0 | 6,328,472 |
| 3.0 | 6,119,492 |
| 4.0 | 5,906,680 |
| 5.0 | 5,730,592 |
| 6.0 | 5,568,244 |
| 7.0 | 5,427,150 |

(Fractional lead values ~0.7–1.5 are sub-day granularity rows from real-time ingest; the integer buckets are the vast majority.)

All 8 integer leads (0–7) are present in calibration_pairs_v2 for both metrics.

### Platt models: horizon_profile
All 932 Platt models have `horizon_profile = 'full'`. There is no lead-stratified Platt fitting — a single model per (city, season, cycle, source_id) covers all leads 0–7. This means **lead-specific calibration accuracy is sacrificed**: a single sigmoid maps p_raw→p_cal regardless of whether lead=0 or lead=7.

### HIGH metric: calibration coverage gap
`calibration_pairs_v2` has 38.8M HIGH rows (`tigge_mx2t6`) but platt_models_v2 has only **137 HIGH VERIFIED models** (vs 746 LOW VERIFIED models). HIGH Platt coverage is ~5.5× thinner than LOW per city. The `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` data_version has 592,950 calibration pairs with **zero corresponding Platt models** in VERIFIED state.

### Leads calibrated but no active trading evidence
`opportunity_fact` has 0 rows — the table is empty. There is therefore no DB-level evidence of which leads are actually traded. The calibration pipeline covers leads 0–7 uniformly but whether trades at leads 5–7 are being attempted is not auditable from this DB snapshot.

---

## 6. Empty / Zero-Populated Tables (schema exists, no data)

| Table | Rows | Notes |
|---|---|---|
| forecast_error_profile | 0 | Schema ready; writer never ran |
| day0_residual_fact | 0 | Schema ready; writer never ran |
| model_skill | 0 | Schema ready; writer never ran |
| asos_wu_offsets | 0 | Schema ready; no station offsets loaded |
| replay_results | 0 | Schema ready; no replays persisted |
| regime_correlation_cache | 0 | Phase 5 T2 table; never populated |
| tail_stress_scenarios | 0 | Phase 3 T2 table; never populated |
| opportunity_fact | 0 | Schema ready; writer never ran |

`hourly_observations` has 1,826,278 rows across 51 cities — this IS populated and used by `src/signal/diurnal.py` and `observation_client.py`. `diurnal_curves` has 4,823 rows (51 cities) and IS actively consumed.

---

## Summary

**Biggest unused-data buckets (ranked by impact)**

1. **23,918 open_ens JSON files (55 cities, leads 0–10d)** — fully downloaded to disk, zero DB rows. Both HIGH and LOW. If the open_ens ingest backend were wired to the DB writer, this doubles real-time forecast depth at leads 8–10 where TIGGE archive has nothing.

2. **4.7M calibration pairs at cycle=12 (HIGH: 3.97M, LOW: 0.74M)** — 267 Platt models exist for cycle=12 (all VERIFIED/active), but the calibration training pipeline is 00z-dominant (9:1 ratio). The 12z pairs represent a genuine second independent forecast run that is under-leveraged relative to its volume.

3. **Zero lead-stratified Platt models** — all 932 Platt models pool leads 0–7 into a single sigmoid. The 48M+ calibration pairs span 8 distinct lead distances; a lead-stratified fit would sharpen calibration at short (day0, day1) and long (day6, day7) leads where p_raw reliability differs materially.

4. **8 empty analytic tables**: `forecast_error_profile`, `day0_residual_fact`, `model_skill`, `asos_wu_offsets`, `replay_results`, `regime_correlation_cache`, `tail_stress_scenarios`, `opportunity_fact` — all have DDL and code writers that were designed but never executed.

5. **Auckland: 22k snapshots + Platt models, zero settlements** — fully calibrated city that cannot trade because no settlement authority is configured.
