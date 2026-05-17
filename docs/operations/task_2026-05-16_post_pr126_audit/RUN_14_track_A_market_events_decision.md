# Run #14 — Track A: market_events_v2 structural decision (F22 + F81 + F82)

**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `b973ece`
**Date**: 2026-05-17
**Verdict**: **A2 (two-zone, forecasts-authoritative)** — single recommendation.

---

## 1. Evidence: three DBs, one table name, three populations

```
DB                              rows   +24h   +1h    max recorded_at
zeus-world.db.market_events_v2  2112      0     0    2026-05-13 16:45 (DEAD post-K1)
zeus_trades.db.market_events_v2 7964   1276   638    2026-05-17T14:59 UTC (HOT)
zeus-forecasts.db.market_events_v2 10552 638   0    2026-05-17 14:55 (HOT)
```

Schemas across all three DBs are **identical** (`market_slug`, `condition_id`, etc.) with `UNIQUE(market_slug, condition_id)`. Sister-table topology confirms zone purposes:
- `zeus_trades.db` owns `market_price_history` (605,887 rows, +172,909/24h) — **runtime tick substrate, trades-DB exclusive**.
- `zeus-forecasts.db` owns `observations` (43,970), `settlements_v2` (4,016), `ensemble_snapshots_v2` (1.1M), `calibration_pairs_v2` (91M), `source_run` — **calibration / settlement / forecast corpus**.
- `zeus-world.db` v2 tables are 0 rows except a stale 2112-row `market_events_v2` + 145-row `observations` (last 5/13, pre-K1 era).

This is **NOT a triple-write**. It is a **dual active writer**:

| Writer | File | Conn → DB | Rate observed |
|---|---|---|---|
| #1 (gamma scanner survey) | `src/data/market_scanner.py:610` | `sqlite3.connect(ZEUS_FORECASTS_DB_PATH)` | matches forecasts +638/24h exactly |
| #2 (forward substrate from live cycle) | `src/engine/cycle_runtime.py:2366` (`log_forward_market_substrate(conn,…)`) | `cycle_runner.get_connection()` → `connect_or_degrade(_zeus_trade_db_path(), write_class="live")` (trades.db, world ATTACHed) | matches trades +1276/24h (~2× cycle rate, dedup vs UNIQUE) |
| #3 (one-shot, historical) | `scripts/migrate_world_observations_to_forecasts.py:180-230` | ATTACH world + forecasts | not active |

DEAD: world.db 2112 rows are K1-era residue; no active writer touches them post-K1.

## 2. F46 root cause (call chain, single hop)

```
src/main.py:102           summary = run_cycle(mode)
src/engine/cycle_runner.py:501    def run_cycle(mode):
src/engine/cycle_runner.py:574        conn = get_connection()                     ← TRADES.DB
src/engine/cycle_runner.py:68     def get_connection():
src/engine/cycle_runner.py:78         conn = connect_or_degrade(_zeus_trade_db_path(), write_class="live")
src/engine/cycle_runner.py:81         # ATTACH world schema
src/engine/cycle_runner.py:933    _execute_discovery_phase(conn, ...)
src/engine/cycle_runner.py:485    return _runtime.execute_discovery_phase(conn, ...)
src/engine/cycle_runtime.py:2166  def execute_discovery_phase(conn, ...)
src/engine/cycle_runtime.py:2366  result = log_forward_market_substrate(conn, markets=..., scan_authority=authority)
src/state/db.py:3830              def log_forward_market_substrate(conn, ...)
src/state/db.py:3949                  _insert_forward_market_event(conn, values)   ← INSERT INTO market_events_v2
```

Conn is trades-rooted because `cycle_runner` is the monolithic orchestrator; trades is its primary write target (commands/positions). Forward substrate is a **semantic forecasts artifact** but lands in trades because the cycle conn is the only conn in scope.

## 3. Karachi 5/17 + 5/19 impact statement

