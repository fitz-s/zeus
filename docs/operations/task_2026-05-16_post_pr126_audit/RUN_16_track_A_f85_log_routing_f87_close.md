# Run #16 Track A — F87 false-alarm close + F85 stdout/stderr root cause

- **Date**: 2026-05-17
- **Worktree**: `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill`
- **Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17`
- **Mode**: READ-ONLY production. No code, plist, or launchd state mutated.
- **Scope**: (1) formally close F87; (2) root-cause F85; (3) specify text-block fix; (4) define operator verification probe.

---

## 1. F87 — formal close (FALSE-ALARM)

### Evidence

```
$ launchctl list | grep zeus
70301   -15     com.zeus.venue-heartbeat
-       0       com.zeus.calibration-transfer-eval
80628   -15     com.zeus.live-trading
10397   1       com.zeus.forecast-live          ← misread as "DOWN" in Run #14
54734   -15     com.zeus.riskguard-live
34316   0       com.zeus.data-ingest
-       0       com.zeus.heartbeat-sensor

$ ps -ef | grep forecast_live_daemon | grep -v grep
  501 10397     1   0 Sat12PM ??         1:30.55 …/.venv/bin/python -m src.ingest.forecast_live_daemon

$ ls -la logs/zeus-forecast-live.err
-rw-r--r--@ 1 leofitz  staff  3142523 May 17 18:59 logs/zeus-forecast-live.err
```

- PID `10397` matches between `launchctl list` (column 1) and `ps -ef`.
- `.err` mtime = 2026-05-17 18:59 (fresh, single-minute lag from sample time).
- Heartbeat JSON `state/heartbeats/zeus-forecast-live.json` last write ≤ 18:40:50Z (per Run #15 T3 evidence) — actively running.

### Root cause of the misread

Run #14 Track D interpreted `launchctl list` column 2 (value `1`) as **current daemon state**. Per `launchctl(1)`:

> The second column is the **last exit status** of the job.

It is the exit code of the **previous incarnation** that launchd's `KeepAlive` then restarted. For `com.zeus.forecast-live`, the prior process exited 1, launchd respawned (PID 10397, current = healthy). For `com.zeus.live-trading` / `riskguard-live` / `venue-heartbeat` the `-15` likewise records the prior SIGTERM exit (F86), not current state — PIDs in column 1 are non-empty, so each daemon is up.

### Verdict
**F87 → CLOSED-FALSE-ALARM (Run #16 A).** No daemon restart needed.

### Antibody (see §5 + LEARNINGS.md)
Cross-check `launchctl list` column 2 against `(column 1 is non-empty PID) AND (ps -p <PID> exists) AND (log/heartbeat mtime fresh)`. Never derive liveness from column 2 alone.

---

## 2. F85 — root cause: `logging.basicConfig()` default stream is `sys.stderr`

### Symptom matrix

```
$ ls -la logs/zeus-*.log logs/zeus-*.err logs/riskguard-*.log logs/riskguard-*.err
-rw-r--r--@ 1 leofitz  staff   3142523 May 17 18:59 zeus-forecast-live.err
-rw-r--r--@ 1 leofitz  staff         0 May 15 03:44 zeus-forecast-live.log   ← 0 bytes, stale 2d
-rw-r--r--  1 leofitz  staff 118250030 May 17 18:59 zeus-ingest.err          ← 118 MB
-rw-r--r--  1 leofitz  staff       860 May 14 03:19 zeus-ingest.log          ← 860 B, stale 3d
-rw-r--r--  1 leofitz  staff 119171612 May 17 18:59 zeus-live.err            ← 119 MB
-rw-r--r--  1 leofitz  staff         0 May 15 09:04 zeus-live.log            ← 0 bytes, stale 2d
-rw-r--r--  1 leofitz  staff  11547387 May 17 18:58 riskguard-live.err
-rw-r--r--  1 leofitz  staff         0 May 15 15:26 riskguard-live.log       ← 0 bytes, stale 2d
-rw-r--r--  1 leofitz  staff      4792 May 17 18:48 zeus-venue-heartbeat.err
-rw-r--r--  1 leofitz  staff         0 May 17 06:18 zeus-venue-heartbeat.log ← 0 bytes
```

100 % of fresh log volume lands in `.err`; every `.log` file is either 0 bytes or multi-day stale.

### Plist layer is CORRECT — ruled out

Every `~/Library/LaunchAgents/com.zeus.*.plist` wires distinct `.log` (StandardOutPath) and `.err` (StandardErrorPath) paths:

| Plist | StandardOutPath | StandardErrorPath |
|---|---|---|
| `com.zeus.forecast-live` | `logs/zeus-forecast-live.log` | `logs/zeus-forecast-live.err` |
| `com.zeus.data-ingest` | `logs/zeus-ingest.log` | `logs/zeus-ingest.err` |
| `com.zeus.live-trading` | `logs/zeus-live.log` | `logs/zeus-live.err` |
| `com.zeus.riskguard-live` | `logs/riskguard-live.log` | `logs/riskguard-live.err` |
| `com.zeus.venue-heartbeat` | `logs/zeus-venue-heartbeat.log` | `logs/zeus-venue-heartbeat.err` |
| `com.zeus.heartbeat-sensor` | `logs/heartbeat-sensor.log` | `logs/heartbeat-sensor.err` |
| `com.zeus.calibration-transfer-eval` | `logs/calibration-transfer-eval.log` | `logs/calibration-transfer-eval.err` |

7/7 distinct. None routed to `/dev/null`. Plists are NOT the misroute.

### Real root cause — daemon entry-point logging config

```
$ grep -nE "basicConfig" src/main.py src/ingest_main.py \
    src/ingest/forecast_live_daemon.py src/riskguard/riskguard.py
