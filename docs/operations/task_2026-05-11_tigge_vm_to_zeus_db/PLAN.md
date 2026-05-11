# Plan: TIGGE VM Outputs To Local 51 Data, Zeus DB, And Training
> Created: 2026-05-11 | Status: IN PROGRESS

## Goal
Move the verified TIGGE localday outputs from `tigge-runner` into local `workspace-venus/51 source data/raw`, import them into Zeus world DB, and run the calibration/training chain only after relationship checks prove the data semantics survived every boundary.

## Context
- User scope is fixed: `2024-01-01..2026-05-02`; do not expand to later dates.
- VM root: `/data/tigge/workspace-venus/51 source data`.
- Local source root: `/Users/leofitz/.openclaw/workspace-venus/51 source data`.
- Zeus repo: `/Users/leofitz/.openclaw/workspace-venus/zeus`.
- Zeus-consumed product is localday JSON under `raw/tigge_ecmwf_ens_mn2t6_localday_min` and `raw/tigge_ecmwf_ens_mx2t6_localday_max`, not the 1.6T raw GRIB cache.
- Existing `scripts/local_post_extract_chain.sh` is `STALE_REWRITE` for this task: it waits old session names, copies into `zeus/raw` instead of local `51 source data/raw`, and its stage7 command no longer matches current 5/8 calibration write gates. It may be used as historical reference only.
- Current topology route is advisory/read-only; rerun topology navigation with changed files before any code edits.

## Approach
Work boundary by boundary. First prove VM output completeness; then transfer only the canonical localday output plus evidence; then prove local byte/file/semantic parity; then import into a staging DB before touching production; then run current v2 calibration rebuild/refit in isolated DB mode and only promote after gates pass. No VM stop/delete occurs until local verification succeeds and operator confirms.

## Relationship Invariants
- VM -> local: file count, archive checksum, and sampled JSON sha256 match.
- Raw GRIB -> localday JSON: after every required `.grib.ok` exists, the final localday extract for touched ranges must be fresh or `--overwrite`; stale JSON skipped before raw completion is invalid evidence.
- JSON -> ingest contract: every accepted file has 51 members, explicit native `unit` (`C`/`F`) that ingestor derives into `members_unit`, explicit `causality`, valid data_version/physical_quantity/temperature_metric pairing.
- Import -> DB: `ensemble_snapshots_v2` rows preserve unique key `(city, target_date, temperature_metric, issue_time, data_version)`, no duplicates, no cross-metric contamination.
- DB -> training: only `authority='VERIFIED'`, `training_allowed=1`, `causality_status='OK'` snapshots feed `calibration_pairs_v2`.
- Training -> serving: `platt_models_v2` active rows are metric/cycle/source/horizon scoped; no OpenData/TIGGE domain collapse.

## Tasks

- [ ] 1. Final VM completion audit
  - Files: remote `tmp/tigge_target_*.json`, local audit output under `tmp/`.
  - What: prove all targeted gap downloads/extracts are complete and extract summaries have `metadata_error_count=0`.

- [ ] 2. Transfer canonical localday package
  - Files: local `../51 source data/raw/tigge_ecmwf_ens_mn2t6_localday_min`, `../51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max`.
  - What: package on VM, transfer to local `51 source data`, verify archive checksum and file-count parity. Do not transfer raw GRIB unless explicitly requested.

- [ ] 3. Local semantic validation
  - Files: local `../51 source data/raw/tigge_ecmwf_ens_*_localday_*`.
  - What: run JSON contract probes for member count, native unit, causality, target-date distribution, and data_version/metric matching before DB import.
  - Anti-staleness gate: for all touched VM ranges, final extract summaries must use expected VM `raw_root`/`output_root`, have `pair_count > 0`, `metadata_error_count=0`, and either `skipped_outputs=0` or an explicit sha256 proof that every skipped JSON already matches the fresh output. Preferred path is final `--overwrite` extract after raw completion.

- [ ] 4. Staging DB import trial
  - Files: `scripts/ingest_grib_to_snapshots.py`, staging copy under `state/` or `/tmp`.
  - What: copy `state/zeus-world.db` to isolated staging DB, run both tracks with `--json-root '../51 source data/raw' --date-from 2024-01-01 --date-to 2026-05-02 --ingest-backend ecds`, then run DB invariants.
  - Commands:
    ```bash
    cd /Users/leofitz/.openclaw/workspace-venus/zeus
    STAGE_DB="state/tigge_stage_$(date -u +%Y%m%dT%H%M%SZ).db"
    sqlite3 state/zeus-world.db ".backup '$STAGE_DB'"
    sqlite3 "$STAGE_DB" 'PRAGMA integrity_check;'
    /usr/local/bin/python3 scripts/ingest_grib_to_snapshots.py \
      --db-path "$STAGE_DB" \
      --track mn2t6_low \
      --json-root "/Users/leofitz/.openclaw/workspace-venus/51 source data/raw" \
      --date-from 2024-01-01 --date-to 2026-05-02 \
      --ingest-backend ecds
    /usr/local/bin/python3 scripts/ingest_grib_to_snapshots.py \
      --db-path "$STAGE_DB" \
      --track mx2t6_high \
      --json-root "/Users/leofitz/.openclaw/workspace-venus/51 source data/raw" \
      --date-from 2024-01-01 --date-to 2026-05-02 \
      --ingest-backend ecds
    /usr/local/bin/python3 scripts/verify_truth_surfaces.py \
      --mode training-readiness --world-db "$STAGE_DB" --json
    ```

