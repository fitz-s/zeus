# RUN — 2026-05-09 daemon restart + 317 backfill

Dispatched: 2026-05-09 sonnet `a5a175a84c62a20b3` (per `TASK.md`)
Status: **PHASE_A_BLOCKED_LAUNCHCTL_SANDBOX — D ALREADY RESOLVED — B/C waiting on A**
Sonnet outcome: 600s stream-watchdog stall; no RUN.md written by sonnet; orchestrator (opus) wrote this report from post-mortem diagnostics. Initial draft was revised after deeper probes corrected two false alarms.

## Pre-state (probed 2026-05-09 ~10:33 CDT)

| Surface | Value |
|---|---|
| `git rev-parse HEAD` | `b4c42aeb` (PR #103, #104 landed since TASK.md was authored at `cd43945e`) |
| `state/daemon-heartbeat-ingest.json` mtime | 2026-05-09 10:32 CDT — **fresh (~1 min)** |
| `logs/zeus-ingest.err` first line | `2026-05-01 19:21:45 [zeus.ingest] INFO: Zeus data-ingest daemon starting (pid=66321)` |
| `logs/zeus-ingest.err` size | 97 MB, single continuous stream — **no restart entries since 2026-05-01** |
| `forecasts` ECMWF previous-runs (K2 family) latest | `2026-05-15T06:44:00+00:00` (11340 rows; FRESH) |
| `forecasts` blank-`source_id` legacy ECMWF latest | `2026-05-03T12:00:00+00:00` (25149 rows) |
| `forecasts` `source_id='ecmwf_open_data'` | **0 rows** (canonical pipeline never started writing here) |
| `source_run` table for `ecmwf_open_data` | 4 rows total, latest cycle `2026-05-04T00`, oldest `2026-05-03T00` |
| `scheduler_jobs_health.json::ingest_opendata_startup_catch_up::last_success_at` | `2026-05-09T14:42:29Z` (status=OK — but stale-pid path) |
| `readiness_state` BLOCKED count | 100 (`SOURCE_RUN_HORIZON_OUT_OF_RANGE`) — all on target dates 2026-05-13 (65) and 2026-05-14 (35) |
| `readiness_state` LIVE_ELIGIBLE | 477 (`PRODUCER_COVERAGE_READY`) |
| `settlements_v2` London authority breakdown | **VERIFIED=471, QUARANTINED=2** |
| `settlements` (legacy) London breakdown | VERIFIED=472, QUARANTINED=2 |

## Findings (corrected)

### Finding 1 — Phase A `launchctl` restart did not occur (CONFIRMED)

The daemon process at pid `66321` has been continuously running since 2026-05-01 19:21:45. No "Zeus data-ingest daemon starting" entry exists after that in 8 days of logs. The sonnet's `launchctl unload` + `launchctl load` commands either:
- Were silently blocked by the agent process sandbox (orchestrator's own `launchctl list | grep zeus` was killed with exit 137 SIGKILL — same sandbox behavior), or
- Failed before the agent could report.

**Consequence**: PR #101 F1 subprocess hardening (timeout 600→1500s, bounded retry [0,60], full stderr capture, daemon-path `db_writer_lock` retrofit) is **NOT loaded** into the running daemon. The daemon is still executing the 2026-05-01 binary. This is the single remaining blocker.

### Finding 2 — Live ECMWF data path IS healthy; only the new canonical pipeline is empty

Earlier draft framed this as "daemon broken" — that was wrong.

`forecasts` table breakdown shows **`ecmwf_previous_runs` (K2 family) is fresh through 2026-05-15T06:44** with 11,340 rows. Live trading inputs are flowing. The empty pipeline is the new canonical `source_run`-backed ECMWF Open Data path that PRs #94/#95/#96/#100/#101 build out — that path requires the daemon to load PR #101 hardening before it can complete a fetch (the OLD code in pid 66321 hits the timeout/retry bug that motivated F1).

The 100 BLOCKED readiness rows are on the new canonical pipeline (target dates 2026-05-13 / 2026-05-14 → require `source_run` rows for those horizons → require post-PR-#101 daemon).

`_record_success()` predicate gap (success-path doesn't gate on `rows_written > 0`) is real but not urgently blocking — once F1 hardening lands, real successes will start landing rows and the predicate-gap stops being exercised. Tracked for follow-up but not on critical path today.

### Finding 3 — Phase D 317 backfill is effectively ALREADY DONE (CORRECTION)

Earlier draft asserted the backfill script would fail with `OperationalError: no such column: quarantine_reason`. That was wrong. The script's actual SQL is:

```sql
SELECT settlement_id, city, target_date, ...
FROM settlements_v2
WHERE city = ? AND authority = 'QUARANTINED'
```

— it filters `authority='QUARANTINED'` at the SQL layer, then reads `provenance_json` via `json.loads()` in Python and filters `quarantine_reason` from the parsed dict. The column-not-found assumption was based on a misread of TASK.md's verification SQL, not the script.

Current London QUARANTINED count: **2** (was 317 at task creation time). Both residuals inspected:

| id | target_date | obs (°C) | bin (°F) | quarantine_reason | classification |
|---|---|---|---|---|---|
| 9 | 2025-01-26 | 10.0 | 51-51 (point, ≈10.55°C → snap 11) | `harvester_source_disagreement_within_tolerance` | source-disagreement legitimate (within ±1°C tolerance from PR #100) |
| 134 | 2025-03-30 | 11.0 | 63-64 (≈17.22-17.78°C → snap {17,18}) | `harvester_live_obs_outside_bin` | genuine outside-bin (obs 11°C far from {17,18}°C) |

The 315 other rows resolved naturally — likely by PR #100's runtime integer-snap logic re-running on subsequent harvester ticks, OR by an apply already done before this dispatch. Whatever the path, **Phase D's goal (drop 317 → close to 0) is met**. The two residuals are correct: one is a flagged source-disagreement (PR #100 enum is doing its job), one is a genuine outside-bin (operator should not auto-resolve).

Running `--apply` now would be a no-op (idempotency tag check at line 136 of the script + already-not-in-target-set).

## Phase status

| Phase | Status | Notes |
|---|---|---|
| A — daemon restart | **BLOCKED** | macOS sandbox blocks `launchctl` from agent and orchestrator processes. Operator must execute manually. |
| B — fresh ECMWF source_run | **WAITING** on A | Once A done, daemon's startup-catch-up will fire with PR #101 hardening; expect first `source_run` row for 2026-05-09/00Z within ~10 min. |
| C — 100 BLOCKED resolution | **WAITING** on B | After source_run row(s) arrive, run `python3 scripts/reevaluate_readiness_2026_05_07.py --apply`. Expect BLOCKED → close to 0. |
| D — 317 London backfill | **EFFECTIVELY DONE** | 2 legitimate residuals remain; `--apply` is a safe no-op. No action required. |

## Operator handoff — single required action

Run from a non-sandboxed terminal:

```
launchctl unload ~/Library/LaunchAgents/com.zeus.data-ingest.plist
launchctl load   ~/Library/LaunchAgents/com.zeus.data-ingest.plist
```

In this Claude Code session, the operator can also dispatch this via `! launchctl unload …` / `! launchctl load …`. The `!` prefix routes the command through the user's shell (not the agent sandbox) so the OS process-management call is permitted.

Verify:
1. `tail logs/zeus-ingest.err` shows a fresh "Zeus data-ingest daemon starting (pid=NEW)" line.
2. Within 10 min: `sqlite3 state/zeus-world.db "SELECT MAX(source_cycle_time) FROM source_run WHERE source_id='ecmwf_open_data'"` returns a 2026-05-09 value.
3. Run `python3 scripts/reevaluate_readiness_2026_05_07.py --apply`. Expect BLOCKED count drop.

## Follow-ups (not blocking today)

- **`_record_success()` predicate fix**: gate on `rows_written > 0`, not "no exception". Track in a future hardening task.
- **TASK.md verification SQL drift**: TASK.md uses `WHERE quarantine_reason='...'` but the actual schema stores it in `provenance_json`. Update TASK.md template for next dispatch.

## Post-state

Identical to pre-state. No state was mutated by this dispatch.

## VERDICT

`PHASE_A_BLOCKED_LAUNCHCTL_SANDBOX docs/operations/task_2026-05-09_daemon_restart_and_backfill/RUN.md`

D is already done. B and C unblock automatically once operator runs the two `launchctl` commands above.
