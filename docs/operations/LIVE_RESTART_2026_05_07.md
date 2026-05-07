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

## Extended post-restart verification

**3. Confirm daemon service state and environment:**

```bash
launchctl print gui/$(id -u)/com.zeus.data-ingest | grep -E "state|ZEUS_MODE|exit_status"
```

Expected: `state = running`. `ZEUS_MODE` will not appear — `com.zeus.data-ingest` runs
`src.ingest_main` (data ingest only) and does not require `ZEUS_MODE=live`. Live order
routing lives in `com.zeus.live-trading` (runs `src.main`), which already carries
`ZEUS_MODE=live`. See plist audit below.

**4. Confirm first new `mx2t3` row via Python:**

```bash
python -c "import sqlite3; print(sqlite3.connect('state/zeus-world.db').execute(\"SELECT data_version, MAX(issue_time) FROM ensemble_snapshots_v2 WHERE data_version LIKE '%mx2t3%' GROUP BY data_version\").fetchall())"
```

Expected: `[('ecmwf_opendata_mx2t3_local_calendar_day_max_v1', '<timestamp>')]`

## ZEUS_MODE plist audit (2026-05-07)

| Plist | Program | ZEUS_MODE=live | Notes |
|---|---|---|---|
| `com.zeus.data-ingest` | `src.ingest_main` | Not needed | Ingest only — no live order path |
| `com.zeus.live-trading` | `src.main` | YES (present) | Live order routing daemon |
| `com.zeus.riskguard-live` | `src.riskguard.riskguard` | YES (present) | Risk guard |
| `com.zeus.calibration-transfer-eval` | `scripts.evaluate_calibration_transfer_oos` | Not needed | Offline eval script |
| `com.zeus.heartbeat-sensor` | `bin/heartbeat_sensor.py` | Not needed | Health monitor only |

Verdict: no plist changes required. `ZEUS_MODE=live` is correctly present on every
daemon that routes live orders.

## Notes

- The `ThrottleInterval` in the plist is 30 s, so the daemon will restart within 30 s
  if it crashes; the `unload`+`load` pair ensures it picks up the post-merge venv.
- Do NOT restart `com.zeus.live-trading` or `com.zeus.riskguard-live` unless instructed
  separately — only the ingest daemon needs to reload for this change.
- Legacy `mx2t6/mn2t6` rows already in `ensemble_snapshots_v2` are preserved (D1 bridge
  ensures they still route to a Platt calibration target).
