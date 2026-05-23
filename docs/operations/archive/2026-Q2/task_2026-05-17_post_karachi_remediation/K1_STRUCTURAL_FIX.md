# K1 Structural Fix — Wave 2 (2026-05-17)

## K-axes

The 10 brief findings plus F102 (coordinator addition) collapse into **2 K-axes**:

### K-A: Writer fan-out — forecast_class writes land in wrong DB
**Root**: `log_forward_market_substrate` accepts an opaque `conn` argument.  
When called from `_record_forward_market_substrate` in `cycle_runtime.py`, the conn
is the cycle's trades-rooted connection (MAIN = zeus_trades.db). The unqualified
`INSERT INTO market_events_v2` at `db.py:3635` resolves to MAIN → trades.db, creating
an active dual-write (market_events_v2 rows accumulate in both forecasts.db AND trades.db).

**Fix (Decision A2)**: `log_forward_market_substrate` drops its `conn` parameter and
opens its own `sqlite3.connect(ZEUS_FORECASTS_DB_PATH)` internally — mirrors the
`market_scanner.py:610` pattern already established. The call site in cycle_runtime.py
drops the positional `conn` argument; all test callers updated to match.

**Findings subsumed**: F19 (market_events_v2 active dual-write), F46 (cycle dual-write
upstream), F81 (world.db market_events leak), F82 (trades.db fan-out)

---

### K-B: Reader bare-name binding — ATTACH resolution falls to wrong MAIN
**Root**: Under the cycle connection (MAIN = zeus_trades.db), any bare `FROM <table>`
resolves first to trades.db (the MAIN). `monitor_refresh.py:1041` bare `FROM settlements`
and `:1065` bare `FROM temp_persistence` both silently dead-read trades.db (0 rows).

`settlements_v2` was renamed correctly in Run #14 but without schema qualification —
`FROM settlements_v2` still resolves to MAIN (trades.db, also 0 rows). F103 is the
re-opened F48 finding: the Run #14 fix was insufficient.

**Fix**: Schema-qualify both reads:
- `FROM settlements` → `FROM forecasts.settlements_v2` (Edits A/B/C from RUN_15_track2)
- `FROM temp_persistence` → `FROM world.temp_persistence` (F102 coordinator addition)

`_check_persistence_anomaly` runs under `get_forecasts_connection_with_world()`
where MAIN = forecasts.db, so `world.` qualifier correctly addresses the ATTACHed world.db.

**Findings subsumed**: F48 (settlements dead-read), F103 (Run #14 fix insufficient),
F102 (temp_persistence dead-read)

---

## Per-finding outcome table

| Finding | Severity | Outcome | Notes |
|---------|----------|---------|-------|
| F19 | SEV-2 HOT | FIX_LANDED (K-A) | market_events_v2 dual-write stopped |
| F46 | SEV-2 HOT | FIX_LANDED (K-A) | cycle_runtime call site updated |
| F48 | SEV-2 HOT | FIX_LANDED (K-B) | settlements_v2 schema-qualified |
| F63 | SEV-2 | RETRACT-already-fixed | data_chain_monitor.sh:26 reads forecasts.db |
| F71 | SEV-2 | RETRACT-already-fixed | check_forecast_live_ready.py imports ZEUS_FORECASTS_DB_PATH |
| F81 | SEV-2 HOT | FIX_LANDED (K-A) | world.db leak stops with K-A |
| F82 | SEV-2 HOT | FIX_LANDED (K-A) | trades.db fan-out stops with K-A |
| F83 | SEV-3 | DEFER-out-of-K-scope | schema drift, separate schema-hygiene pass |
| F84 | SEV-3 | DEFER-out-of-K-scope | backfill scripts, separate wave |
| F102 | SEV-2 HOT | FIX_LANDED (K-B) | world.temp_persistence schema-qualifier |
| F103 | SEV-2 HOT | FIX_LANDED (K-B) | Run #14 insufficient fix completed |

---

## Deploy preconditions

- **K-A (writer fan-out fix)**: UNIQUE constraint on `(market_slug, condition_id)` in
  market_events_v2 ensures the existing trades.db rows are simply unreferenced after
  the fix; no migration needed. Karachi position `c30f28a5-d4e` is live — deploy-safe
  between cycles because the fix changes write destination, not read path.

- **K-B (reader schema-qualifier fix)**: Behavior-neutral until `world.temp_persistence`
  is populated (F106, separate ETL source migration deferred). `forecasts.settlements_v2`
  queries return same result as before (settlements_v2 was already the correct table name;
  the only change is the schema prefix). Per RUN_15 §7: Edits A–C are behavior-neutral alone.

---

## Files changed

| File | Change |
|------|--------|
| `src/state/db.py:3830` | Drop `conn` param from `log_forward_market_substrate`; open own forecasts conn |
| `src/engine/cycle_runtime.py:2366` | Drop positional `conn` from call site |
| `src/engine/monitor_refresh.py:1041,1065` | Schema-qualify `settlements` → `forecasts.settlements_v2`, `temp_persistence` → `world.temp_persistence` |
| `tests/test_market_scanner_provenance.py` | Remove `conn` positional from ~11 call sites |
| `tests/test_schema_v2_gate_a.py:391` | Remove `conn` positional from 1 call site |
| `tests/test_k1_reader_isolation.py` | Extend antibody tests: K-A writer path + K-B schema-qualifier + world.temp_persistence |
