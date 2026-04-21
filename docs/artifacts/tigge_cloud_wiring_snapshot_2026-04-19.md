# TIGGE Cloud Download And Zeus Wiring

Status: active operational context
Created: 2026-04-19
Scope: TIGGE cloud download, local-download retirement, cloud training handoff, Zeus v2 data wiring

This runbook captures the current TIGGE replacement-download state after moving
from local macOS downloads to Google Compute Engine. It is operational guidance
and evidence context, not architecture authority. The dual-track architecture
law remains `docs/authority/zeus_dual_track_architecture.md`.

## Current Decision

Local TIGGE downloading is retired.

The local macOS download sessions were stopped on 2026-04-19. Active local tmux
sessions and local Python download processes were verified absent after killing:

- `tigge-mx2t6-a1`
- `tigge-mx2t6-a2`
- `tigge-mn2t6-a1`
- `tigge-mn2t6-a2`
- `tigge-progress`
- `tigge-watchdog`

The local launchd jobs that were automatically resurrecting those sessions were
also disabled and moved out of `~/Library/LaunchAgents` into:

```text
/Users/leofitz/Library/LaunchAgents/disabled-openclaw-tigge-20260419T182855Z
```

Disabled labels:

- `com.openclaw.tigge.mx2t6.resumer`
- `com.openclaw.tigge.mn2t6.resumer`
- `com.openclaw.tigge.fullhistory.lane0`
- `com.openclaw.tigge.fullhistory.lane1`

Do not restart local TIGGE downloading unless the cloud VM is explicitly
abandoned. Local partial raw data may remain as a fallback evidence cache, but it
is not the forward source of truth and should not be used for the next training
run unless cloud data becomes unrecoverable.

## Cloud Runtime

Google Cloud is now the active TIGGE download environment.

Canonical VM:

- Project: `snappy-frame-468105-h0`
- Zone: `europe-west4-a`
- Instance: `tigge-runner`
- Machine: `e2-standard-4`
- External IP last observed: `34.34.71.125`
- Data disk: `tigge-data-disk`, mounted at `/data`
- Cloud root: `/data/tigge`
- Download workspace: `/data/tigge/workspace-venus/51 source data`
- Helper bundle: `~/tigge_bundle/tigge_gce_trial.sh`

The data disk is registered in `/etc/fstab`:

```text
/dev/disk/by-id/google-tigge-data /data ext4 defaults,nofail 0 2
```

That mount entry is required. Without it, a VM reboot leaves `/data` unmounted
and the downloader paths fail even though the disk still exists.

## Active Download Shape

The current cloud run downloads two independent ECMWF TIGGE physical quantities:

- High track: `param=121.128`, `shortName=mx2t6`, six-hour max at 2m
- Low track: `param=122.128`, `shortName=mn2t6`, six-hour min at 2m

Both tracks use local-calendar-day geometry. They are not peak-window products.
The intended canonical data versions are:

- `tigge_mx2t6_local_calendar_day_max_v1`
- `tigge_mn2t6_local_calendar_day_min_v1`

Cloud session shape:

- `tigge-mx2t6-a1` through `tigge-mx2t6-a5`
- `tigge-mn2t6-a1` through `tigge-mn2t6-a5`
- `tigge-progress`
- `tigge-watchdog`
- 5 ECMWF accounts
- `MAX_WORKERS=2` per account lane
- Current expected raw completion count: `4464` `.grib.ok` marker files

The progress counter intentionally counts `.grib.ok` files only. A large GRIB
may be actively transferring for many minutes without increasing the progress
number. When diagnosing a stall, check both `.ok` marker time and raw `.grib`
file mtimes/sizes before restarting anything.

## Health Commands

Use this local command to inspect the VM:

```bash
gcloud compute ssh tigge-runner \
  --project snappy-frame-468105-h0 \
  --zone=europe-west4-a \
  --command='cd ~/tigge_bundle && ./tigge_gce_trial.sh remote-health'
```

Use this to inspect the VM-side self-monitor report:

```bash
gcloud compute ssh tigge-runner \
  --project snappy-frame-468105-h0 \
  --zone=europe-west4-a \
  --command='tail -160 /data/tigge/logs/self_monitor/tigge_self_monitor_latest.log'
```

Use this to inspect recent `.ok` markers directly:

```bash
gcloud compute ssh tigge-runner \
  --project snappy-frame-468105-h0 \
  --zone=europe-west4-a \
  --command='cd "/data/tigge/workspace-venus/51 source data" && find raw/tigge_ecmwf_ens_regions_mx2t6 raw/tigge_ecmwf_ens_regions_mn2t6 -type f -name "*.grib.ok" -printf "%TY-%Tm-%Td %TH:%TM:%TS %p\n" | sort | tail -20'
```