- [ ] 5. Production DB import with backup
  - Files: `state/zeus-world.db`.
  - What: after staging passes, snapshot production DB, run idempotent import for both tracks, then prove row deltas and invariants. No overwrite unless a failed staging import proves it is necessary.
  - Backup command:
    ```bash
    PROD_BACKUP="state/zeus-world.pre_tigge_import_$(date -u +%Y%m%dT%H%M%SZ).db"
    sqlite3 state/zeus-world.db ".backup '$PROD_BACKUP'"
    sqlite3 "$PROD_BACKUP" 'PRAGMA integrity_check;'
    ```

- [ ] 6. Calibration/training staging
  - Files: `scripts/rebuild_calibration_pairs_v2.py`, `scripts/refit_platt_v2.py`.
  - What: run dry-run first, then isolated DB write mode with `--db <staging-db> --no-dry-run --force --temperature-metric all`, respecting the live rebuild sentinel and preflight gates.
  - Commands:
    ```bash
    /usr/local/bin/python3 scripts/verify_truth_surfaces.py \
      --mode calibration-pair-rebuild-preflight --world-db "$STAGE_DB" --json
    /usr/local/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
      --db "$STAGE_DB" --temperature-metric high \
      --data-version tigge_mx2t6_local_calendar_day_max_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02
    /usr/local/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
      --db "$STAGE_DB" --temperature-metric low \
      --data-version tigge_mn2t6_local_calendar_day_min_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02
    /usr/local/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
      --db "$STAGE_DB" --temperature-metric high \
      --data-version tigge_mx2t6_local_calendar_day_max_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02 \
      --no-dry-run --force
    /usr/local/bin/python3 scripts/rebuild_calibration_pairs_v2.py \
      --db "$STAGE_DB" --temperature-metric low \
      --data-version tigge_mn2t6_local_calendar_day_min_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02 \
      --no-dry-run --force
    /usr/local/bin/python3 scripts/verify_truth_surfaces.py \
      --mode platt-refit-preflight --world-db "$STAGE_DB" --json
    /usr/local/bin/python3 scripts/refit_platt_v2.py \
      --db "$STAGE_DB" --temperature-metric high \
      --data-version tigge_mx2t6_local_calendar_day_max_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02
    /usr/local/bin/python3 scripts/refit_platt_v2.py \
      --db "$STAGE_DB" --temperature-metric low \
      --data-version tigge_mn2t6_local_calendar_day_min_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02
    /usr/local/bin/python3 scripts/refit_platt_v2.py \
      --db "$STAGE_DB" --temperature-metric high \
      --data-version tigge_mx2t6_local_calendar_day_max_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02 \
      --no-dry-run --force --strict
    /usr/local/bin/python3 scripts/refit_platt_v2.py \
      --db "$STAGE_DB" --temperature-metric low \
      --data-version tigge_mn2t6_local_calendar_day_min_v1 \
      --start-date 2024-01-01 --end-date 2026-05-02 \
      --no-dry-run --force --strict
    ```
  - Manual archive import caveat: `ingest_grib_to_snapshots.py` CLI does not create `SourceRunContext`, so source-run fields remain null and `available_at` falls back to issue time. This run is archive-training evidence only unless a reviewed source-run context path is added before promotion.

- [ ] 7. Promotion-ready verification
  - Files: `scripts/verify_truth_surfaces.py`, `src/observability/calibration_serving_status.py`, `state/entry_forecast_promotion_evidence.json` if promotion is authorized later.
  - What: verify `calibration_pairs_v2`, `platt_models_v2`, serving status, producer readiness, and replay audit. Do not arm or promote live trading without separate operator approval.
  - Current finding: no authoritative bulk staging-DB-to-world-DB promotion script was found. Production enablement must use the operator-gated retrain/promotion path or a separately reviewed promotion packet; copying rows manually is out of scope for this run.

- [ ] 8. VM shutdown handoff
  - Files: `ops/tigge_gcp_migration_shutdown_plan_2026-05-11.md`.
  - What: once local import/training evidence is complete, present the exact stop/delete checklist for confirmation.

