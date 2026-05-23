# Forecast-Live Operator Handoff

Date: 2026-05-14

Status: repo handoff only. This file does not authorize `launchctl`, plist
installation, production DB mutation, live venue actions, or daemon deployment.

## Process Boundary

New entry point:

```bash
python -m src.ingest.forecast_live_daemon
```

Recommended launch label for a future operator-applied plist:

```text
com.zeus.forecast-live
```

Do not replace `com.zeus.data-ingest` with this label. After this refactor,
the intended split is:

| Label | Entry | Role |
|---|---|---|
| `com.zeus.forecast-live` | `src.ingest.forecast_live_daemon` | OpenData HIGH/LOW live forecast producer and heartbeat only |
| `com.zeus.data-ingest` | `src.ingest_main` | Legacy mixed ingest, world maintenance, source health, ingest status, UMA/market support until future split |
| `com.zeus.live-trading` | `src.main` | Live trading evaluator/executor path |

## Timing Contract

`forecast_live_daemon` schedules exactly four jobs:

| Job | Trigger | Time / Interval | SLO |
|---|---|---:|---|
| `forecast_live_opendata_daily_mx2t6` | cron UTC | 07:30 | one OpenData HIGH run, `max_instances=1`, `misfire_grace_time=3600` |
| `forecast_live_opendata_daily_mn2t6` | cron UTC | 07:35 | one OpenData LOW run, `max_instances=1`, `misfire_grace_time=3600` |
| `forecast_live_opendata_startup_catch_up` | date | daemon start | one HIGH+LOW catch-up attempt |
| `forecast_live_heartbeat` | interval | every 60 seconds | updates `state/daemon-heartbeat-forecast-live.json` |

Executors are intentionally narrow:

- `default`: 1 worker for OpenData jobs.
- `fast`: 1 worker for heartbeat.

This prevents unrelated ingest work from starving forecast production and makes
heartbeat freshness a measurable process-health signal.

## Mutual Exclusion

Both `src.ingest.forecast_live_daemon` and legacy `src.ingest_main` acquire the
same `ecmwf_open_data` daemon lock before OpenData collection. If both processes
are accidentally running during rollout, one owner returns:

```json
{"status":"skipped_lock_held","source":"ecmwf_open_data","track":"<track>"}
```

This is a rollout guard, not a permanent reason to keep duplicate schedulers.
Future operator deployment should eventually remove OpenData scheduling from
`com.zeus.data-ingest` after the forecast-live label is stable.

## Verification

After an operator-applied launch, verify process and heartbeat:

```bash
launchctl print gui/$(id -u)/com.zeus.forecast-live | grep -E "state|exit_status"
python - <<'PY'
import json, time
from datetime import datetime, timezone
from pathlib import Path

path = Path("state/daemon-heartbeat-forecast-live.json")
payload = json.loads(path.read_text())
updated = datetime.fromisoformat(payload["updated_at"])
age = datetime.now(timezone.utc).timestamp() - updated.timestamp()
print({"daemon": payload.get("daemon"), "pid": payload.get("pid"), "age_seconds": round(age, 1)})
PY
```

Expected:

- `state = running`;
- heartbeat `daemon` equals `forecast_live`;
- heartbeat age is less than 120 seconds after steady state.

Verify data freshness through canonical DB/readiness evidence, not just process
liveness:

```bash
sqlite3 state/zeus-world.db "
SELECT source_id, track, status, completeness_status, MAX(fetch_finished_at)
FROM source_run
WHERE source_id = 'ecmwf_open_data'
GROUP BY source_id, track, status, completeness_status
ORDER BY MAX(fetch_finished_at) DESC;
"
```

Expected after a successful run:

- `source_run.status = SUCCESS`;
- `source_run.completeness_status = COMPLETE`;
- matching `source_run_coverage.readiness_status = LIVE_ELIGIBLE`;
- matching `readiness_state.strategy_key = producer_readiness` row is fresh and unexpired.

## Operator Guardrails

- Do not fix OpenData freshness by restarting `com.zeus.live-trading`.
- Do not use forecast-live launch as approval for calibration refit, TIGGE
  activation, backfill, DB cleanup, or settlement source routing.
- Do not treat `com.zeus.data-ingest` liveness as proof that live forecast data
  is fresh; prove freshness with `source_run`, `source_run_coverage`,
  `readiness_state`, and heartbeat age.
- Do not claim deployment until the operator applies and verifies the plist
  outside this repo patch.
