# Lock-Storm Regression Archaeology — 2026-06-13

**Investigation type:** READ-ONLY log/code archaeology  
**Evidence window:** Jun 8 14:xx CDT through Jun 12 19:10 CDT (log EOF)  
**Plan source:** `docs/evidence/plans/2026-06-13_fill_bridge_retry_storm.md`

---

## 1. TIMELINE — Hourly "database is locked" error counts

Counts are CDT (UTC−5). Each row is one calendar hour.

| Date | Hour (CDT) | Count | Event |
|------|-----------|-------|-------|
| Jun 08 | 14–23 | 200–400/hr | Pre-existing storm (connection held across network I/O, pre-9f70e9c581) |
| Jun 09 | 00–16 | 200–400/hr | Same root, still present |
| Jun 09 | 17 | 1 | **9f70e9c581 deployed** (three-phase venue-sync contract; never hold conn across network I/O) |
| Jun 10 | various | 20–80/hr | Residual; manageable |
| Jun 11 | various | 50–137/hr | Elevated; no single-hour spike |
| Jun 12 | 09 | **163** | **Primary storm onset** — 347f713d deployed 08:43 CDT |
| Jun 12 | 10 | **185** | Storm sustained |
| Jun 12 | 11–18 | 10–60/hr | Moderate |
| Jun 12 | 18:17 CDT (23:17Z) | ramping | 66f873da deployed 10:17 CDT; fill-bridge loops accumulate |
| Jun 12 | ~18:32 CDT (23:32Z) | **423 in 7 min** | **Secondary storm peak** per plan doc |
| Jun 12 | 18:55 CDT (23:55Z) | 1 | **b35decbe44 deployed** (two fill-bridge loops fixed) |
| Jun 12 | 19:10 CDT | EOF | Log file ends |

**Storm onset (primary): 09:38 CDT Jun 12** — first batch of day0_fast_obs DAY0_FAST_OBS_CITY_FAILED errors with "database is locked"  
**Storm peak (primary): 09:58 CDT** — 47 cities all fire within 1 second (Amsterdam through Zhengzhou), 52 locked errors in that minute alone  
**Storm onset (secondary): accumulates from ~15:17 UTC (10:17 CDT)**, reaches 423-error burst by 23:32Z (18:32 CDT)

---

## 2. ATTRIBUTION — Module breakdown and DB target

### Primary storm (09:38–10:30 CDT, regressing commit 347f713d)

| Module / log label | Errors (09–10 CDT) | Target DB |
|--------------------|-------------------|-----------|
| `DAY0_FAST_OBS_CITY_FAILED` (day0_fast_obs.py) | ~143 | zeus-world.db |
| `market_scanner` (market_scanner.py) | ~80 | zeus-world.db / zeus_trades.db |
| `zeus` / reactor path (event_reactor_adapter.py) | ~60 | zeus-world.db |

All errors hit **zeus-world.db**. zeus_trades.db has zeus-world.db ATTACHed on the composite write connection, so locking propagates when the write lock is held.

### Secondary storm (post-10:17 CDT, commits 66f873da → fixed by b35decbe44)

| Module | Errors | Target DB |
|--------|--------|-----------|
| `EDLI durable fill-bridge: failed` (edli_trade_fact_bridge.py) | ~380/7min | zeus-world.db |
| `fill-bridge: could not update failure count` | ~43/7min | zeus-world.db |

---

## 3. REGRESSION COMMITS

### A. Primary regression: `347f713d` (08:43 CDT Jun 12)

**Commit message:** "no-caps wave: delete artificial gates; day0 correctness organs; sigma-scale seam"

**Mechanism:**  
Introduced `_recover_kill_memo_from_events()` in `src/data/day0_fast_obs.py`. This function is called from `execute_monitoring_phase()` → `evaluate_hard_fact_exit()` → `latest_rounded_extreme()` when the in-process kill memo is empty (i.e., after every daemon restart).

```python
def _recover_kill_memo_from_events(*, city_name, target_date, metric, world_conn=None):
    own_conn = False
    conn = world_conn
    try:
        if conn is None:
            from src.state.db import get_world_connection_read_only
            conn = get_world_connection_read_only()  # ← opens NEW separate RO conn
            own_conn = True
        row = conn.execute(sql, (city_name, target_date, metric)).fetchone()
        ...
    finally:
        if own_conn and conn is not None:
            conn.close()
```

After each restart, the in-process memo is empty for ALL cities. The monitoring phase calls `_recover_kill_memo_from_events()` per (city, date, metric) with `world_conn=None`, triggering `get_world_connection_read_only()` per city. At **09:58:09 CDT**, all 47 monitored cities fired within ~1 second (Amsterdam through Zhengzhou in log sequence), creating 47 simultaneous read-only connections to zeus-world.db at the exact moment the EDLI reactor held the zeus-world.db write lock.

