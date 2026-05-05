# T2G: Database-Locked Degrade Runbook

<!--
# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: T2G phase.json + AMD-T2-2 + invariants.jsonl T2G close + SEC-MEDIUM-1+2
-->

> **Runbook class**: Durable Operator Runbook
> **Behavior-change commit**: ee94539f (T2G)
> **Amendment**: AMD-T2-2 (operator-visible behavior change disclosure)
> **Upstream invariant**: T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH
> **Doctrine source**: T2G `phase.json` `_planner_notes.operator_awareness`

---

## (a) Pre-T2G vs Post-T2G Failure Semantics

**This section mirrors AMD-T2-2 operator_awareness verbatim.**

### Pre-T2G behavior

A transient `sqlite3.OperationalError('database is locked')` at the cycle's
trade-DB connection acquisition (`src/engine/cycle_runner.py`, the
`get_connection()` call inside `cycle.run()`) propagated **uncaught** through
`cycle.run()` and **crashed the live daemon** — or surfaced up to launchd's
restart loop, producing a tight respawn window during contention with a
concurrent `rebuild_calibration_pairs_v2.py` or other writer.

**Observable symptoms (pre-T2G)**:
- Daemon crash / launchd respawn counter increments
- `cycle_runner.log` — unhandled `OperationalError` traceback
- launchd respawn frequency spikes during calibration rebuild windows

### Post-T2G behavior (commit ee94539f)

`connect_or_degrade` catches the lock-class `OperationalError`, returns `None`.
The cycle **gracefully degrades** to read-only for that single iteration: no
writes, no SDK contact, no settlement command emission. The next cycle proceeds
normally.

`src.observability.counters.increment('db_write_lock_timeout_total', ...)` records
each occurrence (read-back via T2F typed sink).

Any non-lock `OperationalError` still propagates as before (T2G-CYCLE-RUNNER-PROPAGATES-NON-LOCK).

**This is the intentional realization of T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH at
the live-cycle level** (T1E shipped the primitive only; T2G wires it into the
daemon cycle path).

**Observable symptoms (post-T2G)**:
- Daemon does NOT crash; launchd respawn counts do NOT increment on lock events
- `db_write_lock_timeout_total` counter increments (primary signal)
- `cycle_runner.log` — `ALERT` log line emitted alongside the counter increment
- Cycle summary reflects degrade (no-trade iteration), resumes normally next tick

---

## (b) Oncall Alert-Rule Migration

### Why the migration is required (SEC-MEDIUM-1)

Pre-T2G, the daemon crashed on `database is locked`. Oncall's alert was wired
to launchd respawn count spikes as a proxy for this failure mode.

**Post-T2G, launchd respawn counts will drop to zero even when the cycle is
repeatedly degrading** — the daemon no longer crashes on lock. An oncall team
relying on launchd respawn counts as the primary signal for DB-lock events will
receive NO alert during repeated degradation windows. This is a silent
observability regression unless the alert rule is migrated.

### Migrating from launchd-respawn to typed counter

**Old primary signal (retire)**:
```
launchd respawn count for com.zeus.cycle_runner
```

**New primary signal**:
```python
from src.observability.counters import read as counters_read
count = counters_read('db_write_lock_timeout_total')
```

**New secondary / fallback signal** (log-grep, in case process restarts reset
in-process counter):
```bash
grep 'ALERT' cycle_runner.log | grep 'db_write_lock_timeout_total'
```

### Alert config note

A monitoring alert config at `monitoring/alerts.yaml` is the future home for
this rule (T2E scope). Until that file lands, alert logic must be expressed in
your external monitoring system querying the typed counter sink or the ALERT log
line pattern above.

---

## (c) Diagnostic Checks

When `db_write_lock_timeout_total` is non-zero or ALERT lines appear in
`cycle_runner.log`, work through these steps in order:

### Step 1: Identify the contending writer

Lock contention at the trade DB most commonly originates from:

1. **`rebuild_calibration_pairs_v2.py`** — runs a long write transaction
   per city. Check if a rebuild job is running or recently ran:
   ```bash
   pgrep -af rebuild_calibration_pairs_v2
   ```
