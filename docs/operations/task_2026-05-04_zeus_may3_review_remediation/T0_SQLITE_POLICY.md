# T0_SQLITE_POLICY — Planner Triage

**Created:** 2026-05-04
**Verdict:** MIXED — reality answers most parameters; one operator decision remains (DB physical isolation deferral)
**Captured-by:** planner subagent

---

## 1. What the operator was supposed to choose

Per MASTER_PLAN_v2 §8 T0.5:
> Operator chooses SQLite tactical timeout policy → `T0_SQLITE_POLICY.md`; exact `ZEUS_DB_BUSY_TIMEOUT_MS`, plus whether DB physical isolation is T2G or deferred T4 with live restrictions.

Two operator decisions are nominally required:

1. Exact value for `ZEUS_DB_BUSY_TIMEOUT_MS`.
2. Whether DB physical isolation is pulled forward to T2G or deferred to T4.

## 2. Reality findings (planner grep, 2026-05-04)

### 2.1 Current SQLite connection state

- `src/state/db.py:40` — `conn = sqlite3.connect(str(db_path), timeout=120)` — used by `_connect()` (called by `get_trade_connection`, `get_world_connection`, `get_backtest_connection`).
- `src/state/db.py:349` — `conn = sqlite3.connect(str(db_path), timeout=120)` — used by `get_connection(db_path)` (legacy default `ZEUS_DB_PATH` = `state/zeus.db`).

Both connection helpers hard-code `timeout=120` (seconds). There is **no `ZEUS_DB_BUSY_TIMEOUT_MS` environment variable** anywhere in `src/`, `scripts/`, or `config/` (planner ran `grep -rn "ZEUS_DB_BUSY_TIMEOUT" src/ scripts/ config/` — zero matches).

There is **no `PRAGMA busy_timeout`** anywhere in `src/` (planner ran `grep -rn "busy_timeout\|PRAGMA busy" src/` — zero matches). The Python sqlite3 `timeout=` argument and the SQLite `busy_timeout` PRAGMA are not the same thing in detail but the Python `timeout=` argument internally sets the busy handler.

### 2.2 Other connection sites with DIFFERENT timeouts

`grep -rn "sqlite3.connect" src/ scripts/`:

| Site | Timeout |
|---|---|
| `src/state/db.py:40` | `timeout=120` |
| `src/state/db.py:349` | `timeout=120` |
| `src/observability/status_summary.py:67` | (no timeout) |
| `src/riskguard/discord_alerts.py:167` | `timeout=5` |
| `src/data/market_scanner.py:610` | `timeout=30` |
| `scripts/migrate_observations_k1.py:177` | `timeout=120` |
| `scripts/migrate_add_authority_column.py:222` | (no timeout) |
| `scripts/verify_truth_surfaces.py:2152,2237,2261,2300,2922,2997,2999` | (no timeout) |
| Many other scripts | (no timeout) |

This is heterogeneous: 120s/30s/5s/0s coexist. T1E's claim that timeouts are inconsistent across connection paths is **confirmed by code**.

### 2.3 DB physical isolation status

`docs/to-do-list/known_gaps.md:21` is OPEN with status: **"CRITICAL: SQLite 单写者锁导致 live daemon 崩溃 (2026-05-04)"** — first observed today, *"live daemon 3次崩溃，实际交易窗口丢失"* (the live daemon crashed 3 times today; trading windows were lost).

`known_gaps.md:42`: *"DB 物理隔离（option 2）是唯一能使'rebuild 时 live daemon 崩溃'这一错误类别不可能发生的结构决策。Options 1/3 是降险措施。"* (DB physical isolation is the only structural decision that makes the bug category impossible. Options 1/3 are risk-reduction.)

Reality verdict: the operator has **already documented in the canonical known_gaps file** that physical isolation is the structural fix. The plan asks the operator to "choose"; the gaps file already records the choice direction.

### 2.4 Settings.json