src/main.py:1332:    logging.basicConfig(
src/ingest_main.py:1035:    logging.basicConfig(
src/ingest/forecast_live_daemon.py:664:    logging.basicConfig(
src/riskguard/riskguard.py:1446:    logging.basicConfig(level=logging.INFO)
```

Three of four call sites pass only `level=logging.INFO` and a `format=` string; the fourth passes only `level=logging.INFO`. None pass `stream=` and none pass `handlers=`.

Per CPython `logging` source (`Lib/logging/__init__.py`, `basicConfig` → `StreamHandler.__init__`):

```python
class StreamHandler(Handler):
    def __init__(self, stream=None):
        ...
        if stream is None:
            stream = sys.stderr   # ← default
        self.stream = stream
```

**Therefore every `logger.info(...)` / `logger.warning(...)` / `logger.error(...)` in every Zeus daemon writes to `sys.stderr`**, which launchd captures into `.err`. The `.log` (stdout) file only fills when code calls `print()` — which the daemons effectively never do during steady-state operation.

### Verdict
F85 root cause = **Python default StreamHandler stream = `sys.stderr`**. NOT apscheduler. NOT plist. NOT launchd. The plists are correctly bifurcated; the daemons just don't use stdout.

---

## 3. Fix specification (text-block patch — NOT applied)

### Patch site: every daemon `basicConfig` call

Replace single-handler default with explicit dual-handler routing.

**Before** (representative — `src/ingest/forecast_live_daemon.py:664`):
```python
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
```

**After**:
```python
    _fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setLevel(logging.INFO)
    _stdout.setFormatter(_fmt)
    _stdout.addFilter(lambda r: r.levelno < logging.WARNING)  # INFO/DEBUG only

    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setLevel(logging.WARNING)                          # WARNING+ only
    _stderr.setFormatter(_fmt)

    _root = logging.getLogger()
    _root.handlers.clear()
    _root.setLevel(logging.INFO)
    _root.addHandler(_stdout)
    _root.addHandler(_stderr)
```

Apply identically (with daemon-specific format string preserved) to:
- `src/main.py:1332`
- `src/ingest_main.py:1035`
- `src/ingest/forecast_live_daemon.py:664`
- `src/riskguard/riskguard.py:1446`

`src/control/heartbeat_supervisor.py` has no `basicConfig`; it inherits root logger config from whichever module wires it. Confirm at integration time it acquires the dual-handler root after the patch (logging hierarchy will propagate).

### Why filter (not just two StreamHandlers without filter)

Without the `lambda r: r.levelno < logging.WARNING` filter on the stdout handler, every WARNING/ERROR would appear in BOTH `.log` and `.err`. Dedup at handler level keeps `.log` = INFO-only (operator's signal-of-life feed) and `.err` = WARNING+ only (operator's exception feed).

### Plist hygiene (no change required)

All 7 plists already split `.log` / `.err`. No mutation needed. Leave alone.

### Non-fix path also considered (rejected)

Routing both streams to a single file via plist `StandardOutPath=StandardErrorPath` was considered — it would "fix" the missing-stdout symptom but destroys the WARNING/ERROR signal-noise separation that 119 MB `.err` files already make valuable. Rejected.

---

## 4. Karachi 5/17 ops impact

**Low.** F85 is observability-only: production daemons are functionally healthy (per F87 close + per Run #15 T3 evidence). Risk is operator misreading: anyone running `tail -f logs/zeus-*.log` sees stale-empty file and assumes daemon dead — exactly the failure mode that produced F87. Fix sequencing: deploy F85 fix in same PR as F86 SIGTERM-handler patch; both touch daemon main entry points.

---

## 5. Post-fix verification probe (operator runbook)

After applying §3 patch and `launchctl kickstart -k gui/$UID/com.zeus.<daemon>`:

```bash
# 1. Confirm process is up under new PID
ps -ef | grep -E "src\.(main|ingest_main|ingest\.forecast_live_daemon|riskguard\.riskguard)" | grep -v grep

# 2. Confirm .log is now being written
for f in logs/zeus-live.log logs/zeus-ingest.log logs/zeus-forecast-live.log logs/riskguard-live.log; do
  printf "%-40s " "$f"
  stat -f "size=%-10z mtime=%Sm" -t "%Y-%m-%d %H:%M:%S" "$f"
done
# Expected: each .log file mtime within last minute, size > 0

# 3. Confirm .log contains only INFO (no WARNING/ERROR)
for f in logs/zeus-live.log logs/zeus-ingest.log logs/zeus-forecast-live.log logs/riskguard-live.log; do
  echo "==$f=="
  awk '{print $4}' "$f" | sort -u | head
done
# Expected: only "INFO:" appears

# 4. Confirm .err contains only WARNING+ (no INFO floods)
tail -20 logs/zeus-ingest.err | awk '{print $4}' | sort -u
# Expected: WARNING:, ERROR:, CRITICAL: only — no INFO:

# 5. Confirm no double-logging (a unique INFO line should NOT appear in .err)
grep -c "Zeus starting in live mode" logs/zeus-live.err  # expected: 0 after restart
grep -c "Zeus starting in live mode" logs/zeus-live.log  # expected: ≥1 after restart
```

If step 2 still shows 0-byte `.log` files: patch did not deploy; re-check that the `_root.handlers.clear()` line ran before any `logger.info()` (must be before `bypass_dead_proxy_env_vars()` which itself logs).

If step 4 shows `INFO:` in `.err`: handler filter is missing or stdout handler was attached after `setLevel`; revisit §3.

---

## 6. Findings index delta

- **F87**: SEV-1 HOT → **CLOSED-FALSE-ALARM (Run #16 A)**. Forecast-live daemon healthy under PID 10397. Misdiagnosis traced to `launchctl list` column 2 semantics. New antibody added (LEARNINGS.md §1).
- **F85**: SEV-2 NEW (Run #14) → **ROOT-CAUSE-PINNED + FIX-SPECIFIED (Run #16 A)**. Plist layer ruled out (7/7 correctly bifurcated). Cause = `basicConfig()` default `StreamHandler(sys.stderr)`. Dual-handler patch spec'd in §3; verification probe in §5. No production code mutated this run.

No new findings (F-numbers) opened by this run.
