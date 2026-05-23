# Run #16 — Track B: F102 `temp_persistence` ownership investigation + F48 L1065 fix

## 1. Metadata

| Field | Value |
|---|---|
| Run | #16 — Track B |
| Date | 2026-05-17 |
| Branch | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `d9094b1be8` |
| Worktree | `/Users/leofitz/.openclaw/workspace-venus/zeus/.claude/worktrees/zeus-deep-alignment-audit-skill` |
| Mandate | F102 (SEV-2 HOT, Run #15 T2): `temp_persistence` table empty in trades (0) / world (0) / missing in forecasts. Determine ownership: NEVER-WIRED, RETIRED, MIGRATED, or OPERATOR-OWNED. 1-line fix for F48 L1065. |
| Production code | **NOT MODIFIED** (read-only audit). |

---

## 2. Schema discovery — where does `temp_persistence` live?

| DB | Schema present? | Row count |
|---|---|---|
| `state/zeus_trades.db` (MAIN under cycle conn) | ✓ | **0** |
| `state/zeus-world.db` (ATTACH `world`) | ✓ | **0** |
| `state/zeus-forecasts.db` (ATTACH `forecasts`) | ✗ (not created here) | n/a |
| `state/zeus.db` (legacy/empty) | ✗ | n/a |

Identical DDL in both DBs (created by `init_schema(conn)` in `src/state/db.py:770`):

```sql
CREATE TABLE IF NOT EXISTS temp_persistence (
    city TEXT NOT NULL,
    season TEXT NOT NULL,
    delta_bucket TEXT NOT NULL,
    frequency REAL NOT NULL,
    avg_next_day_reversion REAL,
    n_samples INTEGER NOT NULL,
    UNIQUE(city, season, delta_bucket)
);
```

Both copies exist because `init_schema` is idempotently invoked on every conn opened via `get_*_connection()` — trade conn and world conn both get the full DDL block. Trade-DB copy is structurally dead (nothing writes to it); world-DB copy is the intended writer target.

---

## 3. Writer search — who is supposed to populate it?

Single writer found in repo:

```
scripts/etl_temp_persistence.py:142   INSERT OR REPLACE INTO temp_persistence ...
scripts/etl_temp_persistence.py:59    DELETE FROM temp_persistence  (full reset)
```

Header docstring (lines 1-12):

> **Source**: `zeus.db:observations` (daily, already imported via legacy-predecessor migration)
> **Target**: `zeus.db:temp_persistence`
> Computes day-over-day temperature change distribution per city×season.
> ZEUS_SPEC §14.4 ETL 7.

Conn binding (line 31):

```python
from src.state.db import get_world_connection as get_connection, init_schema
```

→ writer reads `observations` AND writes `temp_persistence` BOTH against `zeus-world.db`. Docstring "zeus.db" is stale terminology; effective target is the world DB.

---

## 4. Scheduling — is the writer actually called?

YES. Two independent schedulers wire it:

| Site | Scheduler | Hour | Lock | Last successful run |
|---|---|---|---|---|
| `src/main.py:413` (`scheduler.add_job(_etl_recalibrate, "cron", hour=6, minute=0, id="etl_recalibrate")`) | APScheduler in live trading daemon | 06:00 daily | n/a (live daemon) | inferred (daemon stop/start logs not parsed this run) |
| `src/ingest_main.py:580` (`_etl_recalibrate` → `_etl_recalibrate_body`) | APScheduler in ingest daemon | 06:00 daily | `acquire_lock("etl_recalibrate")` | **2026-05-17 06:00:29** |
| `scripts/onboard_cities.py:242-244` | one-shot bootstrap (manual) | on city onboarding | n/a | not run since fresh boot |

Most recent 30 days of `logs/zeus-ingest.err` (operator output captures `_etl_recalibrate_body` result dict):

```
2026-05-02 06:01  etl_temp_persistence.py: OK
2026-05-03 06:01  etl_temp_persistence.py: OK
2026-05-04 06:01  etl_temp_persistence.py: OK
2026-05-05 06:00  etl_temp_persistence.py: OK  (diurnal_curves FAIL: db locked)
2026-05-06 06:04  etl_temp_persistence.py: FAIL: 'database is locked'
2026-05-08 06:01  etl_temp_persistence.py: OK
2026-05-13 06:01  etl_temp_persistence.py: OK
2026-05-14 06:00  etl_temp_persistence.py: OK
2026-05-17 06:00  etl_temp_persistence.py: OK
```

The script returns `{"stored": N}` via stdout `print()`, but the recalibrate wrapper captures `r.stderr` only; the row count never reaches the log. Hence "OK" means **process exit 0**, not **rows written**.

---

## 5. Why is the table still empty?  Data-source archeology

Writer query (lines 65-77):

```python
rows = zeus.execute("""
    SELECT city, target_date, high_temp, source
    FROM observations
    WHERE high_temp IS NOT NULL
    ORDER BY city, target_date, ...
""").fetchall()
```

The `observations` table also exists in all three DBs. Live row counts:

| DB | `observations` rows | date range |
|---|---|---|
| `zeus_trades.db` | **0** | n/a |
| `zeus-world.db` | **145** (4 sources: `ogimet_metar_*` ×3 = 6 rows, `wu_icao_history` = 139 rows) | 2026-05-09 → 2026-05-13 (~5 days, 51 cities) |
| `zeus-forecasts.db` | **43,971** with `high_temp NOT NULL`, **868 distinct target_dates**, full historical coverage | 2023-12-27 → 2026-05-16 |

The K1 forecast-DB split (PR #114 / commit `eba80d2b9d` 2026-05-14) moved the canonical `observations` lineage to `zeus-forecasts.db` (see also `39ee725bc1` "Daemon one-and-done refactor: dual-pipeline forecasts, K1 obs schema"). The writer's `get_world_connection()` still points at the **stale** `zeus-world.db` copy (now only the 5-day live tail from the ICAO/WU/ogimet path, not the 868-day historical canonical).

Writer code path under 145 rows / 51 cities / 4 seasons / 9 delta buckets → most (city, season, delta_bucket) cells have n < 3, so the writer's `if n < 3: continue` filter (line 132) discards nearly every bucket. Result: **`stored=0` (or near 0), process exits 0, "OK"** — silent NEVER-WRITES disguised as healthy ETL.

---

## 6. Reader — `_check_persistence_anomaly` at L1065

```python
# src/engine/monitor_refresh.py:1064-1068
freq_row = conn.execute(
    "SELECT frequency, n_samples FROM temp_persistence "
    "WHERE city = ? AND season = ? AND delta_bucket = ?",
    (city_name, season, bucket),
).fetchone()
```

Under cycle conn (`get_trade_connection_with_world()` + forecasts ATTACH per `cycle_runner.py:68-90`), bare `FROM temp_persistence` resolves to **MAIN** = `zeus_trades.db` (0 rows). Even after writer is repaired, the reader still misses the world copy without a schema-qualifier.

---

## 7. Verdict

**MIGRATED — both ends broken.**

Not NEVER-WIRED (writer is scheduled & runs daily, exit 0). Not RETIRED (downstream reader is live in production cycle). Not OPERATOR-OWNED (writer derives from observations, not a hand-curated reference).

This is a **double-sided lineage drift after the K1 forecast-DB split**:
1. **Writer source-drift (F106 new, below)**: `etl_temp_persistence.py` reads `world.observations` (5-day stub) instead of the canonical `forecasts.observations` (868 days). Silently writes ~0 rows; exit code 0 disguises the failure.
2. **Reader bind-drift (F48 / F102)**: `monitor_refresh.py:1065` bare-name `FROM temp_persistence` binds to trade-DB MAIN (0 rows) instead of the writer's world-DB target.

Either-end fix alone is **insufficient**; both must land for persistence-anomaly discount to ever fire.

---

## 8. F48 L1065 — 1-line fix recommendation

Bind reader to the writer's target DB (world), matching K1 schema-qualifier discipline established in Run #15 Track 2 (`forecasts.settlements_v2`):

```python
# src/engine/monitor_refresh.py:1065
-    "SELECT frequency, n_samples FROM temp_persistence "
+    "SELECT frequency, n_samples FROM world.temp_persistence "
```

- **Necessary**: removes the bare-name ATTACH ambiguity; locks the reader to the writer's target DB.
- **Sufficient at runtime today?** NO. `world.temp_persistence` is itself 0 rows (writer source-drift, F106). Reader returns no row → falls through to default `return 1.0` (no discount). Karachi 5/17 persistence discount remains 1.0 for 100% of HIGH calls **until F106 is also fixed**.
- Pair with Run #15 T2 Edit B's `PERSISTENCE_FALLBACK_TRIGGERED` counter so the silent fall-through is at least observable post-deploy.

---

## 9. New findings surfaced this run

### F106 (SEV-2 HOT, NEW) — `etl_temp_persistence.py` reads stale `world.observations`
- **Owner**: `scripts/etl_temp_persistence.py:31` (`from src.state.db import get_world_connection as get_connection`)
- **Symptom**: ETL exits 0 daily but writes ~0 rows because source query hits 145-row world stub instead of 43,971-row forecasts canonical.
- **Root cause**: K1 forecast-DB split (PR #114, 2026-05-14) re-homed `observations` lineage to `zeus-forecasts.db`; writer was not migrated.
- **Fix shape**: open a forecasts conn for the source SELECT (`get_forecast_connection()` or ATTACH `forecasts AS forecasts`), continue writing to `world.temp_persistence`. ETL pipeline already supports cross-DB connections (e.g., `get_trade_connection_with_world`).
- **Silent failure mode**: stdout `print(f"Stored {stored} persistence entries")` is captured by subprocess but discarded by `_etl_recalibrate_body` (only `r.stderr` is logged). "OK" status = exit 0 only; row count is invisible to operators.

### F107 (SEV-3, NEW) — `_etl_recalibrate_body` swallows ETL stdout row counts
- **Owner**: `src/ingest_main.py:600-612` and `src/main.py:127-138`
- **Symptom**: Every ETL script returns `{"stored": N}` on stdout; the recalibrate wrapper only reads `r.stderr` for the `OK / FAIL: ...` decision. Operators have no way to detect "exit 0 with 0 rows written" failures.
- **Fix shape**: parse `r.stdout` for `Stored \d+` (or any structured row-count line) and include in the result dict, e.g., `'etl_temp_persistence.py': 'OK (stored=552)'`. Alarms when `stored=0` two days running.
- **Companion antibody to F106 / F102 / F48** — without this observability fix the next migration that re-homes a derived-table source will repeat the same silent-zero pattern.

---

## 10. Karachi 5/17 blast radius

- Persistence-anomaly discount has been silently 1.0 (no discount) for **100% of HIGH-leg refresh calls** since at least 2026-05-02 (first log line in window), and almost certainly since the K1 split landed 2026-05-14 if not earlier.
- Direction of bias: **under-conservative exit signal** for cities where ENS predicted a historically-rare day-over-day temperature swing relative to recent settlements. Karachi (HIGH leg) is in scope.
- Run #15 T2 Edits A–C (settlements_v2 schema-qualifier + counter + antibody test) close the OTHER half of `_check_persistence_anomaly`. Adding the L1065 schema-qualifier above closes the structural half. **Operational impact gated by F106** (writer source migration).

---

## 11. Probe-output antibody — commands rerun, deterministic

```bash
# Schema + counts
for db in state/zeus_trades.db state/zeus-world.db state/zeus-forecasts.db; do
  echo "--$db--"
  sqlite3 "$db" ".schema temp_persistence" 2>/dev/null
  sqlite3 "$db" "SELECT COUNT(*) FROM temp_persistence;" 2>/dev/null
done

# Writers / readers
grep -rn "temp_persistence" src/ scripts/ tests/

# ETL daily result dicts (last month)
grep "ETL recalibration" logs/zeus-ingest.err | tail -10

# Observation lineage
for db in state/zeus_trades.db state/zeus-world.db state/zeus-forecasts.db; do
  echo "--$db obs--"
  sqlite3 "$db" "SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM observations WHERE high_temp IS NOT NULL;" 2>/dev/null
done
```

All counts captured 2026-05-17 ≈19:00 CT.

---

## 12. Out of scope (deferred)

- ETL source-migration patch (F106 fix-spec) — needs companion review of `etl_diurnal_curves.py` and `etl_hourly_observations.py`, which likely share the same `get_world_connection`-on-stale-observations defect class.
- Backfill of `world.observations` from `forecasts.observations` (alternative to migrating writer) — not recommended; perpetuates duplicate-truth pattern that K1 split was designed to eliminate.
- Trade-DB `temp_persistence` table cleanup (`init_schema` over-creates the table in MAIN). Cosmetic; defer to schema-hygiene pass.