Position `c30f28a5-d4e` is currently `day0_window` (Karachi). The dual-write is **currently benign** for Karachi 5/17 and the next 5/19 window: UNIQUE constraint dedupes per (market_slug, condition_id) within each DB independently, both writes succeed, reads at runtime use `forecasts.market_events_v2` (via `ingest_main.py:853` + scanner re-read). The 2112-row stale world.db copy is dead but harmless. **No blocker, no exit pressure, no decision blocked.**

The risk is **lineage ambiguity post-incident** (which DB is "true" for a market identity?) and **audit confusion**, not money loss in the current window.

## 4. Decision: A2 (two-zone, forecasts-authoritative)

| Option | Where market_events_v2 lives | Blast radius | Verdict |
|---|---|---|---|
| A1 forecasts-only consolidate | drop trades + world copies, refactor cycle conn to expose forecasts | LARGE — cycle_runner conn topology change | reject — over-broad |
| **A2 two-zone forecasts-authoritative** | **forecasts.market_events_v2 = authoritative; trades + world copies drained** | **SMALL — local change in `log_forward_market_substrate`** | **ACCEPT** |
| A3 tri-zone + `source_db` column | accept duplication as feature, add lineage col | MEDIUM | reject — adds entropy, no consumer needs `source_db` |

## 5. Recommended 1-line fix path (low-blast-radius)

Have `log_forward_market_substrate` open its **own forecasts conn** instead of accepting an opaque conn from cycle_runtime — mirrors the established `market_scanner.py:610` pattern. The cycle_runtime caller no longer threads `conn` to this helper.

```python
# src/state/db.py:3830 (sketch)
def log_forward_market_substrate(*, markets, recorded_at, scan_authority):
    from src.state.db import ZEUS_FORECASTS_DB_PATH
    with sqlite3.connect(str(ZEUS_FORECASTS_DB_PATH), timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        for m in markets:
            _insert_forward_market_event(conn, _build_values(m, recorded_at, scan_authority))
            _insert_forward_price_history(conn, _build_price_values(m, recorded_at))
        conn.commit()
```

**Callers to update**: `src/engine/cycle_runtime.py:2366` (drop the `conn` positional). No other callers.

**Migration** (one-shot, after fix lands):
1. Backfill `forecasts.market_events_v2 ← trades.market_events_v2` via adapted `migrate_world_observations_to_forecasts.py` (already has the ATTACH pattern).
2. Verify: `SELECT COUNT(*) FROM trades.market_events_v2 EXCEPT SELECT COUNT(*) FROM forecasts.market_events_v2 WHERE …` → 0.
3. `DROP TABLE trades.market_events_v2` post-verify.
4. Leave world.db copy untouched (already dead, no harm).

## 6. Antibody (semantic linter rule)

Add to `scripts/semantic_linter.py`:

```
RULE forward-substrate-conn-pin:
  Any call to `log_forward_market_substrate(...)` MUST NOT pass a positional `conn`.
  Any function in src/ that opens a conn and then calls `_insert_forward_market_event` or
  `_insert_forward_price_history` MUST use ZEUS_FORECASTS_DB_PATH.
```

Plus a sister assert in `tests/test_market_events_topology.py`:

```python
def test_market_events_v2_writes_land_in_forecasts_only():
    # Simulate one cycle, then:
    assert _count("forecasts.db", "market_events_v2") > 0
    assert _delta("trades.db", "market_events_v2", since=t0) == 0
    assert _delta("world.db",   "market_events_v2", since=t0) == 0
```

## 7. Blast radius summary

- Files touched (fix): 2 (`src/state/db.py:3830`, `src/engine/cycle_runtime.py:2366`)
- Files touched (migration script + test): 2
- Schema change: NONE
- Behavior change: writes that previously landed in trades.market_events_v2 land in forecasts.market_events_v2 instead. UNIQUE constraint already prevents conflicts.
- Karachi 5/17 / 5/19: no impact (benign in current window; can deploy after positions exit).

## 8. New findings opened by this track

- **F46 → CONFIRMED-OPEN + ROOT-CAUSE-IDENTIFIED** (this doc, §2).
- **F81 + F82 + F22** → consolidated under this verdict; close once A2 implementation ships.
- No new F-numbers required for Track A.
