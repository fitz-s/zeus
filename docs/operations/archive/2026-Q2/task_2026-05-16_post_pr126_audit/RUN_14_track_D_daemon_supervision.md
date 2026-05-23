# Run #14 — Track D: Daemon supervision sweep (F85+)

**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `b973ece`
**Date**: 2026-05-17
**Scope**: launchd plists, launchctl status, cron drift, heartbeat consumer chain, exit codes.

---

## New findings F85 – F92

### F85 — HIGH — Daemon stdout/stderr inversion: all noise on .err, .log files dead

**Evidence**: Across 7 zeus launchd services, the .log files are stale (size 0B – 860B, mtime 5/14–5/15) while the .err files are HOT (KB-to-MB, mtime within last hour). Daemons are writing all output (info + warn + error) to stderr exclusively; stdout is empty.

```
~/Library/Logs/zeus-*.log    : 0–860B, last write 2026-05-14 ~ 2026-05-15
~/Library/Logs/zeus-*.err    : 1KB – 3MB, last write 2026-05-17 (active)
```

**Why this is HIGH**: log-rotation, log-aggregation, alerting, and grep-based triage rules typically split on stdout vs stderr. If `.err` carries everything, every INFO line trips error-class alerts; conversely a real ERROR is buried in INFO traffic. Operator cannot distinguish "warning noise" from "real failure" by file or by stream.

**Fix**: pick one — either (a) standardize all daemons on a single python `logging` config that splits INFO→stdout, WARN+→stderr; OR (b) accept stderr-only and rename `.log` → `.err`-only in plist `StandardOutPath` directives, deleting the dead stdout files. Author recommendation: (a) — preserves the semantic boundary.

**Antibody**: launchd-plist linter checking that `StandardOutPath` and `StandardErrorPath` differ AND the daemon's logging config maps INFO/WARN/ERROR correctly.

---

### F86 — HIGH — SIGTERM `exit -15` on live-trading / riskguard / venue-heartbeat without audit trail

**Evidence**: `launchctl list | grep -i zeus` shows several daemons with last exit code `-15` (SIGTERM): live-trading, riskguard, venue-heartbeat. No corresponding entry in `.err` log explaining the kill cause (operator kill, OS resource pressure, launchd unload, etc.).

**Why this is HIGH**: silent SIGTERM means a supervisor (or human) is killing live-money daemons without leaving forensic traces. If this happens during a Karachi-style position window, the only signal is `launchctl list` exit code; the operator has no log entry to reconstruct *why*.

**Fix**:
1. Add a SIGTERM handler to each live-money daemon (`src/control/heartbeat_supervisor.py`, riskguard, live_trading) that writes a final `SHUTDOWN_SIGTERM received from PID=X cause=…` line to .err before exit.
2. Add a launchd `ExitTimeOut` audit script (`tools/ops/launchd_exit_audit.sh`) invoked from each plist's `ExitTimeOut` field, recording exit code + signal + last PID to `~/Library/Logs/zeus-launchd-exit-audit.log`.

**Antibody**: pytest `test_daemons_log_sigterm_cause` — assert each daemon entry-point installs a SIGTERM handler.

---

### F87 — CRITICAL — `com.zeus.forecast-live` launchctl exit code = 1 (FAILED), .err = 3 MB

**Evidence**:
```
$ launchctl list | grep -i forecast
-    1    com.zeus.forecast-live
```
Exit code 1, currently NOT running (`-` PID). `~/Library/Logs/zeus-forecast-live.err` is 3 MB, mtime within last 24h — daemon has been crash-looping or producing dense error output and is now stopped.

**Why this is CRITICAL**: forecast-live is the live ECMWF / forecast ingestion daemon. If down, `forecasts.db` stops receiving fresh `observations`, `ensemble_snapshots_v2`, etc. — which means `_persistence_discount` (F48) and any other forecasts-conn-using helper hits stale data. Karachi 5/17 + 5/19 windows depend on this.

**Fix**:
1. Triage the .err immediately: `tail -200 ~/Library/Logs/zeus-forecast-live.err` to identify the crash signature. (Done in this audit — see investigate-further item.)
2. Restart: `launchctl kickstart -k gui/$(id -u)/com.zeus.forecast-live`.
3. Add a `KeepAlive { SuccessfulExit: false }` to the plist so launchd auto-restarts on non-zero exit.

**Antibody**: `tools/ops/zeus_daemon_health.sh` cron job (every 5 min) — pages operator if any zeus-* daemon shows `-` PID with non-zero exit code.

**Karachi impact**: **HOT**. If forecasts stop refreshing, monitor_refresh (already buggy per F48) becomes doubly compromised. Fix-and-restart BEFORE next Karachi monitor tick.

---

### F88 — MEDIUM — `calibration-transfer-eval` daemon is once-daily, last run 5/17 04:00 OK

**Evidence**: plist sets `StartCalendarInterval` to 04:00 daily; last .log entry 5/17 04:00:43, exit 0. Functioning as designed. Flagging only because the name suggests "live eval" which a reader might assume is real-time.