## VM-Side Recovery

Local Codex cron automation was paused because the automation sandbox could not
reliably reach Google OAuth, Compute APIs, or SSH. That failure mode does not
imply the VM or downloader is down.

VM-side monitoring is installed instead:

- Script: `/data/tigge/bin/tigge_cloud_self_monitor.sh`
- Report directory: `/data/tigge/logs/self_monitor/`
- Latest report symlink: `/data/tigge/logs/self_monitor/tigge_self_monitor_latest.log`
- Local source copy: `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/tigge_cloud_self_monitor.sh`

Current crontab on the VM:

```text
*/30 * * * * DATE_FROM=2024-01-01 DATE_TO=2026-04-18 ACCOUNT_LIMIT=5 MAX_WORKERS=2 /data/tigge/bin/tigge_cloud_self_monitor.sh >/data/tigge/logs/self_monitor/cron_last.log 2>&1
@reboot sleep 90; DATE_FROM=2024-01-01 DATE_TO=2026-04-18 ACCOUNT_LIMIT=5 MAX_WORKERS=2 /data/tigge/bin/tigge_cloud_self_monitor.sh >/data/tigge/logs/self_monitor/reboot_last.log 2>&1
```

The self-monitor checks:

- `/data` is mounted
- available disk is above the hard floor
- all TIGGE tmux sessions exist
- current progress, recent rates, retry counts, and last done events

If a tmux session is missing, it runs the cloud helper's `remote-start` path to
restore missing sessions. It does not blindly increase worker counts.

`DATE_TO` is intentionally frozen at `2026-04-18`. Do not allow the VM-side
self-monitor to use a dynamic `today - 2 days` target date during this run. A
dynamic target date shifts the five-account shard boundaries after a restart and
causes duplicate three-day batch windows to be downloaded under different
directory names.

## Last Observed Evidence

As of the 2026-04-19 manual check, local downloading was stopped and cloud
downloading was still moving. The cloud count had advanced to:

```text
mx2t6 ok markers: 643
mn2t6 ok markers: 627
total: 1270 / 4464
```

Recent progress can look frozen during large perturbed-member GRIB transfers,
but this is not sufficient evidence of a stall unless `.grib` mtimes and raw
file sizes also stop moving.

## 2026-04-21 Rebalance

On 2026-04-21, the original five-account split had become imbalanced. The
following lanes had completed:

- `mx2t6`: `a1`, `a2`, `a5`
- `mn2t6`: `a1`, `a2`, `a5`

The remaining bottleneck was the middle shard:

- `mx2t6 a3`: `2024-12-02 .. 2025-05-18`
- `mn2t6 a3`: `2024-12-02 .. 2025-05-18`

Idle accounts were reassigned to later 3-day-boundary-aligned subranges, while
the original `a3` sessions continued on the earlier subrange. The helper
sessions are:

| Account | Sessions | Date range |
|---------|----------|------------|
| `account1` | `tigge-mx2t6-a1r`, `tigge-mn2t6-a1r` | `2025-03-17 .. 2025-04-03` |
| `account2` | `tigge-mx2t6-a2r`, `tigge-mn2t6-a2r` | `2025-04-04 .. 2025-04-21` |
| `account5` | `tigge-mx2t6-a5r`, `tigge-mn2t6-a5r` | `2025-04-22 .. 2025-05-09` |
| `account4` | `tigge-mx2t6-a4r`, `tigge-mn2t6-a4r` | `2025-05-10 .. 2025-05-18` |

Each helper uses `MAX_WORKERS=1` and its own status file:

```text
tmp/tigge_mx2t6_rebalance_a{1,2,4,5}.json
tmp/tigge_mn2t6_rebalance_a{1,2,4,5}.json
```

The rebalanced helpers intentionally use the existing 3-day batch boundaries so
they do not introduce new shifted-window duplicates. They may overlap logically
with the original broad `a3` date range, but the downloader uses `overwrite=0`
and skips files with existing `.grib.ok` markers when a queued task reaches an
already-completed target.

## Stall Triage

Do not restart a lane merely because the one-line progress bar has not changed.
First inspect:

1. `last_done` in `remote-health`
2. recent `.grib.ok` mtimes
3. raw `.grib` mtimes and file sizes for active transfers
4. per-lane `tmp/tigge_*_download_status_a*.json`
5. retry / `ConnectionResetError` / `Transfer interrupted` counts

Expected behavior during large perturbed-file transfers:

