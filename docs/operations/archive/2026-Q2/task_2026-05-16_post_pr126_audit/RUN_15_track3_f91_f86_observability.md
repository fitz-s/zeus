# Run #15 — Track 3: F91 heartbeat consumer trace + F86 SIGTERM forensic

**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `7fb380c` (local, ahead of origin)
**Date**: 2026-05-17
**Scope**: trace every heartbeat writer ↔ consumer; trace every SIGTERM event last 3d on live-money daemons.
**Mode**: READ-ONLY production. No production code edits.

---

## TASK A — Heartbeat writer ↔ consumer wire diagram

### Writers (5 heartbeat surfaces)

| # | File | Writer site | Cadence | Payload shape |
|---|---|---|---|---|
| HB-1 | `state/daemon-heartbeat.json` | `src/main.py:390` `_write_heartbeat` (live-trading daemon, APScheduler job id=`heartbeat`) | every 60 s | `{alive, timestamp, mode}` (3 fields) |
| HB-2 | `state/daemon-heartbeat-ingest.json` | `src/ingest_main.py:170` (data-ingest daemon, 60 s scheduler tick) | every 60 s | `{daemon, alive_at, pid}` |
| HB-3 | `state/forecast-live-heartbeat.json` | `src/ingest/forecast_live_daemon.py:72` `_write_forecast_live_heartbeat` | every 30 s (also on start, scheduler_ready, stopping) | `{cadence_seconds, daemon, jobs[], pid, status, timestamp, written_at}` |
| HB-4 | `state/venue-heartbeat-keeper.json` | `src/control/heartbeat_supervisor.py:177` `write_heartbeat_keeper_status` (run_heartbeat_keeper loop) | every 5 s | rich — `{consecutive_failures, consecutive_successes, health, heartbeat_id, last_error, last_success_at, last_failure_at, lease_continuous_since, lease_gap_suspected_until, owner, resting_order_safe, schema_version, written_at}` |
| HB-5 | `data/oracle/oracle_error_rates.heartbeat.json` | `src/state/paths.py:183` `write_heartbeat` (called from `scripts/bridge_oracle_to_calibration.py:382`) | per-artifact-write (paired) | `{sha256, written_at}` + artifact metadata |

### Consumers (3 reader sites — 2 real, plus 3 orphan scripts)