## Immediate Status
- Documents created:
  - `/Users/leofitz/.openclaw/ops/tigge_gcp_migration_shutdown_plan_2026-05-11.md`
  - `/Users/leofitz/.openclaw/ops/google_cloud_billing_refund_appeal_2026-05-11.md`
- Live VM check at 2026-05-11T08:40:49Z:
  - `mn12 2024-08-22..24`: 9/12 complete.
  - `mn12 2024-09-01`: complete.
  - `mx00 2026-04-19..20`: complete.
  - `mx00 2026-04-21..22`: 6/8 complete.
  - `mx00 2026-04-23..24`: 5/8 complete on active a2 shard; stale a5 status exists but no tmux session.
  - Later mx00 shards still running or queued.
  - Extract summaries inspected had `metadata_error_count=0`.
- 2026-05-11T08:55Z:
  - Started corrected explicit-root extractions:
    - `tigge-gap-extract-mn12-20240819-20240901-v2`
    - `tigge-gap-extract-mx00-20260419-20260424-v2`
  - Earlier same-name attempts without explicit `--raw-root/--output-root` produced `pair_count=0` and wrote nothing; ignore those logs.
  - Killed duplicate `tigge-early-extract-mn2t6-a8-c12`, which overlapped exactly with `tigge-gap-extract-mn12-20251225-20260131`.
  - Remaining mx00 download shards `20260425_26`, `20260427_28`, `20260429_30`, `20260501`, `20260502` were still accepted/running with zero completed tasks at 08:59Z.
- 2026-05-11T09:13Z:
  - Verified `/dev/shm/tigge-ecds-runtime-20260509/account6..10.cdsapirc` are distinct hashes from each other and from disk `account1..5`.
  - Existing a6-a10 sessions were each blocked on the first `americas` task for their assigned date, leaving 27 non-active region/date tasks not yet submitted to idle accounts.
  - Started supplemental workers on disk ECDS accounts 1-5:
    - `tigge-supp-mx00-a1-20260425-0502`
    - `tigge-supp-mx00-a2-20260425-0502`
    - `tigge-supp-mx00-a3-20260425-0502`
    - `tigge-supp-mx00-a4-20260425-0502`
    - `tigge-supp-mx00-a5-20260425-0502`
  - Supplemental task file: `/data/tigge/workspace-venus/51 source data/tmp/tigge_mx00_supplemental_region_date_tasks_20260511T0913Z.tsv`.
  - Initial supplemental workers wrote to canonical raw paths; critic flagged `.ok` is not a lock. They were stopped before completion and relaunched as isolated workers targeting `tigge_ecmwf_ens_regions_mx2t6_supplemental`.
  - Isolated supplemental workers deliberately exclude the five currently active a6-a10 `americas` tasks. They must not be merged into canonical raw until a manifest-level de-duplication pass proves the canonical key is missing and the isolated `.ok` pair is valid.
- 2026-05-11T09:30Z:
  - Started final `--overwrite` canonical extracts for low-track ranges whose earlier summaries had skipped outputs:
    - `tigge-final-overwrite-extract-mn12-20240819-20240901`
    - `tigge-final-overwrite-extract-mn12-20251116-20251224`
  - Stopped duplicate non-overwrite canonical writer `tigge-early-extract-mn2t6-a8-c12` after critic recheck found the rolling watcher restarted it.
  - Stopped `tigge-rolling-early-extract-watch` to prevent further non-overwrite canonical extractor respawns during final stabilization.
- 2026-05-11T09:44Z:
  - Stopped isolated `a5` supplemental lane after repeated ECDS rejected statuses for `asia 2026-04-26`; relaunched that lane on account1 as `tigge-supp-mx00-isolated-a1-retry-lane5`.
  - Launched isolated Americas retries for the five long-accepted canonical original tasks:
    - `tigge-supp-mx00-isolated-americas-a2`
    - `tigge-supp-mx00-isolated-americas-a3`
    - `tigge-supp-mx00-isolated-americas-a4`
  - Rationale: original a6-a10 canonical sessions remained accepted on those Americas tasks for over an hour. Isolated retries do not write canonical raw paths; final merge/audit decides canonical materialization.

## Risks / Open Questions
- The current local source root already contains older TIGGE localday directories. Transfer must be merge-safe and should preserve/compare manifests before replacing anything.
- `ingest_grib_to_snapshots.py` manual CLI does not populate a `SourceRunContext`; downstream checks must confirm whether source_run/readiness tables are separately required for this archive import.
- Current v2 rebuild/refit scripts refuse direct shared world DB writes; production promotion path needs explicit evidence, not a blind `--no-dry-run` on canonical DB.
- Calibration quality and promotion are not identical. Training can be completed without authorizing live trading.
