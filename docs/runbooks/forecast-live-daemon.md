# Forecast Live Daemon Wiring Runbook

Status: wiring material only. This runbook does not authorize launchctl
changes, daemon start/restart, production DB writes, external source fetches,
or live venue side effects.

Authority basis:

- `docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md`
- `src/ingest_main.py` owner switch `ZEUS_FORECAST_LIVE_OWNER`
- `src/ingest/forecast_live_daemon.py` forecast-live scheduler boundary

## Completion Boundary

Using this runbook can support `OPERATOR_LAUNCH_READY` only. It cannot support
`LIVE_RUNNING`, `PRODUCER_READY`, `LIVE_CONSUMING`, or `DONE` without the
post-launch evidence named in the data-daemon plan.

The forecast-live daemon writes `state/forecast-live-heartbeat.json` at startup
and every 30 seconds on the dedicated scheduler `heartbeat` executor. Heartbeat
evidence can support `LIVE_RUNNING` only when paired with process evidence. It
does not prove `PRODUCER_READY`, `LIVE_CONSUMING`, or `DONE` without fresh
authority-chain rows and live-reader evidence.

## Preconditions

All of these must be true before an operator launch window:

1. The implementation branch has passed its code and topology gates.
2. `ZEUS_FORECAST_LIVE_OWNER` is unset or `ingest_main` in the current runtime
   until cutover begins.
3. No `python -m src.ingest.forecast_live_daemon` process is already running.
4. The live trading daemon is not depending on fresh producer readiness from
   the new daemon yet.
5. Operator approval explicitly names the allowed action: staging launch,
   canonical launch, rollback, or read-only verification.

## Single-Owner Cutover

The cutover has two separate process responsibilities:

1. Legacy ingest remains the default owner. With `ZEUS_FORECAST_LIVE_OWNER`
   unset or set to `ingest_main`, `src.ingest_main` may register OpenData
   forecast jobs.
2. Forecast-live ownership is explicit. With `ZEUS_FORECAST_LIVE_OWNER` set to
   `forecast_live`, legacy ingest must not register OpenData HIGH/LOW daily
   jobs or startup catch-up.

Do not start the forecast-live daemon until legacy ingest has been restarted
under forecast-live owner mode and its OpenData job absence has been verified
from logs, job-spec dry-run evidence, or an operator-approved status command.

## Operator-Approved Launch Sequence

These are instructions for an approved launch window, not actions to run during
code review.

1. Freeze the runtime head and record the commit SHA.
2. Update the existing legacy ingest process environment so the next start has:

   ```bash
   ZEUS_FORECAST_LIVE_OWNER=forecast_live
   ```

   Then restart the existing legacy ingest instance through its operator-approved
   supervisor path. Do not start a second `src.ingest_main` process.

3. Verify legacy ingest did not register:

   - `opendata_daily_mx2t6`
   - `opendata_daily_mn2t6`
   - `opendata_startup_catch_up`

4. Start forecast-live only after step 3 is proven:

   ```bash
   python -m src.ingest.forecast_live_daemon
   ```

5. Record process evidence:

   ```bash
   pgrep -af 'python -m src.ingest.forecast_live_daemon'
   pgrep -af 'python -m src.ingest_main'
   ```

6. Record scheduler evidence from forecast-live logs:

   - `forecast_live_opendata_daily_mx2t6`
   - `forecast_live_opendata_daily_mn2t6`
   - `forecast_live_opendata_startup_catch_up`
   - `forecast_live_heartbeat`

7. Record heartbeat and authority-chain evidence:

   ```bash
   python3 scripts/check_forecast_live_ready.py --claim-mode post-launch --json
   ```

## Read-Only Verification

Use read-only DB handles or copied DBs for these checks unless the operator
has approved a write stage.

Prefer an operator-approved copy for launch evidence. If querying the live
SQLite file directly, use a read-only URI such as
`sqlite3 'file:state/zeus-forecasts.db?mode=ro'` and do not run checkpoint, VACUUM,
schema migration, cleanup, or backfill commands from this runbook.

Forecast-live work journal:

```sql
SELECT job_name, scheduled_for, source_id, track, release_calendar_key,
       status, reason_code, rows_written, rows_failed, recorded_at
FROM job_run
WHERE job_name LIKE 'forecast_live_opendata_%'
ORDER BY recorded_at DESC
LIMIT 10;
```

Forecast authority chain:

```sql
SELECT source_run_id, source_id, track, release_calendar_key,
       source_cycle_time, status, completeness_status, reason_code, recorded_at
FROM source_run
WHERE source_id = 'ecmwf_open_data'
ORDER BY recorded_at DESC
LIMIT 10;
```

Producer readiness:

```sql
SELECT source_id, track, source_run_id, status, computed_at, expires_at
FROM readiness_state
WHERE source_id = 'ecmwf_open_data'
ORDER BY computed_at DESC
LIMIT 10;
```

The operator may claim `PRODUCER_READY` only when HIGH and LOW have current
source runs, current coverage/readiness rows, and non-expired live-eligible
readiness for the target scope. Partial or failed source runs must show blocked
or non-live-eligible readiness.

## Rollback

Rollback must restore a single owner and must not delete canonical DB rows.

1. Stop the forecast-live daemon process.
2. Set `ZEUS_FORECAST_LIVE_OWNER=ingest_main` or unset it for legacy ingest.
3. Restart legacy ingest only after the operator approves the restart.
4. Verify legacy OpenData job registration is present again.
5. Verify no forecast-live process remains.
6. Record `state/forecast-live-heartbeat.json`, then clear only that
   runtime-local heartbeat if the operator wants stale-heartbeat noise removed.
   Do not remove `source_run`, `source_run_coverage`, `readiness_state`,
   `ensemble_snapshots_v2`, or `job_run` rows as part of rollback.

## Evidence Checklist

Record these items for any launch or rollback claim:

- runtime commit SHA
- owner environment for legacy ingest
- process list for legacy ingest, forecast-live, and live trading daemon
- legacy OpenData job absence or presence, depending on cutover or rollback
- forecast-live scheduler job list
- forecast-live heartbeat age and payload
- latest HIGH and LOW `job_run` rows
- latest HIGH and LOW `source_run` rows
- latest HIGH and LOW coverage/readiness rows
- source-health state, including any `THROTTLED` or dependency-failed status
- live evaluator blocker status for eligible candidates
- rollback command/result if rollback was exercised

## Forbidden In This Runbook

- Creating or loading launchd plist files.
- Running launchctl commands.
- Starting or restarting daemons without explicit operator approval.
- Mutating production DBs during verification.
- Clearing stale data, deleting duplicate DBs, or backfilling historical rows.
- Claiming `DONE` from process liveness alone.
