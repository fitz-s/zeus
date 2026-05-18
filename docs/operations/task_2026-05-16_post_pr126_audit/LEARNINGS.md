# Audit LEARNINGS — antibody catalog

Cross-run antibodies extracted from `task_2026-05-16_post_pr126_audit`. Each
entry pins a failure pattern observed during this audit, the diagnostic
heuristic that caught it, and the verification probe to re-run.

---

## §1 — `launchctl list` column 2 is LAST exit, not CURRENT state

**Origin**: Run #14 F87 (forecast-live "DOWN") → Run #16 A close (false-alarm).

### Pattern
`launchctl list | grep <label>` output:
```
PID     STATUS  LABEL
10397   1       com.zeus.forecast-live
```
A non-zero value in column 2 (here `1`) was interpreted as "daemon currently failed / exit=1". This is wrong.

### Correct semantics
Per `launchctl(1)`: column 2 is the **last exit status of the job**. Under `KeepAlive=true`, a non-zero column 2 typically means "previous incarnation exited non-zero; launchd then restarted it; the value persists as a historical record". Column 1 is the **current PID** — if non-empty, the job is currently running.

### Diagnostic rule (cross-check, never trust one signal)
A daemon is healthy iff **all three** hold:
1. `launchctl list | grep <label>` shows non-empty PID in column 1.
2. `ps -ef | grep <process-pattern> | grep -v grep` lists that same PID.
3. Daemon's last write surface (heartbeat JSON OR stderr log file) has mtime ≤ expected tick interval × 2.

If any one of the three fails, the daemon is degraded or dead — regardless of column 2.

### Verification probe
```bash
PID=$(launchctl list | awk '$3=="com.zeus.forecast-live"{print $1}')
test -n "$PID" -a "$PID" != "-" && ps -p "$PID" >/dev/null && \
  find logs/zeus-forecast-live.err -mmin -5 | grep -q . && \
  echo HEALTHY || echo DEGRADED
```

### Failure mode this prevents
"Forensic-by-status-column": staring at `launchctl list` and triaging without `ps` + log mtime. Run #14 spent SEV-1 triage cycles on a healthy daemon.

---

## §2 — Python `logging.basicConfig()` default stream is `sys.stderr`

**Origin**: Run #14 F85 → Run #16 A root cause.

### Pattern
Daemon entry point calls:
```python
logging.basicConfig(level=logging.INFO, format="...")
```
Operator expects INFO messages to appear in stdout-routed `.log` file. They never do. The `.log` file stays 0 bytes while `.err` grows to >100 MB.

### Root cause
`logging.basicConfig()` with no `stream=` argument creates a `StreamHandler()` with `stream=None`; `StreamHandler.__init__` defaults `stream=sys.stderr`. ALL log levels — INFO, WARNING, ERROR — write to stderr. launchd plist `StandardErrorPath` captures everything; `StandardOutPath` captures only direct `print()` output, of which daemons emit ~none.

### Diagnostic rule
If a daemon has a plist with distinct `StandardOutPath` and `StandardErrorPath` and operators report `.log` empty / `.err` huge, suspect Python logging default BEFORE suspecting launchd / plist misconfiguration. Plist forensics first (rule out), then `grep -nE "basicConfig|StreamHandler" <daemon-entry-points>`.

### Antibody pattern (proper daemon logging setup)
```python
# Pin INFO→stdout, WARNING+→stderr (no double-emit)
_fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
_stdout = logging.StreamHandler(sys.stdout)
_stdout.setLevel(logging.INFO)
_stdout.addFilter(lambda r: r.levelno < logging.WARNING)
_stdout.setFormatter(_fmt)
_stderr = logging.StreamHandler(sys.stderr)
_stderr.setLevel(logging.WARNING)
_stderr.setFormatter(_fmt)
root = logging.getLogger()
root.handlers.clear()
root.setLevel(logging.INFO)
root.addHandler(_stdout)
root.addHandler(_stderr)
```

### Verification probe
After restart:
```bash
# .log should grow with INFO; .err only with WARNING+
[ "$(stat -f%z logs/zeus-live.log)" -gt 0 ] && \
  ! grep -q "INFO:" logs/zeus-live.err && \
  echo SPLIT_OK || echo MISROUTED
```

### Failure mode this prevents
"Plist witch-hunt": chasing launchd configuration when the bug is one stack frame inside a 5-line `basicConfig()` call. Also prevents "fix" of routing both plist streams to one file, which destroys the signal/noise separation the plists were designed for.

---

## §3 — Audit-finding death-check requires probe-then-claim, not status-column-then-claim

**Meta-antibody** synthesizing §1 and §2.

Before opening a finding at SEV-1 ("daemon DOWN", "ingestion broken", "Karachi exposure"), every audit run must answer in evidence form, not in interpretation form:

| Claim form | Required evidence |
|---|---|
| "daemon X is down" | `ps -ef \| grep X \| grep -v grep` returns no rows, AND log mtime > 2× tick interval |
| "log file Y is stale" | `stat -f%Sm Y` and current `date` shown side-by-side |
| "code path Z is dead" | actual `pytest` or `python -c` execution OR live `tail -f log` showing the path skipped |
| "metric M not emitted" | `grep -c M log` returning 0, with log freshness shown |
| "ingestion broken" | downstream DB SELECT showing no row written in window, with rowcount + max(ts) printed |

A status-column reading without one of these is NOT evidence. Run #14 F87 violated this and a SEV-1 was opened against a healthy daemon for ≥24 hours.

### Cheap rule
Each audit finding must include a copy-pasted terminal block with the command run AND its full output. If the auditor cannot produce that block, the finding is not yet evidence; downgrade to AMBIGUOUS until it can be.
