# INV-37 Harvester Fix Plan
# Created: 2026-06-14
# Authority basis: ChatGPT PR#408 review blocker B1, INV-37 cross-DB atomicity law (AGENTS.md)

## Violation

`src/execution/harvester.py` `run_harvester()` (lines ~860-1082) opens TWO independent
DB connections and commits them separately:

```python
trade_conn = get_trade_connection()      # zeus_trades.db
shared_conn = get_forecasts_connection() # zeus-forecasts.db
...
def _db_op_trade() -> None:
    trade_conn.commit()   # trade side committed
    shared_conn.commit()  # crash here = partial state
```

A crash/kill/busy between the two commits creates logically impossible state.
INV-37 law (AGENTS.md): writes spanning K1 DB split MUST use ATTACH + single SAVEPOINT.

## Fix Design

### 1. New context manager in src/state/db.py

`forecasts_connection_with_trades_flocked`: opens zeus-forecasts.db as MAIN with
zeus_trades.db ATTACHed as `trades`. Mirrors `get_forecasts_connection_with_world`.

SQLite name resolution:
- forecasts-class tables (settlements, calibration_pairs, observations, ensemble_snapshots)
  → MAIN (forecasts.db) ✓
- trade-class tables (position_current, position_events, decision_log, chronicle,
  settlement_commands) → NOT in forecasts.db main → found in `trades` schema ✓

Ghost table safety: forecasts.db has no ghost trade-class tables (initialized via
init_schema_forecasts, not world-class init_schema). Confirmed via db_table_ownership.yaml.

### 2. Modified run_harvester() in src/execution/harvester.py

Single conn replacing trade_conn + shared_conn. SAVEPOINT wraps all writes.

### 3. Test

`tests/execution/test_inv37_harvester_atomicity.py`: fault-injection RED-on-revert.

## Changed Files

- `src/state/db.py` — new `forecasts_connection_with_trades_flocked`
- `src/execution/harvester.py` — single-conn SAVEPOINT pattern
- `tests/execution/test_inv37_harvester_atomicity.py` — new test
- `architecture/test_topology.yaml` — register test
