# Zeus Database Runtime Authority

Status: active durable authority law  
Scope: DB topology, SQLite/WAL behavior, busy-timeout, writer locks, bulk/live split, and lock-contention avoidance  
Machine authority: `architecture/db_runtime_manifest.yaml`, `architecture/db_table_ownership.yaml`  
Executable anchors: `src/state/db.py`, `src/state/db_writer_lock.py`  
Freshness model: durable runtime law. Current DB contents, row counts, lock state, WAL size, and process liveness are operations facts only.

---

## 1. Authority Boundary

DB runtime law is authority because DB lock bugs can halt live trading, suppress exits, corrupt lifecycle truth, or create duplicated side effects.

Authority order:

1. executable code and tests;
2. `architecture/db_table_ownership.yaml` and `architecture/db_runtime_manifest.yaml`;
3. this file;
4. canonical data/replay reference;
5. operations current facts while fresh;
6. evidence/history.

---

## 2. Physical DB Law

Current durable topology:

| DB | Path | Role |
|---|---|---|
| world | `state/zeus-world.db` | world/runtime records that remain world-owned |
| forecasts | `state/zeus-forecasts.db` | source runs, readiness, observations, forecast artifacts, forecast posteriors, settlement outcomes |
| trades | `state/zeus_trades.db` | trade decisions, execution facts, venue commands/events, positions, lifecycle truth |
| backtest | `state/zeus_backtest.db` | derived diagnostic output only |
| risk | `state/risk_state.db` | riskguard-only runtime state |

Table authority is `(table, db)`, not table name alone. `architecture/db_table_ownership.yaml` owns the mapping.

---

## 3. WAL And Lock Law

SQLite WAL permits concurrent readers and one writer. It does not permit multiple writers to make progress at the same time.

Current lock layers:

1. SQL-level `PRAGMA busy_timeout` on every connection, owned by `src/state/db.py::_apply_busy_timeout`.
2. In-process world DB write mutex, owned by `src/state/db.py::world_write_lock`.
3. No blocking I/O while the same thread holds the world mutex, enforced by `assert_no_world_mutex_held_for_io`.
4. Cross-process per-DB/per-class flock, owned by `src/state/db_writer_lock.py::db_writer_lock`.
5. `BulkChunker`, owned by `src/state/db_writer_lock.py`, so bulk writers yield and live writers can make progress.

These layers are complementary. Removing one because another exists is not safe.

---

## 4. Busy Timeout Law

`sqlite3.connect(timeout=N)` is not enough. Zeus sets SQL-level `PRAGMA busy_timeout` so a writer that loses the WAL write lock waits instead of instantly raising database-locked errors.

Durable default:

```text
ZEUS_DB_BUSY_TIMEOUT_MS default = 30000
```

Malformed values fall back where code defines fallback. Negative values fail loudly.

---

## 5. World Mutex Law

`world_write_lock` is for short world-DB write transactions only.

Allowed:

```text
acquire world mutex -> BEGIN IMMEDIATE -> DB writes -> COMMIT/ROLLBACK -> release
```

Forbidden:

```text
acquire world mutex -> HTTP/CLOB/chain call -> DB writes -> release
```

Reads do not require the world write mutex. WAL already permits concurrent readers.

---

## 6. Bulk/Live Split

Live writes and bulk writes are different write classes:

- `LIVE`: live trading, protection, command, lifecycle, and risk hot-path writes.
- `BULK`: backfill, migration, replay, large maintenance writes.

Bulk work must chunk/yield. A bulk writer that monopolizes a DB and starves live writers is a live-money bug.

---

## 7. Current Facts

The following are not durable authority:

- current WAL file size;
- current lock holder;
- current PID;
- DB row counts;
- active process status;
- current queue depth;
- active migration progress.

If they must be recorded, they belong in operations current-fact/evidence surfaces with freshness/expiry.

---

## 8. Change Protocol

Any DB runtime change must update:

1. source code;
2. `architecture/db_runtime_manifest.yaml`;
3. `architecture/db_table_ownership.yaml` if table ownership changes;
4. this authority file if law changes;
5. tests for lock, transaction, or ownership behavior;
6. docs registry if routes change.

Do not patch around database-locked bugs with sleeps in arbitrary callers. Fix the lock layer or transaction boundary that made contention unsafe.