`config/settings.json` does not contain a SQLite timeout key (planner grep: only `fill_timeout_seconds: 600` — unrelated, fill timeout). The setting would be a fresh introduction.

## 3. Reality-answered fields

| T0.5 field | Reality answer | Evidence |
|---|---|---|
| Existing timeout default | 120 seconds (`timeout=120`) on the two main `_connect()`/`get_connection()` paths | `src/state/db.py:40,349` |
| Existing timeout consistency | NOT consistent — connection sites range from 0s (no timeout) to 120s | `grep -rn "sqlite3.connect"` enumeration above |
| `ZEUS_DB_BUSY_TIMEOUT_MS` env support | **DOES NOT EXIST** in code today; T1E must introduce it | grep returned zero matches |
| `PRAGMA busy_timeout` use | **NOT IN USE** in `src/`; only Python `timeout=` argument | grep returned zero matches |
| Crash evidence | `known_gaps.md` records 3 crashes on 2026-05-04 | `docs/to-do-list/known_gaps.md:21-44` |
| Structural fix direction | DB physical isolation already named as the only category-killer in known_gaps | `docs/to-do-list/known_gaps.md:42` |

## 4. Recommended draft policy (operator-confirmable)

The planner proposes the following defaults; operator may override but reality strongly supports them:

```
ZEUS_DB_BUSY_TIMEOUT_MS: 30000   (30 seconds)
  Rationale: 120s blocks the live cycle for two minutes on contention,
  which the known_gaps record shows already crashed the daemon. 30s is
  long enough to ride out short writers (data ingest commits, normal
  init_schema()) but short enough that on rebuild contention the cycle
  fails fast and degrades to read-only monitor (T1E acceptance gate).

DB physical isolation: PULL FORWARD to T2G.
  Rationale: known_gaps explicitly states physical isolation is the only
  structural antibody and that today's crashes lost actual trading
  windows. T1E remains correct as tactical mitigation, but corrected
  live cannot be enabled until isolation lands. Per MASTER_PLAN_v2 T2G
  ("pull isolation forward"), this gates T3.

T1E scope (unchanged):
  1. Introduce ZEUS_DB_BUSY_TIMEOUT_MS read in db.py.
  2. Apply uniformly to _connect (line 40) and get_connection (line 349).
  3. Add db_write_lock_timeout_total counter on OperationalError "database is locked".
  4. On timeout: cycle degrades to read-only monitor for that cycle.
  5. Rebuild script refuses to start if .zeus/rebuild_lock.do_not_run_during_live exists.
  6. Rebuild shards transactions per city/metric (bounded transaction durations).
```

## 5. Residual operator decision

The operator must still confirm two points before T0_PROTOCOL_ACK can say `proceed_to_T1`:

1. **Accept default `ZEUS_DB_BUSY_TIMEOUT_MS=30000`?** Yes / set custom value / set as `<integer>`.
2. **Accept "DB physical isolation pulled forward to T2G as gating for T3 corrected live"?** Yes / Defer to T4 with explicit shadow-only restriction.

Until these two checkboxes are signed, T1E may proceed only on the assumption above and the closeout must flag if operator chose differently.

## 6. Source-evidence cite list (planner grep-verified within 10 minutes)

- `src/state/db.py:40` — `sqlite3.connect(str(db_path), timeout=120)`
- `src/state/db.py:349` — `sqlite3.connect(str(db_path), timeout=120)`
- `src/riskguard/discord_alerts.py:167` — `timeout=5`
- `src/data/market_scanner.py:610` — `timeout=30`
- `docs/to-do-list/known_gaps.md:21-44` — CRITICAL: SQLite single-writer lock crash entry, opened 2026-05-04
- `config/settings.json` — no SQLite timeout key present
- `architecture/source_rationale.yaml` — `src/state/db.py` zone=`K2_runtime`, authority_role=`db_connection_schema_runtime`

---

**Decision:** MIXED. Reality answers all measurement questions and the structural-fix direction; operator must still confirm the exact timeout value and pull-forward decision.