SQLite WAL mode: readers do not block writers, but when the composite connection (`get_connection()` in `cycle_runner.py`) holds the ATTACHED zeus-world.db in a write transaction, concurrent `file:...?mode=ro` connections attempting any operation get `SQLITE_BUSY`. 47 concurrent new connections × reactive city burst = 52 locked errors in that minute.

The burst repeats every ~2 minutes (reactor cycle cadence): 09:58 (52 errors), 10:00 (51 errors), 10:12 (43 errors). Pattern is periodic because each cycle restarts the monitoring phase for all cities.

**Propagation path:**  
`cycle_runner.get_connection()` → ATTACH zeus-world.db → holds composite write conn → `execute_monitoring_phase()` → `evaluate_hard_fact_exit()` → `latest_rounded_extreme()` → `_recover_kill_memo_from_events(world_conn=None)` → `get_world_connection_read_only()` × 47 cities simultaneously

### B. Secondary regression: `66f873da` (10:17 CDT Jun 12)

**Commit message:** "fill-bridge: settled-market terminal routing + bounded-retry quarantine"

**Mechanism 1 — NOT NULL schema drift:**  
Introduced `edli_fill_bridge_dispositions` table with `disposition TEXT NOT NULL` constraint. The live database had this table from a prior boot with the same constraint. `ensure_table()` in Zeus schema management calls `CREATE TABLE IF NOT EXISTS` which does nothing when the table already exists — the constraint cannot be relaxed by re-running `ensure_table`. The new code attempted to INSERT rows with NULL disposition (intermediate accumulating state). Result: every INSERT to `edli_fill_bridge_dispositions` raised `NOT NULL constraint failed`. The `_increment_failure_count()` helper writes to this table on each retry; failure there returned `attempt_count=1` forever. Since the quarantine threshold is 10, quarantine was structurally unreachable. Every fill-bridge scan retried indefinitely.

**Mechanism 2 — orphan bridge re-selection:**  
The candidate query for pending bridge aggregates did not filter out `status='RECONCILED'` (terminal). Terminal-RECONCILED aggregates were re-selected every scan, re-attempted, re-failed the NOT NULL insert, and re-queued — amplifying write contention proportionally to the number of accumulated orphan bridges.

Both loops composed: each reactor cycle triggered N fill-bridge retries × M orphan bridges × failed `INSERT INTO edli_fill_bridge_dispositions`. Each failure generated a locked error on zeus-world.db. Over 7 minutes post-boot this accumulated to 423 locked errors at 23:32–23:39Z (18:32–18:39 CDT).

**Fixes deployed (b35decbe44 at 18:55 CDT = 23:55Z):**
- `cd8ce11997`: Fill-bridge infinite retry loop fixed — attempt counter correctly incremented; quarantine at threshold 10 reachable
- `b35decbe44`: Orphan bridge re-selection blocked — terminal-RECONCILED aggregates excluded from candidate query

Post-fix evidence: 1 locked error total in remaining log window (18:55–19:10 CDT).

---

## 4. RESIDUAL RISKS

### A. Post-fix locked rate: RESOLVED

After b35decbe44 boot at 18:55 CDT: 1 locked error in 15-minute log window. The two write-amplifier loops that drove the 423-error burst are confirmed eliminated.

The day0_fast_obs burst (primary regression, 347f713d) was not independently fixed in the observed log window. The burst is memo-warm-after-first-cycle, so after the first reactor pass all 47 cities populate their in-process memo and subsequent cycles do not call `get_world_connection_read_only()`. Risk: any daemon restart re-exposes the burst. Status as of log EOF: not explicitly patched (no commit visible for day0_fast_obs warm-on-start or world_conn threading).

### B. market_scanner "0 rows inserted out of 95 events"

**NOT a defect.** `market_events` uses `INSERT OR IGNORE` with a `UNIQUE` constraint on `condition_id`. When all 95 events already exist in the table (normal steady-state after first boot), every INSERT is a silent duplicate ignore (SQLite `rowcount=0`). The warning fires whenever `inserted==0 AND len(results)>0`. The "possible constraint storm" log wording is misleading — it means "all rows were duplicates, table already current." No locked errors are generated. No data loss. The label `WARNING` and the phrase "possible constraint storm" create false alarm noise but represent zero functional risk. Log example: `market_scanner: 0 rows inserted out of 95 events — possible constraint storm`.

### C. WAL BUSY warning and long-lived reader identity

The `_world_wal_checkpoint_cycle()` job (every 90s, `2026-06-04 part 2`) logs BUSY when `PRAGMA wal_checkpoint(TRUNCATE)` returns `busy=1`. Evidence at 09:15 CDT: `log_frames=33063, checkpointed=4` — WAL grew to ~33k frames with only 4 frames checkpointed, confirming a reader pinned the WAL floor all morning.

**Part-1 (a1a620622e, `fix(world-db): kill WAL checkpoint-starvation`):** The original long-lived reader was `event_reactor_adapter._edli_forecast_sharpness_evidence`, which opened a zeus-world.db read connection per-event in the reactor hot path and never closed it. Each call leaked a connection, pinning the WAL floor until GC. Fix: close in `finally`. Deployed pre-Jun-12.

