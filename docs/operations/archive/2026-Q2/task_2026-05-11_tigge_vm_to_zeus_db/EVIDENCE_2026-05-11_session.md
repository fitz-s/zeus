# Evidence: TIGGE VM → Local → STAGE_DB ingest session

> Created: 2026-05-11 (UTC ~19:30) | Branch: `fix/harvester-paginator-bound-2026-05-11`

## Summary
Successful end-to-end VM-to-STAGE_DB pipeline. STAGE_DB ingest complete. Calibration rebuild + Platt refit launched in nohup background and is in progress at time of this checkpoint commit. Production `state/zeus-world.db` was NEVER modified during this session.

## Pipeline boundaries crossed
| Boundary | Result | Evidence |
|---|---|---|
| VM coverage audit | 51/51 cities × 853 days × 2 tracks, 0 missing | `tmp/tigge_localday_coverage_audit_20260511T1525Z.json` |
| VM → local transfer (tar.zst + sha256 sidecar) | sha256 parity confirmed both tracks | `51 source data/raw_incoming_tigge_2026-05-11/*.tar.zst{,.sha256}` |
| Local untar | file-count parity 694342/track == VM | `51 source data/raw/tigge_ecmwf_ens_*_localday_*` |
| STAGE_DB created (sqlite .backup of zeus-world.db) | 36G, integrity_check=ok, 90s | `state/tigge_stage_20260511T175548Z.db` |
| Pre-purge `calibration_pairs_v2` in STAGE_DB only | 53,490,902 rows deleted (FK was blocking REPLACE) | (rebuild step repopulates) |
| Ingest `mn2t6_low` | 308666 written / 382820 skipped / 0 errors | `tmp/ingest_mn2t6_low_20260511T182235Z.log` |
| Ingest `mx2t6_high` | 314974 written / 376512 skipped / 0 errors | `tmp/ingest_mx2t6_high_20260511T183616Z.log` |
| Final `ensemble_snapshots_v2` row counts | mn 694006, mx 694707 (matches 51×853×~16 expected) | `sqlite3 -readonly $STAGE_DB` query |
| `verify_truth_surfaces --mode training-readiness` (initial) | NOT_READY, 9 FAIL: 1 NEW (1283 high unsafe) | `tmp/verify_training_readiness_20260511T184133Z.json` |
| Investigation of 1283 unsafe rows | PRE-EXISTING ECMWF OpenData ingest mislabel (`mx2t6` written instead of `mx2t3`) — first appeared 2026-05-05, not from today's TIGGE | (subagent report in transcript) |
| STAGE_DB-only fix: UPDATE training_allowed=0 on 1283 OpenData mislabeled rows | Verified safe by 2nd subagent (FK NO ACTION; calibration empty; existing 0 rows of same data_version exist) | `UPDATE ensemble_snapshots_v2 SET training_allowed=0 WHERE data_version IN ('ecmwf_opendata_mx2t6_local_calendar_day_max_v1','ecmwf_opendata_mn2t6_local_calendar_day_min_v1') AND training_allowed=1` |
| Re-verify preflight | ready=True, 0 blockers | (output in transcript) |
| Task 6 launch (nohup) | preflight + 2 dry-runs PASS; HIGH live rebuild in progress at checkpoint | `tmp/task6_calibration_refit_20260511T192921Z.log` |

## Skip semantics confirmation
55% skip rate is benign: **all** skips are `skipped_exists` (manifest_sha matches DB row → idempotent no-op). Zero parse_error, zero contract_rejected. Final row counts match expected 51×853×~16 ≈ 694k per track.

## VM teardown
- `gcloud compute instances stop tigge-runner --zone=europe-west4-a` → STOPPED
- `gcloud compute instances delete tigge-runner --zone=europe-west4-a` → instance + boot disk auto-deleted
- `gcloud compute disks delete tigge-data-disk --zone=europe-west4-a` → 2TB persistent disk deleted
- Project `snappy-frame-468105-h0` europe-west4-a final state: 0 instances, 0 disks, 0 snapshots, 0 IPs → **$0/mo recurring billing**
- (Unrelated GCS bucket `gs://0f75d593-...shoxnafitz/` 283MB present from a separate AI workspace tool, not TIGGE-related, untouched.)

## Open issue (NOT a today blocker)
ECMWF OpenData ingest pipeline mislabels `data_version` field: writes `ecmwf_opendata_mx2t6_local_calendar_day_max_v1` when the source product is actually `mx2t3`. Production `zeus-world.db` has 1342 rows with this defect, dating from 2026-05-05. The download cron itself uses `--param mx2t3` correctly, so the bug is in the JSON build / data_version assignment downstream of download. Filed for separate fix — see also `docs/operations/task_2026-05-11_ingest_starvation_refactor/PLAN.md`.

## STAGE-only fix caveat
The `UPDATE training_allowed=0 ... opendata_mx2t6/mn2t6` SQL was applied **only** to STAGE_DB. Production `zeus-world.db` still carries the 1342 rows with `training_allowed=1` and would block any production rebuild on the same gate. When promoting STAGE_DB results to production, either (a) apply the same UPDATE on prod, or (b) fix the OpenData ingest mislabel and re-ingest, then the gate will pass naturally.

## Files preserved on disk (full triple-redundancy)
- Live extracts: `51 source data/raw/tigge_ecmwf_ens_mn2t6_localday_min/` (694342 files / 9.9G), `.../tigge_ecmwf_ens_mx2t6_localday_max/` (694342 / 5.3G)
- Pre-untar partial backups: `*.bak.20260511T173723Z` (still present)
- Compressed tarballs with sha256 sidecars: `51 source data/raw_incoming_tigge_2026-05-11/*.tar.zst[+ .sha256]`
- VM source: deleted (no longer needed; everything verified locally first)

## Wired-up status
**STAGE_DB is the artifact of this session.** Production zeus-world.db unchanged. No live trading impact. No serving change. Promotion to production is a separate, operator-gated step and is **out of scope** for this run (PLAN Task 7).
