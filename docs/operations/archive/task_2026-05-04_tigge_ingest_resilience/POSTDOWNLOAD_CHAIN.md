# Post-download chain: TIGGE 12z → JSON → ensemble_snapshots_v2

**Created:** 2026-05-04
**Last reused or audited:** 2026-05-04
**Authority basis:** PR #55 review-round-1 follow-up; user directive 2026-05-04
"完成后自动切换全历史下载，确认提取脚本也准备好".

---

## State at 2026-05-04 (PR #55)

### Cloud (tigge-runner @ europe-west4-a)

- 10 download lanes active: 5 ECMWF accounts × 2 metrics (mx2t6/mn2t6) × cycle=12z.
- Date range per account-pair: ~6 months each, covering 2024-01-01 → 2026-05-02 collectively.
- Status JSONs at `/data/tigge/workspace-venus/51 source data/tmp/tigge_*_download_status_a*_cycle12z.json`.
- Output GRIBs at `.../raw/tigge_ecmwf_ens_regions_{mx2t6,mn2t6}/<region>/<YYYYMMDD_YYYYMMDD>_cycle12z/*.grib`.
- Watchdog tmux session `tigge-watchdog` keeps lanes alive across MARS-side outages.

### Cycle-aware pipeline readiness

| Step | Cycle-aware? | Where |
|---|---|---|
| Download | YES | cloud — `tigge_{mx2t6,mn2t6}_download_resumable.py --cycle 12` |
| Status namespacing | YES | cloud — `*_cycle12z.json` distinct from default `*.json` |
| GRIB output dir | YES | cloud — `<date_range>_cycle12z/*.grib` |
| Extract → JSON | YES | local + cloud — `tigge_local_calendar_day_extract.py --cycle 12` (uploaded 2026-05-04) |
| JSON output dir | YES | derives `<issue_date>_cycle12z` namespace |
| Ingest → snapshots_v2 | YES | local — `ingest_grib_to_snapshots.py` reads `issue_time_utc` from JSON; downstream `derive_phase2_keys_from_ens_result` extracts cycle from `datetime.hour` |
| Evaluator bucket selection | YES | `get_calibrator(... cycle=, source_id=, horizon_profile=)` |

## Auto-chain orchestrator

`scripts/cloud_tigge_autochain.sh` runs ON the GCE instance (deploy via
tmux session `tigge-autochain`).  Polls the 10 status JSONs every 10 min
(POLL_SECONDS=600); when ALL report `status: "completed"`, kicks off
Phase B with the same lane structure but a different date range (default
2023-01-01 → 2023-12-31, override via `PHASE_B_DATE_FROM` /
`PHASE_B_DATE_TO`).

The script does NOT touch in-flight lanes — it only starts NEW ones once
existing lanes are confirmed done.  Safe to start at any time during
Phase A.

To deploy:

```bash
gcloud compute scp scripts/cloud_tigge_autochain.sh tigge-runner:'/data/tigge/workspace-venus/51 source data/scripts/' \
  --zone=europe-west4-a --project=snappy-frame-468105-h0
gcloud compute ssh tigge-runner --zone=europe-west4-a --project=snappy-frame-468105-h0 \
  --command="tmux new-session -d -s tigge-autochain 'bash \"/data/tigge/workspace-venus/51 source data/scripts/cloud_tigge_autochain.sh\"'"
```

## Local post-download flow (after rsync)

The cloud holds GRIBs only.  Extraction → ingest still runs on the local
zeus environment (the cloud lacks the zeus repo + DB).  Sequence:

1. **Rsync** GRIBs from cloud to local `51 source data/raw/`:
   ```bash
   gcloud compute scp --recurse \
     tigge-runner:'/data/tigge/workspace-venus/51 source data/raw/tigge_ecmwf_ens_regions_mx2t6/*' \
     '/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens_regions_mx2t6/' \
     --zone=europe-west4-a --project=snappy-frame-468105-h0
   # (and the same for mn2t6)
   ```

2. **Extract 12z** to JSONs:
   ```bash
   cd '/Users/leofitz/.openclaw/workspace-venus/51 source data'
   python scripts/extract_tigge_mx2t6_localday_max.py \
       --cycle 12 \
       --date-from 2024-01-01 --date-to 2026-05-02 \
       --raw-root './raw' --output-root './raw'
   python scripts/extract_tigge_mn2t6_localday_min.py \
       --cycle 12 \
       --date-from 2024-01-01 --date-to 2026-05-02 \
       --raw-root './raw' --output-root './raw'
   ```

3. **Ingest** JSONs into `ensemble_snapshots_v2`:
   ```bash
   cd '/Users/leofitz/.openclaw/workspace-venus/zeus'
   source .venv/bin/activate
   python -m scripts.ingest_grib_to_snapshots \
       --track mx2t6_high \
       --json-root '../51 source data/raw' \
       --date-from 2024-01-01 --date-to 2026-05-02
   python -m scripts.ingest_grib_to_snapshots \
       --track mn2t6_low \
       --json-root '../51 source data/raw' \
       --date-from 2024-01-01 --date-to 2026-05-02
   ```

4. **Verify** dual-cycle coverage:
   ```sql
   SELECT cycle, COUNT(*) FROM ensemble_snapshots_v2
   WHERE data_version LIKE 'tigge_%' GROUP BY cycle;
   ```
   Both `00` and `12` should be non-zero.

5. **Refit** Platt v2 with cycle stratification:
   ```bash
   python scripts/refit_platt_v2.py
   ```
   New rows land in `platt_models_v2` keyed on (cycle, source_id, horizon_profile).

## Forward-rolling daily updates

Once the historical backfill is complete, the daily catch-up loop in
`src/ingest_main.py` keeps both 00z and 12z current — each cycle has its
own track-isolated path (see Phase 1 deliverables in
`UNLOCK_SEQUENCE.md`).