**Fix**: rename plist to `com.zeus.calibration-transfer-eval-daily` to make schedule explicit.

**Karachi impact**: none.

---

### F89 — LOW — `heartbeat-sensor` not in launchctl; dispatched by cron `*/30`

**Evidence**: No `com.zeus.heartbeat-sensor` plist; cron has `*/30 * * * * /Users/leofitz/.openclaw/workspace-venus/zeus/scripts/heartbeat_sensor.sh` (verified via `crontab -l`). Runs every 30 min, last invocation OK.

**Why LOW**: works, but supervision is split between launchd (other daemons) and cron (this one). Inconsistent ops topology = higher cognitive load + drift risk.

**Fix** (optional): migrate to launchd plist with `StartInterval=1800` for consistency.

---

### F90 — HIGH — `cron/jobs.json` 82 KB vs `crontab -l` only 2 lines — massive drift

**Evidence**:
```
$ wc -c < cron/jobs.json
   82347
$ crontab -l | wc -l
        2
```

`cron/jobs.json` describes a 42-job catalog (per `CLAUDE.md`). Live `crontab -l` has only 2 entries (heartbeat sensor + one more). The repo-tracked job catalog is **NOT** the source of truth for what cron actually runs.

**Why HIGH**: any job listed in `cron/jobs.json` and assumed to be running by readers of the repo is likely NOT scheduled. Finance reports, oracle snapshots, ensemble ETLs, etc. — silently absent. This is exactly the lineage-ambiguity pattern F22/F46/F81/F82 surfaced for the DB layer, now on the scheduler layer.

**Fix**:
1. Build `tools/ops/cron_reconcile.py` that diffs `cron/jobs.json` vs `crontab -l` and exits 1 if either has entries the other lacks.
2. Decide source of truth: either (a) treat `cron/jobs.json` as authoritative and have a `make install-crontab` target that re-renders crontab from JSON; OR (b) accept that crontab is hand-edited and demote `cron/jobs.json` to a `cron/jobs.json.example` reference doc.
3. Audit the 40-job delta — many will be dead (finance jobs explicitly disabled in `backups/` filenames suggest deliberate disablement), but each needs a verdict.

**Karachi impact**: depends on which jobs are silently dropped. Need the reconcile diff before claiming impact.

---

### F91 — MEDIUM — heartbeat JSONs written every minute; consumer chain + alert path unverified

**Evidence**: `state/heartbeats/zeus-*.json` files updated every 60 s; `src/control/heartbeat_supervisor.py` exists; but no grep evidence of a consumer that reads these JSONs and pages on staleness. Heartbeat producer ≠ alert path.

**Why MEDIUM**: heartbeats are write-only telemetry until something reads them and acts. If the consumer is silent, heartbeats are just disk churn.

**Fix**: trace the consumer:
```bash
grep -rn "heartbeats/zeus-" src/ scripts/
grep -rn "heartbeat_supervisor\|HeartbeatSupervisor" src/ scripts/
```
If consumer exists, document its alert path. If not, build one: `tools/ops/heartbeat_check.py` → exit 1 if any heartbeat JSON older than 5 min.

**Karachi impact**: low for current window; medium for long-term ops hygiene.

---

### F92 — MEDIUM — riskguard `auth/api-key` 400 → derive-api-key fallback succeeds, no metric

**Evidence**: `~/Library/Logs/zeus-riskguard.err` shows `POST /auth/api-key → 400 Bad Request` followed immediately by `derive-api-key fallback → 200 OK` and continued operation. The fallback works (good), but there is no `RISKGUARD_AUTH_FALLBACK_TRIGGERED` counter or metric to track frequency.

**Why MEDIUM**: silent fallback = invisible degradation. If the primary auth path is permanently broken and fallback rate-limits, we will not know until fallback also fails.

**Fix**: increment a counter on each fallback (`metrics.counter('riskguard.auth.fallback_trigger').inc()`); alert when rate > 10/hr.

**Karachi impact**: none in current window (fallback works); medium for long-term.

---

## Recommended remediation order

| Priority | Finding | Why first |
|---|---|---|
| 1 (today) | **F87** | live forecast ingestion DOWN; touches Karachi |
| 2 (today) | **F48** (Track B) | persistence discount silently no-op for Karachi |
| 3 (this week) | **F90** | cron source-of-truth ambiguity = invisible job loss |
| 4 (this week) | **F85** | log/err inversion = operator triage blindness |
| 5 (this week) | **F86** | SIGTERM forensic gap on live-money daemons |
| 6 (next) | F91, F92, F88, F89 | hygiene + medium ops debt |

## Karachi 5/17 + 5/19 ops gate

Before next Karachi monitor tick, confirm:
- [ ] F87: forecast-live daemon RUNNING (`launchctl list | grep forecast-live` shows PID + exit 0)
- [ ] F48: `_persistence_discount('Karachi', 2026-05-17, …)` returns non-1.0 OR explicitly logs `PERSISTENCE_CHECK_DISABLED` AND we acknowledge the no-discount default
- [ ] F90: no critical Karachi-supporting cron jobs missing from live crontab
