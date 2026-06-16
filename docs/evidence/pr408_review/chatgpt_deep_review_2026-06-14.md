# ChatGPT PR#408 Deep Review — INV-37 Harvester Fix Plan
# Created: 2026-06-14
# Authority basis: ChatGPT PR#408 review blocker B1, INV-37 cross-DB atomicity law (AGENTS.md)

## Problem: INV-37 Cross-DB Atomicity Violation

`src/execution/harvester.py` `run_harvester()` opens TWO independent DB connections:
- `trade_conn = get_trade_connection()` → zeus_trades.db
- `shared_conn = get_forecasts_connection()` → zeus-forecasts.db

Then commits them INDEPENDENTLY in `_db_op_trade()`:
```python
def _db_op_trade() -> None:
    trade_conn.commit()   # trade side committed
    shared_conn.commit()  # crash here = partial state
```

A crash/kill/busy between the two commits creates logically impossible state:
- settlement truth + calibration pairs written to forecasts.db
- positions NOT settled in zeus_trades.db (or vice versa)
→ contaminated calibration, PnL, redeem state, future sizing

## INV-37 Law

Root AGENTS.md: writes spanning the K1 DB split (zeus-world.db / zeus-forecasts.db / zeus_trades.db)
MUST use ATTACH + a single SAVEPOINT, NEVER independent connections.

## Fix

### New context manager in src/state/db.py

Add `forecasts_connection_with_trades_flocked` (mirrors `get_forecasts_connection_with_world`):
- Opens zeus-forecasts.db as MAIN
- ATTACHes zeus_trades.db as `trades` schema
- Acquires writer-lock flocks on BOTH DBs in canonical alphabetical order
  (zeus-forecasts.db < zeus_trades.db per canonical_lock_order)

SQLite name resolution in this configuration:
- forecasts-class tables (settlements, calibration_pairs, observations, ensemble_snapshots)
  → MAIN (zeus-forecasts.db) ✓ (live tables exist here)
- trade-class tables (position_current, position_events, decision_log, chronicle,
  settlement_commands, executable_market_snapshots)
  → NOT in forecasts.db main, found in attached `trades` schema (zeus_trades.db) ✓

Ghost tables in forecasts.db: none for trade-class tables (forecasts.db was initialized
cleanly via init_schema_forecasts, not the old world-class init_schema). Safe to rely
on SQLite name resolution fallthrough.

### Modified run_harvester() in src/execution/harvester.py

Replace:
```python
trade_conn = get_trade_connection()
shared_conn = get_forecasts_connection()
```

With:
```python
with forecasts_connection_with_trades_flocked(write_class="live") as conn:
    # single conn: forecasts MAIN + trades ATTACHED
    # trade_conn = conn (for functions expecting trade writes)
    # shared_conn = conn (for functions expecting forecasts writes)
```

All writes inside a single `SAVEPOINT harvester_settlement` → all-or-nothing per INV-37.

### Functions that receive both trade_conn and shared_conn

These functions are called with the same single conn for both arguments:
- `_preflight_harvester_stage2_db_shape(conn, conn)` — read-only preflight
- `_snapshot_contexts_for_market(conn, conn, ...)` — read-only snapshot resolution
- `_log_snapshot_context_resolution(conn, ...)` — chronicle write (trade-class, resolved via ATTACH)

### Commit change

Replace `_db_op_trade()` two-commit pattern with single SAVEPOINT:
```python
conn.execute("SAVEPOINT harvester_settlement")
try:
    # all writes happen here (single conn)
    conn.execute("RELEASE SAVEPOINT harvester_settlement")
    conn.commit()
except:
    conn.execute("ROLLBACK TO SAVEPOINT harvester_settlement")
    conn.execute("RELEASE SAVEPOINT harvester_settlement")
    raise
```

## Test

`tests/execution/test_inv37_harvester_atomicity.py`:
- Build temp forecasts + trade DBs with minimal schema
- Drive settlement write via patched `_write_settlement_truth` + `_settle_positions`
- Inject exception AFTER forecasts write, BEFORE trade commit
- Assert: with PRE-FIX code → forecasts side persists, trade side doesn't (partial state)
- Assert: with FIX → neither persists (atomic rollback)

## Files Changed

- `src/state/db.py` — new `forecasts_connection_with_trades_flocked` context manager
- `src/execution/harvester.py` — `run_harvester()` use single conn + SAVEPOINT
- `tests/execution/test_inv37_harvester_atomicity.py` — RED-on-revert test
- `architecture/test_topology.yaml` — register new test

## Authority

- INV-37: AGENTS.md root "writes spanning K1 DB split MUST use ATTACH + single SAVEPOINT"
- Pattern: `get_forecasts_connection_with_world` in src/state/db.py (L583+)
- Review: ChatGPT PR#408 blocker B1