- `.grib` file may be hundreds of MB
- `.ok` marker is written only after the request completes
- the displayed percentage can appear frozen while bytes are still arriving
- several lanes may show `active_stalled=2` before the configured stall
  threshold; this alone is not a failure

Restart a lane only when there is evidence of a dead process, stale status JSON,
high retry churn, disk exhaustion, or no mtime movement beyond the watchdog
threshold.

## Cloud Training Handoff

Cloud training is viable and preferred after raw download completes. The local
machine should not pull hundreds of GB of raw GRIB back just to train.

Recommended cloud training shape:

1. Keep raw GRIB and extracted JSON on `/data/tigge`.
2. Deploy or sync the full `zeus/` repository to the VM before running Zeus
   training scripts.
3. Upload a SQLite-consistent snapshot of `state/zeus-world.db` to the VM.
   Use SQLite backup or `VACUUM INTO`; do not copy a live WAL database directly.
4. Train into a separate DB copy, for example
   `/data/tigge/training/zeus-world-trained.db`.
5. Sync back compact outputs only: coverage reports, validation reports, trained
   v2 Platt rows/artifacts, and final DB snapshots if needed.

The local raw partial files should be kept as a backup until cloud validation
passes, but they are no longer part of the main path.

## Zeus Data Wiring

The intended TIGGE-to-Zeus historical pipeline is:

```text
Cloud raw GRIB
  -> 51 source data local-calendar-day extractor
  -> extracted localday JSON
  -> scripts/ingest_grib_to_snapshots.py
  -> ensemble_snapshots_v2
  -> scripts/rebuild_calibration_pairs_v2.py
  -> calibration_pairs_v2
  -> scripts/refit_platt_v2.py
  -> platt_models_v2
```

Extractor output directories:

- `raw/tigge_ecmwf_ens_mx2t6_localday_max/`
- `raw/tigge_ecmwf_ens_mn2t6_localday_min/`

Raw region directories:

- `raw/tigge_ecmwf_ens_regions_mx2t6/`
- `raw/tigge_ecmwf_ens_regions_mn2t6/`

Zeus ingest script:

- `scripts/ingest_grib_to_snapshots.py --track mx2t6_high`
- `scripts/ingest_grib_to_snapshots.py --track mn2t6_low`

The ingest script writes `ensemble_snapshots_v2` and carries:

- `temperature_metric`
- `physical_quantity`
- `observation_field`
- `data_version`
- `training_allowed`
- `causality_status`
- `boundary_ambiguous`
- `ambiguous_member_count`
- `manifest_hash`
- `provenance_json`
- `members_unit`
- `local_day_start_utc`
- `step_horizon_hours`

Metric identity source of truth:

- `src/types/metric_identity.py`
  - `HIGH_LOCALDAY_MAX`
  - `LOW_LOCALDAY_MIN`

Calibration spec source of truth:

- `src/calibration/metric_specs.py`
  - both high and low specs are iterated

Pair rebuild:

- `scripts/rebuild_calibration_pairs_v2.py`
  - reads `ensemble_snapshots_v2`
  - selects `observations.high_temp` for high
  - selects `observations.low_temp` for low
  - writes `calibration_pairs_v2`

Optional p-raw backfill:

- `scripts/backfill_tigge_snapshot_p_raw_v2.py`
  - writes `ensemble_snapshots_v2.p_raw_json`
  - metric-scoped
  - useful for audit and replay surfaces

Platt refit:

- `scripts/refit_platt_v2.py`
  - reads `calibration_pairs_v2`
  - writes `platt_models_v2`
  - iterates both metric specs

## Live Consumption Gap

Do not assume live Zeus consumes `platt_models_v2` yet.

As of this handoff, the historical data and v2 training path are wired, but the
live evaluator calibration seam still needs a v2 consumption gate. The current
runtime path still calls `get_calibrator(conn, city, target_date)`, and that
legacy manager path reads legacy `platt_models`, not metric-aware
`platt_models_v2`.

Before live use of cloud-trained calibration, implement and verify:

1. Metric-aware calibrator lookup keyed by `temperature_metric`, `data_version`,
   cluster, season, and input space.
2. Runtime evaluator wiring that passes the `MetricIdentity`.
3. A hard refusal for low Day0 slots whose `causality_status` is not `OK`.
4. Tests proving high and low never share a Platt model, bin lookup, or
   calibration family.

Until that gate lands, cloud-trained v2 artifacts are training evidence and
offline assets, not live trading authority.

## Sensitive Data Rule

Do not write ECMWF API keys into docs, reports, logs, or handoffs. Account files
exist on the local machine and VM under secret paths, but this runbook only names
their operational role, not their contents.
