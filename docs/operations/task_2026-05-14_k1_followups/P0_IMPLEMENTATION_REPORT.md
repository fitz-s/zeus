# P0 Implementation Report — K1 Daily-Obs DB Redirect
# Branch: fix/k1-p0-daily-obs-redirect-2026-05-14
# Date: 2026-05-14
# Status: STOPPED — PLAN.md absent from disk; no code changes made

---

## Stop Condition

**PLAN.md for `task_2026-05-14_k1_followups` is not on disk.**

Searched exhaustively:
- `zeus-k1-p0-2026-05-14/docs/operations/task_2026-05-14_k1_followups/` — directory did not exist until this report created it
- `zeus/docs/operations/` — no k1_followups subdirectory
- `zeus-data-daemon-authority-chain-2026-05-14/docs/` — no k1_followups subdirectory
- All stashes — none contain k1_followups PLAN.md

The task brief mandates "implement per PLAN.md." Without the PLAN on disk, scope authority is absent. No code changes were committed.

---

## Empirical Pre-Flight Findings

These are verified against the MAIN workspace DBs (`zeus/state/`). The worktree
(`zeus-k1-p0-2026-05-14/`) has no `state/` directory — symlink or copy not present.

### Row counts confirming the data-loss bug (main workspace DBs):

| DB | Table | Row count | MAX target_date |
|---|---|---|---|
| `zeus-world.db` | `observations` | 127 | 2026-05-13 |
| `zeus-forecasts.db` | `observations` | 43,903 | 2026-05-10 |

Interpretation: after the K1 DB split (commit `eba80d2b9d`, 2026-05-11), daemon
`_k2_daily_obs_tick` continued writing new daily observations to `world.observations`
instead of `forecasts.observations`. Forecasts DB is stale by ~3 days.

### UNIQUE constraint confirmation (CRITIC B1 finding):

Both `world.observations` and `forecasts.observations` have:
```sql
UNIQUE(city, target_date, source)
```
Table-level constraint confirmed via `sqlite_master`. The concern in PLAN REV 1
that "no UNIQUE index exists" was incorrect; `INSERT OR IGNORE` / duplicate-skip
semantics are safe.

### SQLite ATTACH + SAVEPOINT cross-DB atomicity: CONFIRMED

Empirically verified via in-process test:
```python
import sqlite3
c = sqlite3.connect(':memory:')
c.execute("ATTACH ':memory:' AS w")
c.execute('CREATE TABLE main.observations (k TEXT)')
c.execute('CREATE TABLE w.data_coverage (k TEXT)')
c.execute('CREATE TABLE w.daily_observation_revisions (k TEXT)')
c.execute('SAVEPOINT t')
c.execute("INSERT INTO observations VALUES ('x')")   # → main
c.execute("INSERT INTO data_coverage VALUES ('y')")  # → w
c.execute("INSERT INTO daily_observation_revisions VALUES ('z')")  # → w
c.execute('RELEASE SAVEPOINT t')
# obs in main: (1,), cov in w: (1,), revisions in w: (1,)
```
MAIN-wins name resolution + SAVEPOINT atomicity spans ATTACHed databases.

---

## Identified Fix Path (NOT implemented — no PLAN authorization)

The "structural blocker" framing from earlier analysis was wrong. The fix is small:

### 1. New helper: `get_forecasts_connection_with_world()`

Pattern mirrors the existing `get_trade_connection_with_world()` in `src/state/db.py`.
Opens `zeus-forecasts.db` as MAIN, ATTACHes `zeus-world.db` as `world`.

Unqualified table name resolution under this connection:
- `observations` → MAIN (forecasts.db) ✓
- `data_coverage` → not in MAIN → falls through to `world` ✓
- `daily_observation_revisions` → same ✓
- `hko_hourly_accumulator` → same ✓

No SQL surgery required in `daily_observation_writer.py` or `data_coverage.py`.
The bare table names in `_INSERT_SQL`, `_UPSERT_SQL`, `_SELECT_EXISTING_SQL` at
`daily_observation_writer.py:66-84` continue to work correctly.

### 2. Daemon fixes (2 call sites in `src/ingest_main.py`)

**`_k2_daily_obs_tick()` at L216-230:**
Replace `get_world_connection(write_class="bulk")` → `get_forecasts_connection_with_world(write_class="bulk")`.
`daily_tick(conn)` at L230 writes `observations` (now to forecasts.db) + `data_coverage` (world.db) atomically.

**`_k2_startup_catch_up()` at L346-380:**
L354 opens `conn = get_world_connection(...)` — used for all K2 catch-ups.
Only L380 (`catch_up_obs(conn, days_back=30)`) needs to use the ATTACH connection.
The rest of the function (catch_up_hourly, catch_up_solar, catch_up_forecasts,
Phase 2 staleness probes) uses `data_coverage` and `forecasts` tables — both in
world.db — and should stay on `conn` (world connection).

Options:
- (A) Open a second `obs_conn = get_forecasts_connection_with_world(...)` just for L380.
- (B) Route the whole function through ATTACH + change the staleness probe queries.

Option A is minimal-diff. Option B is cleaner but broader. PLAN should specify which.

### 3. Migration script: `scripts/migrate_world_observations_to_forecasts.py`

Scope: copy `world.observations` rows (127 rows, target_date 2026-05-11 to 2026-05-13)
to `forecasts.observations` via `INSERT OR IGNORE`. No overlap with forecasts.observations
(MAX 2026-05-10), so duplicate-skip pattern is safe. Similarly for `market_events_v2`
(2112 rows, also affected by the same misrouting — verify in main workspace).

This slice has no daemon code touches and can be delivered without PLAN.

### 4. Test: `tests/data/test_daily_obs_routing.py`

Verify that `daily_tick` and `catch_up_missing` write to the correct DB when given
a forecasts+world ATTACH connection. No existing test covers cross-DB routing.

---

## What Was NOT Done

- No edits to `src/ingest_main.py`
- No edits to `src/state/db.py`
- No new migration script
- No new test file
- No commits

---

## Request to Orchestrator

Provide either:
1. The PLAN.md content for `task_2026-05-14_k1_followups` (specify which option for L354/L380 split), OR
2. Explicit scope authorization for the minimal-fix path described above (Option A + migration script + routing test)

The mechanics are resolved. The only missing input is scope authority.
