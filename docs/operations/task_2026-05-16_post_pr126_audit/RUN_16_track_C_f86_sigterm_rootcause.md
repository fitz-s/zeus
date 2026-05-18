# Run #16 Track C — F86 SIGTERM root cause + alerting antibody

- **Date**: 2026-05-17
- **Worktree**: `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill`
- **Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17`
- **Mode**: READ-ONLY forensic. No code, plist, launchd, or DB state mutated.
- **Scope**: pin the source of the three live-money daemon SIGTERMs flagged in
  F86 (Run #15 T3 confirmed signal, did not pin issuer); rule each candidate
  category in/out with evidence; propose an observability antibody so the next
  occurrence is self-attributed.

---

## 1. Baseline (F86 carry-over)

Per Run #15 T3 + the current `launchctl print` snapshot (sampled today, the
plists have not been touched since):

```
launchctl list | grep zeus      # status, last exit, label
70301   -15     com.zeus.venue-heartbeat
80628   -15     com.zeus.live-trading
54734   -15     com.zeus.riskguard-live
10397   1       com.zeus.forecast-live      (carries SIGTERM handler — exits 1)
34316   0       com.zeus.data-ingest
-       0       com.zeus.calibration-transfer-eval
-       0       com.zeus.heartbeat-sensor
```

The three labels exit `-15` (`Terminated: 15`, SIGTERM) on every recent restart
incarnation. All three lack any in-process SIGTERM handler (confirmed Run #15
T3 source scan: only `src/main.py` and `src/ingest/forecast_live_daemon.py`
register `signal.SIGTERM`). The signal is therefore externally injected.

`launchctl print gui/501/com.zeus.<label>` for all 3 today:

| Label                       | runs | forks | last_term         | last_exit_reason | jetsam_active | exit_timeout |
|-----------------------------|------|-------|-------------------|------------------|---------------|--------------|
| com.zeus.live-trading       | 18   | 77    | Terminated: 15    | inefficient      | unlimited     | 5 s          |
| com.zeus.riskguard-live     | 5    | 572   | Terminated: 15    | inefficient      | unlimited     | 5 s          |
| com.zeus.venue-heartbeat    | 5    | 2     | Terminated: 15    | inefficient      | unlimited     | 5 s          |

`inefficient` is launchd's word for "respawn rate exceeded `ThrottleInterval`";
it is a *consequence* of frequent SIGTERMs, not a cause. Jetsam memory limit
`unlimited` ⇒ OS OOM-killer cannot be the source.

## 2. Timeline (last ~12 h, captured 2026-05-17)

Source: `log show --process launchd --info --last 12h | grep com.zeus`
(saved to `/tmp/run16_zeus_launchd.txt`, 273 zeus-related lines; SIGTERM-exit
lines extracted below).

| Time (CDT)            | Label(s) SIGTERMed                                | Prior run-time |
|-----------------------|---------------------------------------------------|----------------|
| 08:25:16.711          | `live-trading` (PID 56604)                        | 2 309 335 ms (~38 m) |
| 08:25:16.717          | `venue-heartbeat` (PID 56501)                     | 1 412 482 ms (~24 m) |
| 08:31:55 → 17:49:32   | `live-trading` ×17 additional restarts            | 222 s – 1 402 s (irregular) |

Inter-restart spacing across the 17 live-trading-only events: **3 min – 23 min,
no fixed period** (3:42, 4:58, 5:38, 6:39, 7:45, 8:34, 9:43, 10:26, 10:59,
12:38, 17:01, 17:46, 19:46, 20:51, 21:05, 23:23). Pattern is *interactive*, not
schedule-driven.

The 08:25:16 event is the smoking gun: two distinct labels SIGTERMed **6 ms
apart**. No supervisor in tree, no plist co-trigger, no scheduler key.
Six-ms separation is consistent with two `launchctl kickstart -k …` calls
issued back-to-back from one shell (or one wrapper script).

Every line in the launchd log carries the same attribution suffix:

```
2026-05-17 08:25:16.738680-0500 launchd: [gui/501/com.zeus.live-trading [56604]:]
    exited due to SIGTERM | sent by launchd[1], ran for 2309335ms