| # | Consumer | Reads | How invoked | Action on stale/missing |
|---|---|---|---|---|
| RD-A | `src/main.py:920,944` via `fresh_heartbeat_id_from_status()` in `src/control/heartbeat_supervisor.py:198` | HB-4 (venue) | At live-trading daemon **startup** | seeds initial CLOB lease token; missing/stale → empty token, daemon registers fresh chain |
| RD-B | `src/main.py:445-453` via `ExternalHeartbeatSupervisor` in `src/control/heartbeat_supervisor.py:227` | HB-4 (venue) | At live-trading daemon startup (configured as global supervisor) + runtime gate | read-only gate; returns degraded state on stale read |
| RD-C | `scripts/check_daemon_heartbeat.py:35` | HB-1 (daemon) | **MANUAL ONLY** — referenced in `docs/runbooks/live-operation.md:109`; no cron, no plist, no daemon caller | exits 1 if file missing or >300 s stale |
| RD-D | `scripts/check_forecast_live_ready.py:662 `_heartbeat_check` | HB-3 (forecast-live) | **MANUAL ONLY** — referenced in `docs/runbooks/forecast-live-daemon.md:97`; imported by `scripts/check_forecast_live_e2e.py` (also unscheduled) | CheckResult fail at >90 s default |
| RD-E | `scripts/live_health_probe.py:30,383` | HB-1 + HB-3 (refers FORECAST_LIVE_HEARTBEAT + reads `state/daemon-heartbeat.json`) | Driven by `scripts/live_health_monitor.sh` (operator-launched polling shell, NOT a launchd plist) and imported by `healthcheck.py` for `_git_runtime_identity` only — **the heartbeat-age fields in the probe are emitted but no autonomous consumer pages on them** | emits JSON, prints, no alert side-effect |

### Wire / no-wire verdicts

| HB | Writer | Consumer in autonomous loop? | Verdict |
|---|---|---|---|
| HB-1 daemon-heartbeat.json | live-trading | **NO** — `check_daemon_heartbeat.py` is unscheduled; `live_health_monitor.sh` is operator-run | **NO-WIRE** for alerting (write-only telemetry) |
| HB-2 daemon-heartbeat-ingest.json | data-ingest | **NO** — zero readers anywhere in `src/` or `scripts/` (grep returns only writer + comments) | **NO-WIRE** — pure disk churn |
| HB-3 forecast-live-heartbeat.json | forecast-live | **NO** — `check_forecast_live_ready.py` unscheduled, `live_health_probe.py` operator-only | **NO-WIRE** for alerting |
| HB-4 venue-heartbeat-keeper.json | venue-heartbeat | **YES** (functional) — consumed by live-trading at startup for lease-token recovery + as runtime gate | **WIRED** (functional consumer, not alert path) |
| HB-5 oracle_error_rates.heartbeat.json | bridge_oracle_to_calibration | **NO** — `write_heartbeat` writes paired file; zero readers in `src/` or `scripts/` (only `paths.py` definition site) | **NO-WIRE** — pair survives only for forensic re-derivation |

**Net**: 5 heartbeat writers; 1 has an in-loop functional consumer (HB-4, used for chain-safety, NOT alerting); 4 are write-only — including the daemon-liveness signals (HB-1, HB-2, HB-3) that operators would expect to drive an alert.

The only autonomous "alerter" is `scripts/heartbeat_dispatcher.py` (crontab `*/30 * * * *`), but it calls `scripts/healthcheck.py` which checks `status_summary.json` staleness, `source_health.json`, `risk_state.db` mtime, launchctl `state=running`, and `_launchctl_pid_for(...)`. **`healthcheck.py` does NOT grep any heartbeat JSON** (`grep heartbeat scripts/healthcheck.py` → 0 matches). So the every-30-min cron path never reads HB-1/HB-2/HB-3.

### Karachi-relevance flags

| HB | Karachi-critical? | Reasoning |
|---|---|---|
| HB-1 daemon (live-trading liveness) | **YES** | If live-trading dies during a Karachi position window, no autonomous alerter fires. heartbeat_dispatcher catches it via status_summary staleness only if status writer also fails. SEV-2. |
| HB-2 ingest (data freshness) | **YES** | F87 sibling — ingest die means observations/forecast feeds stale; no alert. SEV-2. |
| HB-3 forecast-live | **YES** | Already F87 SEV-1 (daemon was DOWN). Its own heartbeat is unread; F87 was discovered only by manual `launchctl list` + Track D sweep. |
| HB-4 venue | partial | venue dying = no lease-token writes → live-trading startup wedge → caught at next restart, not in real time. |
| HB-5 oracle | NO | calibration artifact, post-settlement; tier-2 telemetry. |

---

## TASK B — SIGTERM forensic gap (last 3 days)

### Current launchctl state (as of probe @ 2026-05-17T23:42 UTC)

```
PID    LastExit  Label
80628  -15       com.zeus.live-trading        (running, started Sun May 17 17:49:32 2026)
54734  -15       com.zeus.riskguard-live      (running, started Sun May 17 12:23:09 2026)
70301  -15       com.zeus.venue-heartbeat     (running, started Sun May 17 14:36:05 2026)
10397   1        com.zeus.forecast-live       (running, started Sat May 16 12:46:48 2026)
34316   0        com.zeus.data-ingest         (running, started Sat May 16 11:24:22 2026)
-       0        com.zeus.calibration-transfer-eval (one-shot)
-       0        com.zeus.heartbeat-sensor    (cron-driven; not a daemon)
```

All daemons configured `KeepAlive = 1` + `RunAtLoad = 1`; live-trading / riskguard-live / forecast-live / data-ingest have `ThrottleInterval = 30`.

### Event timeline (reconstructed from process-start gaps + .err scans)

| Daemon | Last respawn (process lstart) | Signal at prior exit | Cause inferable from .err? |
|---|---|---|---|
| live-trading | 2026-05-17 17:49:32 (~6 h ago) | -15 SIGTERM | **NO** — `grep -nE "SIGTERM\|received signal" logs/zeus-live.err` → 0 handler lines. Last 47 grep hits are FATAL strings about `world_schema_ready.json` stale containing "launchctl" only in the suggestion text. No daemon-side acknowledgement of the SIGTERM. |
| riskguard-live | 2026-05-17 12:23:09 (~12 h ago) | -15 SIGTERM | **NO** — `grep` of `logs/riskguard-live.err` → 0 SIGTERM/signal/launchctl hits. Daemon writes 11 MB of .err with no shutdown trace. |
| venue-heartbeat | 2026-05-17 14:36:05 (~10 h ago) | -15 SIGTERM | **NO** — `logs/zeus-venue-heartbeat.err` 4.5 KB, 0 SIGTERM hits. |
| forecast-live | 2026-05-16 12:46:48 (>30 h ago, currently UP) | 1 (non-zero exit, then respawn succeeded — F87) | **YES, partial** — `forecast_live_daemon.py:114` installs `signal.signal(SIGTERM, _graceful_shutdown)` with `logger.info("forecast-live daemon received SIGTERM; shutting down scheduler")`. F87 root cause (exit 1) is upstream of SIGTERM handler. |
| data-ingest | 2026-05-16 11:24:22 (>30 h ago) | 0 (clean) | **YES** — `ingest_main.py:1069` installs handler with `logger.info("data-ingest daemon received SIGTERM; shutting down scheduler")`. |

### Pattern detection

These are **NOT** scheduled restarts (no cron / no plist KeepAfterFullExit timer / no operator runbook entry that issues `launchctl kickstart -k`). The respawn pattern is `launchd KeepAlive=1` reacting to a process exit. The triggering SIGTERM source could be:
- macOS Sleep/Wake or system memory pressure (jetsam) — possible, but `log show --predicate 'process == "launchd"'` failed to return data within timeout, leaving this unverified;
- An operator running `launchctl kickstart -k gui/$UID/com.zeus.*` — possible (matches Track D F87 fix instructions);
- An unhandled Python exception that propagates → process exit → launchd re-spawn — but exit code would be Python's `1`, not `-15`. The `-15` specifically requires an external SIGTERM (signal 15).

### Signal-handler audit (the F86 essence)

| Daemon | Handler installed? | Logs shutdown cause to .err? |
|---|---|---|
| **live-trading** (`src/main.py`) | **NO** — `grep -nE "signal.signal\|SIGTERM\|signal_handler" src/main.py` → 0 hits | **NO** — SIGTERM kills silently |
| **riskguard** (`src/riskguard/riskguard.py`) | **NO** | **NO** |
| **venue-heartbeat** (`src/control/heartbeat_supervisor.py`) | **NO** | **NO** |
| data-ingest (`src/ingest_main.py:1069`) | YES (`_graceful_shutdown`) | YES (`logger.info("data-ingest daemon received SIGTERM…")`) |
| forecast-live (`src/ingest/forecast_live_daemon.py:805`) | YES | YES |

**The 3 daemons that exit -15 are EXACTLY the 3 daemons without SIGTERM handlers.** The 2 daemons with handlers (data-ingest, forecast-live) exit cleanly (0 / 1 respectively). Causation is plausible but not proven — the handlers may not be the reason for clean exit, but their absence is sufficient to explain the forensic gap.

### Alerting gap

- The every-30-min `heartbeat_dispatcher.py` cron path calls `healthcheck.py` which inspects launchctl `state` and PID via `_launchctl_pid_for(label)` but does NOT read `launchctl list <label>` to surface the prior `last_exit_status` field. So a daemon that gets SIGTERMed and respawned within the 30-min window is **never noticed** by the autonomous path.
- No file in `src/` or `scripts/` greps for `last_exit_status`, `last exit code`, or parses `launchctl list` exit codes. Operators rely on manual `launchctl list | grep zeus` (the exact discovery method used in Track D and again here).
- No daemon writes a `SHUTDOWN_SIGTERM` line, so post-mortem reconstruction is limited to (a) the gap between process-lstart values across observations, and (b) the macOS unified log (which itself is volatile and was not queryable within tool timeout).

### Antibody (1-line per daemon)

```python
# Add at top of src/main.py main(), src/riskguard/riskguard.py main(), and
# src/control/heartbeat_supervisor.py run_heartbeat_keeper():
import signal; signal.signal(signal.SIGTERM, lambda s, f: (logger.error("SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss", os.getpid(), os.getppid(), int(time.monotonic()-_start)), sys.exit(0)))
```

Where `_start = time.monotonic()` is captured at process start. This single line (a) emits a forensic trail to .err (which alert tooling can grep), (b) yields a `SIGTERM_RECEIVED` counter for the existing `live_health_probe.py` to expose, and (c) preserves graceful exit. Optional follow-up: add an `Alerter.fire("daemon_sigterm", label, ppid)` call inside the lambda to page operator when ppid != launchd-uid-pid (i.e., human-issued kill).

### Stronger antibody (system-level)

Extend `healthcheck.py` (cheaper than per-daemon code change):
```python
# After existing _launchctl_loaded_contract() call, parse last_exit_status:
exit_status = _first_launchctl_field(launchctl_print_output, "last exit code")
if exit_status not in ("0", "(never exited)"):
    result.add_warning(f"{label} last_exit_status={exit_status} since startup={start_time}")