**Residual BUSY on Jun 12:** The 33k-frame WAL at 09:15 CDT predates both Jun-12 regressions. The BUSY warnings on Jun 12 indicate the part-1 fix regressed or a new long-lived reader was introduced by the Jun-12 commits. Suspects:

1. `cycle_runner.get_connection()` returns zeus_trades.db with zeus-world.db ATTACHed. If this composite connection is held open between reactor passes (not closed and reopened each cycle), it pins a WAL read-mark on zeus-world.db across cycles. Per 9f70e9c581 law, connections must not be held across network I/O; whether they are held across the full inter-cycle gap is not confirmed from logs alone.

2. `_recover_kill_memo_from_events()` connections from 347f713d: these ARE properly closed in `finally` (code verified), so they are not WAL pinners.

3. The `_edli_redecision_screen` opens `world_ro` and `world_ro2` (lines 6308, 6337 of main.py) both closed in `finally` blocks — not pinners.

**Bottom line:** The WAL BUSY on Jun 12 is a pre-existing condition (33k frames at 09:15 CDT = before 347f713d even deployed at 08:43 CDT). The Jun-12 regressions did not introduce the WAL pinner; they introduced write-amplification that converted the existing WAL tension into hard SQLITE_BUSY errors. The WAL floor pinner identity requires tracing which connection survives between the part-2 checkpoint job's 90s intervals without releasing its read-mark — most likely the composite `get_connection()` held across the full reactor inter-cycle gap, but this requires live `lsof`/`PRAGMA wal_autocheckpoint` probing to confirm definitively.

---

## 5. ANTIBODY PROPOSAL

**Regression class:** "New code opens N per-city short-lived world-DB connections simultaneously inside a code path already holding the composite world-write connection, causing SQLITE_BUSY × N."

**Root structural failure:** No test asserts that the monitoring phase (or any path called from a composite write-conn context) does not open additional independent connections to zeus-world.db. The path `execute_monitoring_phase → evaluate_hard_fact_exit → latest_rounded_extreme → _recover_kill_memo_from_events(world_conn=None)` compiles, runs, and passes all existing function tests with world_conn=None — only the runtime interaction with a concurrent write lock surfaces the error.

**Antibody design (relationship test, not a function test):**

```
test_monitoring_phase_does_not_open_secondary_world_connections()

Invariant: Within any code path that receives a composite write connection
(zeus_trades + zeus-world ATTACHed), NO sub-call may open an independent
connection to zeus-world.db via get_world_connection_read_only() or
sqlite3.connect(ZEUS_WORLD_DB_PATH, ...).

Implementation:
1. Mock/patch get_world_connection_read_only and sqlite3.connect.
2. Construct an in-memory composite conn (trades DB with world ATTACHed).
3. Call execute_monitoring_phase(conn, ...) with enough fake state to
   traverse the evaluate_hard_fact_exit → latest_rounded_extreme branch.
4. Assert: get_world_connection_read_only was called 0 times.
   Assert: sqlite3.connect was not called with any path containing "world".

Category made unconstructable: Any future change that adds a
"get_world_connection_read_only() if world_conn is None" fallback inside
the monitoring phase immediately fails this test, preventing the commit from
landing without explicit acknowledgment.

Companion structural fix: Pass `world_conn` through the call chain:
execute_monitoring_phase(conn, world_conn=conn) → evaluate_hard_fact_exit
receives world_conn → latest_rounded_extreme receives world_conn →
_recover_kill_memo_from_events(world_conn=world_conn) never opens a
secondary connection. The test would then assert the mock was called 0 times
regardless of memo state (cold start or warm).
```

This test catches the regression class at PR review time (RED on any PR that introduces a `get_world_connection_read_only()` call inside a monitoring/exit path without threading world_conn), making the category unconstructable without test modification.

---

## Evidence summary

| Item | Value |
|------|-------|
| Primary regression commit | `347f713d` — `_recover_kill_memo_from_events` opens N per-city RO world conns |
| Secondary regression commit | `66f873da` — fill-bridge NOT NULL schema drift + orphan re-selection loops |
| Primary storm onset | 09:38 CDT Jun 12; peak 09:58 CDT (47 cities, 52 errors/min) |
| Secondary storm peak | 23:32–23:39Z Jun 12 (18:32–18:39 CDT), 423 errors in 7 min |
| Post-fix locked rate | 1 error / 15-min window after b35decbe44 at 23:55Z |
| WAL BUSY identity | Pre-existing (33k frames at 09:15 CDT); likely composite conn held between cycles; not introduced by Jun-12 commits |
| market_scanner 0 rows | Benign deduplication; INSERT OR IGNORE on all-duplicate batch; no data loss |
| Antibody | Relationship test: monitoring phase must not open secondary world connections when composite conn is present |