```

`sent by launchd[1]` is the kernel's recorded sender. On macOS, `launchd[1]`
records itself as sender whenever it injects SIGTERM in response to any of:
plist `KeepAlive` reload, `WatchPaths` fire, `StartInterval` boundary,
operator-issued `launchctl kickstart -k` / `launchctl stop` / `launchctl
bootout`, system sleep/wake throttle. The next sections rule each in/out.

## 3. Candidate categories — evidence table

| Category                | Hypothesis                                      | Verdict | Evidence                                                                                                                                                                                                                                                                                                              |
|-------------------------|-------------------------------------------------|---------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| SCHEDULED-RELOAD        | plist `StartInterval` / `WatchPaths` boundary   | **RULED OUT** | All 3 plists have no `StartInterval`, no `WatchPaths`, no `StartCalendarInterval`. Triggers in each: only `RunAtLoad`+`KeepAlive`. (`~/Library/LaunchAgents/com.zeus.{live-trading,riskguard-live,venue-heartbeat}.plist`.)                                                                                              |
| MEMORY-CAP              | plist `ResourceLimits` / `HardResourceLimits`   | **RULED OUT** | None of the 3 plists set `ResourceLimits`, `HardResourceLimits`, `MemoryHigh`, or `ProcessType`. `launchctl print` shows `jetsam memory limit (active) = unlimited`.                                                                                                                                                  |
| SUPERVISOR              | another zeus process sends SIGTERM              | **RULED OUT** | `grep -RnE "(signal\.SIGTERM\|os\.kill.*SIGTERM\|subprocess.*launchctl.*kickstart\|launchctl.*stop\|launchctl.*bootout)" src/ scripts/` returns only: (a) SIGTERM **handlers** in `src/main.py` and `src/ingest/forecast_live_daemon.py`; (b) liveness probe `os.kill(pid, 0)` in `src/engine/process_lock.py`; (c) **printed recipes** in `src/control/cli/promote_entry_forecast.py` (`print(f"launchctl kickstart -k gui/$(id -u)/{LAUNCHD_LABEL}")` — never executed). `src/control/heartbeat_supervisor.py` manages lease tokens only, no signal calls. |
| OOM / jetsam            | macOS killed for memory pressure                | **RULED OUT** | `jetsam memory limit (active) = unlimited`, `jetsam priority = 40` (standard daemon). No `lowSwap`/`jetsam-pressure` lines around the SIGTERM timestamps. Process RSS at sample time well under 1 GB.                                                                                                                  |
| SELF-EXIT / fatal Python| daemon crashed; launchd recorded SIGTERM        | **RULED OUT** | Daemon SIGTERM-handlers do not log "received SIGTERM" for these 3 labels; nonetheless, `tail logs/zeus-live.err`, `logs/zeus-venue-heartbeat.err`, `logs/riskguard-live.err` around each timestamp show *no* uncaught traceback, *no* `MemoryError`, *no* "shutdown" line. Last visible activity is normal HTTP/APScheduler traffic right up to the kill instant, consistent with external SIGTERM mid-loop. |
| SLEEP / WAKE throttle   | macOS suspended → launchd evicted               | **RULED OUT** | `pmset -g log` filtered to lines beginning `2026-05-17`: zero `Sleep`, `Wake`, `DarkWake`, `Display is turned` entries on event day.                                                                                                                                                                                  |
| CRON                    | a cron job restarted daemons                    | **RULED OUT** | `crontab -l`: no `launchctl kickstart` / `launchctl stop` entries. `cron/jobs.json`: no entry whose `command` references `com.zeus.live-trading|riskguard-live|venue-heartbeat`.                                                                                                                                       |
| HUMAN / AGENT-OPERATOR  | interactive `launchctl kickstart -k …`           | **PINNED** (residual-only-suspect) | All other categories eliminated. `~/.zsh_history` has zero matches for `kickstart.*com\.zeus` on 2026-05-17 — *expected*, because Claude / Copilot / VSCode agent terminals do not always persist to `~/.zsh_history`. Historical agent trajectories (e.g. `agents/venus/sessions/304d276d-…trajectory.jsonl`, 2026-04-30) record exactly this command shape (`launchctl kickstart -k gui/501/com.zeus.live-trading`) issued by a worker agent during a code-iteration loop, confirming this is an established pattern. The 6-ms co-firing at 08:25:16 (live-trading + venue-heartbeat) matches a script restarting both labels together; the subsequent 17 live-trading-only kicks match an operator/agent iterating on `src/main.py` work after pinning the venue-heartbeat config. |

## 4. Verdict

**Category: HUMAN-AGENT-OPERATOR `launchctl kickstart -k` (proximate); F86
upgraded from "signal-pinned, issuer-unknown" → "issuer-class-pinned, exact
shell unattributed".**

Confidence: HIGH that the source is an interactive `launchctl kickstart -k`
issued from a Claude / Copilot / VSCode agent terminal whose history is not
persisted to `~/.zsh_history`. Confidence MEDIUM on which specific session
issued the calls — the 5 venus session-trajectory files matching the
`kickstart…com.zeus.*` regex are all April-dated, so today's invoker is one of:
(a) a Claude/Copilot worker subagent terminal in an active PR session, (b) a
`zeus`-related skill / pipeline wrapper, (c) a hand-typed iteration. A
post-hoc attribution sweep across `~/.copilot/**/debug-logs/*.jsonl` and
`~/.claude/projects/**/*.jsonl` timed out at 60 s on the broader glob; a
targeted, mtime-bounded sweep is deferred to the antibody (§5), which removes
the need for retrospective attribution by capturing it at signal-injection
time.

**Categories explicitly ruled out**: SCHEDULED-RELOAD, MEMORY-CAP, SUPERVISOR,
OOM, SLEEP/WAKE, CRON, SELF-EXIT.

## 5. Alerting antibody (proposed, NOT IMPLEMENTED)

Two complementary surfaces, both observability-only — neither mutates plists,
daemons, or signal behavior.

### Antibody A — `launchctl_audit_wrapper.sh` (caller-attribution shim)

Add `~/.local/bin/launchctl` (PATH-prepended for interactive shells) that
shadows `/bin/launchctl`. The shim writes one JSON line per invocation to
`logs/launchctl-audit.log` **before** exec'ing the real binary:

```jsonl
{"ts":"2026-05-17T13:25:16.700Z","action":"kickstart","flags":["-k"],
 "target":"gui/501/com.zeus.live-trading",
 "caller_pid":48312,"caller_argv":"…","ppid_chain":[48312,29111,1] ,
 "session_name":"vscode-shell-7","tty":"ttys023"}
