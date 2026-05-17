# Run #14 — Track B: F46 + F48 root causes + 1-line fixes

**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `b973ece`
**Date**: 2026-05-17

---

## F46 — cycle_runtime upstream of K1 dual-write

### Root cause (1 sentence)
`cycle_runner.get_connection()` at `src/engine/cycle_runner.py:68-81` opens **trades.db** (`connect_or_degrade(_zeus_trade_db_path(), write_class="live")` with world ATTACHed) as the single cycle conn; this conn is threaded down through `execute_discovery_phase(conn, …)` to `log_forward_market_substrate(conn, …)` which then writes `market_events_v2` rows into trades.db instead of forecasts.db.

### Call chain (verified)
```
src/main.py:102 run_cycle(mode)
  → cycle_runner.py:574  conn = get_connection()      # TRADES.DB
  → cycle_runner.py:933  _execute_discovery_phase(conn, ...)
  → cycle_runtime.py:2166 execute_discovery_phase(conn, ...)
  → cycle_runtime.py:2366 log_forward_market_substrate(conn, ...)
  → db.py:3949 _insert_forward_market_event(conn, ...)
```

### 1-line fix
Remove the `conn` parameter from `log_forward_market_substrate` and have it open its own forecasts conn (mirrors `market_scanner.py:610`). Full sketch in `RUN_14_track_A_market_events_decision.md` §5.

### Antibody
`semantic_linter` rule `forward-substrate-conn-pin`: any caller of `log_forward_market_substrate` MUST NOT pass `conn` positional; any function that opens a conn and writes to `market_events_v2` MUST use `ZEUS_FORECASTS_DB_PATH`. Spec in Track A §6.

### Karachi 5/17 impact
None. Benign in current window (UNIQUE dedupes; readers use forecasts). Fix is safe to deploy after Karachi 5/17 + 5/19 positions exit.

---

## F48 — monitor_refresh.py:1041 reads legacy `settlements` (silent-zero deltas)

### Root cause (1 sentence)
`src/engine/monitor_refresh.py:1041` (`_persistence_discount` helper) reads from the **LEGACY** `settlements` table (not `settlements_v2`); if the prior 3 days have NULL `settlement_value` for `(city, target_date, temperature_metric='high', authority='VERIFIED')`, the helper logs `PERSISTENCE_CHECK_DISABLED` at WARNING and returns `1.0` (no discount), silently masking the persistence anomaly signal — and the legacy `settlements` table is no longer maintained as the authoritative source (Track A migration moved authoritative settlements to `settlements_v2` per `scripts/migrations/202605_backfill_settlements_v2.py`).

### Evidence
```
zeus-forecasts.db:
  settlements (legacy)      :  5599 rows
  settlements_v2 (current)  :  4016 rows
  settlements_v2 churn last 17 d: only 5/07 (3220), 5/11 (767), 5/17 (29)
  → settlements_v2 not refilling daily; 3-day lookback may hit empty days
```

Cross-table read confirms data CAN match (when present), but the legacy table is on path to retirement (see `migrate_add_authority_column.py` which does `DELETE FROM settlements WHERE authority != 'VERIFIED'` — half-baked migration policy).

`grep -rn "FROM settlements\b"` shows `monitor_refresh.py:1041` is the **only** src/-tree reader of legacy `settlements` (table without `_v2` suffix). All scripts/ readers are backfill / audit tools.

### 1-line fix
Repoint `monitor_refresh.py:1041` to `settlements_v2` AND ensure the conn passed to `_persistence_discount` is a forecasts conn (not the trades+world ATTACHed cycle conn — re-verify in same fix). The bare 1-line:

```python
# src/engine/monitor_refresh.py:1041 (was: FROM settlements)
"SELECT settlement_value FROM settlements_v2 "
"WHERE city = ? AND target_date = ? "
"AND temperature_metric = 'high' "
"AND authority = 'VERIFIED' LIMIT 1",
```

(The conn-source review may surface a second 1-liner in `monitor_refresh` — investigate-further item.)

### Antibody
`semantic_linter` rule `legacy-settlements-readonly`: any `SELECT … FROM settlements\b` in `src/` MUST emit a linter error pointing to `settlements_v2` as replacement. Scripts/ readers are allowed (backfill / migration / audit).

Plus relationship test (Fitz #2):
```python
def test_persistence_discount_uses_v2_settlements(tmp_db_with_v2_only):
    # Populate settlements_v2 but NOT legacy settlements; assert _persistence_discount
    # returns a non-1.0 value when 3-day deltas exist in v2.
    ...
```

### Karachi 5/17 impact
**HOT — read this carefully.** `monitor_refresh` is the Day0 truth plane (`src/engine/AGENTS.md:26`). For Karachi 5/17 (`day0_window` position `c30f28a5-d4e`), if `_persistence_discount` silently returns 1.0 (no discount) when it should have applied a 10–30% discount, the position's exit signal could be under-conservative. Risk: not catastrophic (discount is a *reduction* of confidence, returning 1.0 = MAX confidence = potentially holding longer than ideal), but worth verifying before next monitor tick.

**Recommended pre-deploy probe**: run `_persistence_discount(conn, 'Karachi', date(2026,5,17), predicted_high=...)` against the live forecasts conn; if WARNING `PERSISTENCE_CHECK_DISABLED` fires, the helper is currently a no-op for Karachi. Fix-then-deploy if so.

### Status
**F48 was already marked CONFIRMED-DEAD-READ in Run #13**; this Run #14 entry provides the **mechanical 1-line fix** + relationship test + Karachi impact statement.

---

## Summary table

| F# | Status before | Status after Run #14 | Fix size | Antibody type | Karachi-hot? |
|---|---|---|---|---|---|
| F46 | CONFIRMED-OPEN | ROOT-CAUSE-IDENTIFIED + FIX-SPECIFIED | 2 files, ~10 LOC | semantic_linter + relationship test | No (benign) |
| F48 | CONFIRMED-DEAD-READ | FIX-SPECIFIED + KARACHI-FLAGGED | 1 file, 1 LOC | semantic_linter + relationship test | **YES — probe before next monitor tick** |