```
This lifts the prior-exit signal into the every-30-min dispatcher path without touching live-money daemon code, and is the lowest-risk wire.

---

## New findings (Track 3 — F99/F100/F101; F93–F95 already taken by Track 1)

### F99 — MEDIUM — Heartbeat write/read asymmetry: 4 of 5 heartbeat surfaces are write-only

**Evidence**: HB-1/HB-2/HB-3/HB-5 have no autonomous consumer; only HB-4 (venue) is read by the runtime, and even that is functional (lease seed), not alerting. 4 daemons keep writing every 30–60 s and nothing on the system reads the result.

**Why MEDIUM**: silent observability is worse than no observability — operators believe they have a heartbeat-driven alerting story (because the files exist and update) but the alerting loop is open. Same pattern as F85 (.err vs .log inversion) and F90 (cron/jobs.json vs crontab drift): repo artifact present, runtime wiring absent.

**Fix path**: pick ONE of:
- (a) schedule `check_daemon_heartbeat.py` + `check_forecast_live_ready.py` in crontab (every 5 min) with alert on non-zero exit; or
- (b) fold heartbeat-staleness checks into `healthcheck.py` (preferred — already runs every 30 min via heartbeat_dispatcher; one consolidated alerter).

**Karachi impact**: MEDIUM. Combined with F87 (forecast-live had been DOWN), the lack of autonomous staleness alerting on HB-3 is the reason F87 went undetected until manual sweep.

### F100 — MEDIUM — daemon-heartbeat-ingest.json has zero readers anywhere

**Evidence**: `grep -rn "daemon-heartbeat-ingest.json"` returns only writer site + comments. No script, no test, no health probe, no consumer.

**Why MEDIUM**: pure disk churn for 30+ hours. The data-ingest daemon is the upstream of forecast lineage; its liveness should be a first-class observability target.

**Fix**: add to consolidated `healthcheck.py` staleness check (with HB-1, HB-3).

**Karachi impact**: same surface as F99. SEV-2.

### F101 — LOW — Schema drift across heartbeat payloads

**Evidence**: 5 writers, 5 schemas:
- HB-1: `{alive, timestamp, mode}` (3 fields)
- HB-2: `{daemon, alive_at, pid}` (3 fields, different key names)
- HB-3: 7 fields including `status`, `jobs[]`, `cadence_seconds`
- HB-4: 13 fields with full health detail
- HB-5: artifact-derived schema

**Why LOW**: any cross-daemon staleness checker would need 5 parsing branches. A single shared schema (or a minimal envelope `{daemon, written_at, status, pid}` with optional payload) would make a generic checker trivial.

**Fix**: introduce `src/state/heartbeat_envelope.py` with `HeartbeatEnvelope` dataclass; refactor writers in a follow-on PR.

**Karachi impact**: none.

---

## Cross-reference

- F86 (Track D Run #14): SIGTERM forensic gap — this run confirms gap is real for live-trading/riskguard/venue-heartbeat; identifies 1-line antibody.
- F91 (Track D Run #14): heartbeat consumer chain unverified — this run resolves AMBIGUOUS → **CONFIRMED-NO-WIRE for 4 of 5 surfaces**; HB-4 promoted to WIRED-FUNCTIONAL.
- F87 (Track D Run #14): the F99/F100 alerting gap explains why F87 was discovered manually rather than autonomously.

---

## Recommended remediation order (post Run #15)

| Priority | Finding | Why |
|---|---|---|
| 1 | F86 system-level antibody (healthcheck.py reads last_exit_status) | catches all 3 silent SIGTERM daemons in one place, no live-money code change |
| 2 | F86 per-daemon SIGTERM handler 1-liners | belt-and-suspenders, emits forensic line to .err |
| 3 | F99/F100 fold staleness checks into healthcheck.py | closes the alerting loop on HB-1/HB-2/HB-3 |
| 4 | F101 unify schema (post-Karachi 5/19) | hygiene |