```

Implementation sketch (`logs/` is .gitignored):
```sh
#!/bin/sh
ts="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
LOG="$HOME/.openclaw/workspace-venus/zeus/logs/launchctl-audit.log"
python3 - "$@" <<PY >>"$LOG"
import json,os,sys,subprocess
ppid=os.getppid()
chain=[]
p=ppid
for _ in range(8):
    r=subprocess.run(["ps","-o","pid=,ppid=,comm=","-p",str(p)],capture_output=True,text=True)
    if not r.stdout.strip(): break
    pid,nxt,comm=r.stdout.split(None,2); chain.append({"pid":int(pid),"comm":comm.strip()})
    p=int(nxt)
    if p<=1: break
print(json.dumps({"ts":"$ts","argv":sys.argv,"ppid_chain":chain,"tty":os.environ.get("SSH_TTY",os.ttyname(0) if sys.stdin.isatty() else None),"vscode":os.environ.get("VSCODE_PID")}))
PY
exec /bin/launchctl "$@"
```
Cost: one fork + ps walk per `launchctl` call (∼5 ms). Zero impact on daemons.

### Antibody B — launchd-event tail daemon → notification

A small `tools/launchd_sigterm_watcher.py` started via a new
`com.zeus.launchd-sigterm-watcher.plist` (RunAtLoad+KeepAlive, no resource
limits, `Program = python -m tools.launchd_sigterm_watcher`). It runs:

```sh
log stream --process launchd --info --predicate \
  'eventMessage CONTAINS "exited due to SIGTERM" AND eventMessage CONTAINS "com.zeus."'