2. **`riskguard` write activity** — riskguard has its own write path to the
   DB. Check riskguard log for concurrent write windows:
   ```bash
   grep -i 'write\|lock\|error' riskguard.log | tail -30
   ```
3. **Any other sqlite writer** — check `lsof` for open handles on the trade DB:
   ```bash
   lsof | grep zeus_trade.db
   ```

### Step 2: Inspect cycle_runner.log for ALERT lines

```bash
grep 'ALERT\|db_write_lock_timeout_total\|database is locked' cycle_runner.log | tail -50
```

Look for:
- Frequency of degrade events (how many per hour?)
- Whether degradation is isolated (single event) or sustained (repeated)
- Whether the pattern correlates with a rebuild window

### Step 3: Read the typed counter

```python
from src.observability.counters import read as counters_read
print(counters_read('db_write_lock_timeout_total'))
```

Note: the typed counter is **in-process only** (no persistence to disk per T2
scope). Counter resets on daemon restart. Use the log ALERT lines as the
durable signal across restarts.

### Step 4: Classify the event

| Pattern | Classification | Action |
|---------|---------------|--------|
| 1-2 events, correlates with rebuild run | Transient / expected | Monitor; no action if self-resolving |
| Repeated events, no rebuild in progress | Investigate | Identify unexpected writer (Step 1) |
| Sustained / high-rate events | Escalate | See escalation thresholds below |
| Non-lock OperationalError propagates + daemon crashes | Non-lock error | Different code path; check traceback |

---

## (d) Escalation Thresholds

**Operator calibration required.** The thresholds below are intentionally
left as placeholders. Per AMD-T2H-1, numeric bounds must be set by the operator
after the first 7-day production window using `db_write_lock_timeout_total`
telemetry. Do NOT treat these as fixed values until calibrated.

| Condition | Counter / log threshold | Action |
|-----------|------------------------|--------|
| Transient — within expected noise | `db_write_lock_timeout_total` <= `<PLACEHOLDER pending first-7-day production calibration>` per hour | Log and continue; no page |
| Investigate — elevated, possible contention issue | `db_write_lock_timeout_total` > `<PLACEHOLDER pending first-7-day production calibration>` per hour, sustained over `<PLACEHOLDER pending first-7-day production calibration>` minutes | Investigate Step 1-3 above; notify on-call channel |
| Page operator — high-rate or sustained degradation | `db_write_lock_timeout_total` > `<PLACEHOLDER pending first-7-day production calibration>` per hour OR any single window exceeds `<PLACEHOLDER pending first-7-day production calibration>` consecutive degraded cycles | Page on-call; consider pausing rebuild jobs; inspect for unexpected writers |

**To calibrate after the first 7-day production window**:
1. Collect `db_write_lock_timeout_total` increments from `cycle_runner.log` ALERT lines
2. Identify P50, P95, P99 hourly rates during normal operation (with and without rebuild runs)
3. Set the "transient" threshold at P95 + margin; set the "investigate" threshold at 2× that; set the "page" threshold at the absolute maximum observed plus headroom
4. Update this table and commit with authority basis citing the calibration run date

---

## Authority Back-Trace

| Item | Reference |
|------|-----------|
| Behavior-change commit | `ee94539f` (T2G) |
| Amendment ID | `AMD-T2-2` |
| Durable doctrine source | T2G `phase.json` `_planner_notes.operator_awareness` |
| Upstream primitive invariant | `T1E-LOCK-TIMEOUT-DEGRADE-NOT-CRASH` |
| Live-cycle wiring invariant | `T2G-CYCLE-RUNNER-DEGRADES-NOT-CRASHES` |
| Counter emit invariant | `T2G-CONNECT-OR-DEGRADE-COUNTER-WIRED` |
| Typed counter sink invariant | `T2F-EVERY-T1-COUNTER-EMITS-VIA-SINK` |
| Alert-rule migration trigger | `SEC-MEDIUM-1` (T2G security reviewer finding) |
| Operator-awareness disclosure trigger | `SEC-MEDIUM-2` (T2G security reviewer finding) |
| Oncall alert config future home | `monitoring/alerts.yaml` (T2E scope; not yet created) |
