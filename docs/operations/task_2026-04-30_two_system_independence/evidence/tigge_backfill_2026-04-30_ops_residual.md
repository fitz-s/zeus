# TIGGE Backfill 2026-04-30 — Evidence File

## Task
Backfill TIGGE ensemble data for the 12-day gap: issue dates 2026-04-19 → 2026-04-30

## Pre-Backfill Baseline
- **Total rows in ensemble_snapshots_v2**: 684,624
- **Max issue_time**: `2026-04-18T00:00:00+00:00`
- **Max target_date**: `2026-04-25`
- **high rows**: 342,312 (target_dates 2024-01-01 → 2026-04-25)
- **low rows**: 342,312 (target_dates 2024-01-01 → 2026-04-25)

## ECMWF Credentials
- File: `~/.ecmwfapirc` — PRESENT
- Key: `0889f7e1891d8ae52a845058aed1d521`
- Email: `yewuyusile@gmail.com`

## Approach
1. Download GRIBs via `tigge_mx2t6_download_resumable.py` (param 121.128) → `tigge_ecmwf_ens_regions_mx2t6/`
2. Download GRIBs via `tigge_mn2t6_download_resumable.py` (param 122.128) → `tigge_ecmwf_ens_regions_mn2t6/`
3. Extract JSONs via `tigge_local_calendar_day_extract.py` for both tracks
4. Ingest JSONs via `zeus/scripts/ingest_grib_to_snapshots.py` for both tracks

## Status: IN PROGRESS

## Download Log
(populated during execution)

## Post-Backfill Results
(populated after completion)