```

…parses each line, and posts to the existing alerting path
(`notifier.notify()` used elsewhere in `src/control/*`) with payload
`{label, prior_runtime_ms, sender}` plus a tail of the last 20 entries from
`logs/launchctl-audit.log` for caller-attribution context. Rate-limit: 1
notification per label per 5 minutes. Net effect: every future F86 occurrence
is self-attributed within seconds and surfaces to the operator immediately
rather than only via post-hoc audit.

### Adoption gate
Both antibodies are **OBS-only** and safe to merge independently. Implementation
not in scope of this READ-ONLY run; spec is committed for the next write-run.

## 6. Findings emitted

- **F86**: status updated from `CONFIRMED-NO-WIRE / issuer-unknown` →
  `ISSUER-CLASS-PINNED (HUMAN-AGENT kickstart)` with all 6 alternate categories
  ruled out. Carry severity unchanged (SEV-2 OBS — daemons are healthy at
  steady state; the issue is observability of restarts, not production
  failure).
- **F114** (NEW, SEV-3 OBS): launchd-kickstart caller-attribution gap — no
  audit layer captures who issued `launchctl kickstart -k`. Fixed by
  antibody A.
- **F115** (NEW, SEV-3 OBS): live-trading restart concentration 17:1 vs.
  riskguard-live/venue-heartbeat — indicates targeted operator/agent work on
  the live-trading code path; suggests pairing antibody B with a
  `restart-rate per label per hour` panel.
- **F116** (NEW, SEV-3 SEM): the 3 SIGTERM-exiting labels lack
  `signal.SIGTERM` handlers; once antibody B is in place, also add a
  graceful-shutdown handler so the daemon logs `received SIGTERM, draining …`
  before exit — this closes Run #15 T3's "exactly the 3 without handlers"
  observation as a structural concern rather than coincidence.

Note: Track B + Track F + Track G in this run also emit F106–F113. Track C's
F114–F116 sit above that range to avoid collision.

## 7. Probe (operator reproducibility)

```sh
# 1. Confirm daemons are SIGTERM-exiting:
launchctl list | grep zeus
# 2. Snapshot last-exit + jetsam:
for l in com.zeus.live-trading com.zeus.riskguard-live com.zeus.venue-heartbeat; do
  launchctl print "gui/$(id -u)/$l" | grep -E "runs|last terminating|jetsam|exit timeout"
done
# 3. Pull recent launchd SIGTERM events for these labels:
log show --process launchd --info --last 12h \
  | grep -E "com\.zeus\.(live-trading|riskguard-live|venue-heartbeat).*SIGTERM"
# 4. Rule out sleep/wake:
pmset -g log | awk '/^2026-05-17/ {print}' | grep -Ei "sleep|wake"
# 5. Search for kickstart in shell history (expected: empty when source is agent shell):
grep -E "kickstart.*com\.zeus\.(live-trading|riskguard-live|venue-heartbeat)" \
  ~/.zsh_history ~/.zsh_sessions/*/history 2>/dev/null
```

Expected: probe steps (1)–(3) match this run's table; step (4) returns
nothing; step (5) returns nothing — confirming the antibody gap (no
attribution surface) rather than a fault in the daemons.
