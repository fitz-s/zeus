# Live Restart — 2026-05-07

Branch: `live-alignment-2026-05-07`
PR: pending (see PR number after merge)

## Context

Daemon PID 33671 (`src.ingest_main`, started ~16:00 2026-05-07) is running pre-merge
code and emitting legacy `ecmwf_opendata_mx2t6/mn2t6` rows post-deprecation. After
merging `live-alignment-2026-05-07` onto main, restart with the commands below to
load the updated code (D1 legacy bridge + any other merged fixes).

## Restart commands

```bash
launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist
launchctl load  ~/Library/LaunchAgents/com.zeus.data-ingest.plist
```

## Verification

**1. Confirm fresh PID and start time:**

```bash
ps -ef | grep ingest_main | grep -v grep
```

Expected: a new PID (different from 33671) with a start time after the restart.

**2. Confirm first new `mx2t3` row appears in `ensemble_snapshots_v2`:**

```bash
sqlite3 ~/Library/LaunchAgents/../../../.openclaw/workspace-venus/zeus/state/zeus-world.db \
  "SELECT data_version, issue_time, created_at FROM ensemble_snapshots_v2 WHERE data_version LIKE '%mx2t3%' ORDER BY created_at DESC LIMIT 1;"
```

Or from the zeus working directory:

```bash
sqlite3 state/zeus-world.db \
  "SELECT data_version, issue_time, created_at FROM ensemble_snapshots_v2 WHERE data_version LIKE '%mx2t3%' ORDER BY created_at DESC LIMIT 1;"
```

Expected: `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` with `created_at` after the restart timestamp.

## Notes

- The `ThrottleInterval` in the plist is 30 s, so the daemon will restart within 30 s
  if it crashes; the `unload`+`load` pair ensures it picks up the post-merge venv.
- Do NOT restart `com.zeus.live-trading` or `com.zeus.riskguard-live` unless instructed
  separately — only the ingest daemon needs to reload for this change.
- Legacy `mx2t6/mn2t6` rows already in `ensemble_snapshots_v2` are preserved (D1 bridge
  ensures they still route to a Platt calibration target).
