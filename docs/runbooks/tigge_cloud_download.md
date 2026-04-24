# TIGGE Cloud Download Runbook

Purpose: durable operator guidance for TIGGE cloud download supervision and
Zeus v2 data handoff. This runbook is intentionally generic. Environment
snapshots, project identifiers, VM names, account lane names, IP addresses,
local absolute paths, and dated progress diaries belong in evidence artifacts,
not in this durable procedure.

Authority status: operator guidance only. Data/version law lives in
`docs/authority/zeus_current_architecture.md`, machine manifests, tests, and
the executable data pipeline.

## Operating Model

TIGGE cloud download is the preferred path for large historical raw GRIB
collection when local download cost or reliability is unacceptable.

The operator goal is to keep the cloud run observable and recoverable while
preserving Zeus data semantics:

- high track uses local-calendar-day maximum geometry
- low track uses local-calendar-day minimum geometry
- high and low outputs must remain metric-scoped
- raw downloads are evidence inputs, not live trading authority
- trained v2 calibration outputs are offline assets until live consumption gates
  explicitly support them

## Health Check Shape

Use the cloud provider's normal SSH or remote-command surface to inspect the
runner. Prefer scripted health reports over manual one-off command output.

Check:

1. data disk is mounted
2. expected download sessions or worker processes exist
3. recent success markers are advancing
4. raw GRIB files are still changing when marker counts look flat
5. retry counts and transfer errors are not growing without recovery
6. free disk space is above the configured hard floor

Do not restart a lane only because a progress counter appears flat. Large GRIB
transfers can hold marker counts steady while bytes are still arriving.

## Recovery Rules

Restart only when there is evidence of a dead process, stale status file,
missing mounted disk, exhausted disk, high retry churn, or no file mtime
movement beyond the watchdog threshold.

Recovery scripts should:

- recreate missing sessions without increasing worker counts blindly
- keep date ranges stable during a run
- avoid dynamic target dates when shard boundaries depend on fixed ranges
- skip existing completed files rather than overwrite them
- write a new evidence note when rebalancing lanes or changing ranges

## Zeus Handoff

After raw download completion:

1. extract local-calendar-day JSON by metric
2. ingest into metric-aware snapshot tables
3. rebuild calibration pairs by metric
4. refit metric-aware Platt models
5. sync compact reports and trained artifacts back to the local workspace

Do not copy live SQLite WAL databases directly. Use a consistent backup method
or a separate training DB copy.

## Live Use Guard

Do not assume cloud-trained v2 calibration is live-ready. Before live use,
verify:

- evaluator lookup is metric-aware
- high and low never share a calibration family
- `data_version` and `temperature_metric` are carried through lookup paths
- low Day0 slots refuse unsafe causality states
- tests prove metric separation across calibration and replay paths

Until those gates pass, cloud-trained outputs remain training evidence and
offline assets.

## Evidence Routing

Put dated run state, VM details, session names, provider project identifiers,
rebalance tables, and local absolute paths in an artifact such as
`docs/artifacts/tigge_cloud_wiring_snapshot_2026-04-19.md`.

Never write API keys, account secrets, token contents, or credential file
contents into docs, reports, logs, or handoffs.
