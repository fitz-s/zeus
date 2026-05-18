# FIX_SEV1_BUNDLE — F2/F7/F15/F18/F23 fix-shapes

**Authority**: Sonnet SEV-1 deep-dive 2026-05-17T13:15Z.
**Status**: All 5 findings have concrete fix shapes. PR sequencing requires F23 (migration runner) first because F7/F15 backfills need it.

---

## F2 — selection_hypothesis_fact.decision_id 100% NULL

**Probe (HEAD)**: `(1518, 1518)` — 100% NULL confirmed.

**Fix** (`src/engine/evaluator.py:1378` + `:1535`):

```python
# evaluator.py:1378 — add parameter
def _record_selection_family_facts(
    conn, *, candidate, edges, filtered, hypotheses,
    decision_snapshot_id: str, decision_id: str | None = None,  # NEW
    selected_method, recorded_at, decision_time_status=None,
) -> dict:

# evaluator.py:1535 — forward kwarg
result = log_selection_hypothesis_fact(
    conn,
    hypothesis_id=row["hypothesis_id"],
    family_id=row["family_id"],
    candidate_id=row["candidate_id"],
    decision_id=decision_id,  # NEW
    ...
)
```

**Antibody** (`tests/state/test_lineage_join_keys.py`):
```python
def test_selection_hypothesis_fact_decision_id_not_null_after_write(trade_conn):
    log_selection_hypothesis_fact(trade_conn, ..., decision_id="test-dsi-001", ...)
    row = trade_conn.execute("SELECT decision_id FROM selection_hypothesis_fact WHERE hypothesis_id=?", ("test-hyp",)).fetchone()
    assert row["decision_id"] is not None
```

**LOC**: ~8 lines. **Karachi blast radius**: ZERO (audit/calibration table).

---

## F7 — order_intent / venue_command lineage gap

**Probe (HEAD)**: 6/64 `execution_fact` rows lack the full `execution_fact → venue_commands → venue_order_facts` chain.

**Root cause**: `execution_fact` has `intent_id` PK but no `command_id` FK; chain traversable only via `decision_id`. 6 orphans are either `pending_fill_authority` entries (where order was never submitted, WAD) OR a `retry_pending` exit with `decision_id=NULL` (`c30f28a5-d4e:exit` — the Karachi position's exit handle).

**Fix** (`src/state/db.py:5663` + DDL migration):

```python
def log_execution_fact(conn, *, intent_id, position_id, order_role,
                       decision_id=None, command_id: str | None = None, ...) -> dict:
    # ... and in the INSERT: add command_id column + ? binding
```

DDL migration (depends on F23 framework):
```python
# scripts/migrations/202605_add_execution_fact_command_id.py
def up(conn):
    conn.execute("ALTER TABLE execution_fact ADD COLUMN command_id TEXT")
    conn.execute("CREATE INDEX ix_execution_fact_command_id ON execution_fact(command_id) WHERE command_id IS NOT NULL")
```

**Antibody**:
```python
def test_execution_fact_command_id_linkable(trade_conn):
    orphans = trade_conn.execute("""
      SELECT COUNT(*) FROM execution_fact ef
      WHERE command_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM venue_commands vc WHERE vc.command_id=ef.command_id)
    """).fetchone()[0]
    assert orphans == 0
```

**LOC**: ~20 lines (DDL + signature + test). **Karachi blast radius**: LOW — written on entry side, not redeem cascade. The `c30f28a5-d4e:exit` orphan is a separate defect (exit orders missing decision_id passthrough).

---

## F15 — settlements vs settlements_v2 1583-row gap

**Probe (HEAD, zeus-forecasts.db)**: `settlements=5599`, `settlements_v2=4016`. **Both MAX(settled_at) = '2026-05-17T05:46:44+00:00'** — dual-write is currently active; gap is historical hole, NOT ongoing divergence.

**Fix** — one-time backfill migration:

```python
# scripts/migrations/202605_backfill_settlements_v2.py
def up(conn):
    """Backfill settlements_v2 from settlements for pre-dual-write rows.
    Safe: UNIQUE(city,target_date,temperature_metric) makes this idempotent."""
    conn.execute("""
        INSERT OR IGNORE INTO settlements_v2
            (city, target_date, temperature_metric, market_slug, winning_bin,
             settlement_value, settlement_source, settled_at, authority, provenance_json,
             recorded_at)
        SELECT city, target_date, temperature_metric, market_slug, winning_bin,
               settlement_value, settlement_source, settled_at, authority, provenance_json,
               settled_at
        FROM settlements s
        WHERE NOT EXISTS (
            SELECT 1 FROM settlements_v2 v2
            WHERE v2.city=s.city AND v2.target_date=s.target_date
            AND v2.temperature_metric=s.temperature_metric
        )
    """)
```

**Pre-check before running migration**:
```sql
SELECT city, target_date, temperature_metric, COUNT(*) AS dups
FROM settlements GROUP BY city, target_date, temperature_metric
HAVING COUNT(*) > 1;
```
If any rows return → operator review before backfill (duplicates would silently drop).

**Antibody**:
```python
def test_settlements_v2_not_behind_settlements(forecasts_conn):
    v1 = forecasts_conn.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    v2 = forecasts_conn.execute("SELECT COUNT(*) FROM settlements_v2").fetchone()[0]
    assert v2 >= v1 * 0.99, f"settlements_v2 ({v2}) lags settlements ({v1}) by more than 1%"
```

**LOC**: ~25 lines. **Karachi blast radius**: LOW — `harvester_pnl_resolver.py:78` reads `FROM settlements` (v1, has all rows); no live query reads v2.

---

## F18 — INSERT OR IGNORE silent loss (market_scanner.py:627)

**Probe (HEAD)**: line 627 confirmed `INSERT OR IGNORE INTO market_events_v2 ...` inside raw `sqlite3.connect()`.

**Fix** — instrument ignored-count detection:

```python
# BEFORE (market_scanner.py:~627)
cursor = conn.execute("INSERT OR IGNORE INTO market_events_v2 ...", (...))
inserted += cursor.rowcount

# AFTER
cursor = conn.execute("INSERT OR IGNORE INTO market_events_v2 ...", (...))
if cursor.rowcount == 1:
    inserted += 1
else:
    logger.debug("market_events_v2 INSERT ignored for condition_id=%s", condition_id)

# After conn.commit():
if inserted == 0 and results:
    logger.warning(
        "market_scanner: 0 rows inserted out of %d events — possible constraint storm",
        len(results),
    )
```

**Antibody**:
```python
def test_insert_or_ignore_logs_zero_insert_warning(caplog, forecasts_conn):
    _persist_market_events_to_db(sample_events, db_path=forecasts_conn_path)  # prime
    with caplog.at_level(logging.WARNING, logger="src.data.market_scanner"):
        count = _persist_market_events_to_db(sample_events, db_path=forecasts_conn_path)
    assert count == 0
    assert "0 rows inserted" in caplog.text
```

**LOC**: ~10 lines. **Karachi blast radius**: ZERO (market discovery metadata only).

**Deeper fix** (F22 tracked): migrate raw `sqlite3.connect` to `get_forecasts_connection()`. The logging patch is the minimal viable antibody for SEV-1.

---

## F23 — Migration runner architecturally bare

**Probe (HEAD)**: `scripts/migrations/` contains only `__init__.py` (6 LOC stub) + `202605_add_redeem_operator_required_state.py`. No ledger. No CLI.

**Fix** — minimal viable framework:

```python
# scripts/migrations/__init__.py (replace 6-line stub)
import importlib
from datetime import datetime
from pathlib import Path
import sqlite3

MIGRATIONS_DIR = Path(__file__).parent

def _get_pending(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS _migrations_applied
        (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)""")
    applied = {r[0] for r in conn.execute("SELECT name FROM _migrations_applied")}
    scripts = sorted(MIGRATIONS_DIR.glob("2*.py"))
    return [s for s in scripts if s.stem not in applied]

def apply_migrations(conn, *, dry_run=False, target=None):
    for script in _get_pending(conn):
        if target and script.stem != target:
            continue
        mod = importlib.import_module(f"scripts.migrations.{script.stem}")
        if dry_run:
            print(f"[dry-run] would apply: {script.stem}")
            continue
        mod.up(conn)
        conn.execute("INSERT INTO _migrations_applied VALUES (?,?)",
                     (script.stem, datetime.utcnow().isoformat()))
        conn.commit()
        print(f"applied: {script.stem}")
```

CLI wrapper: `python -m scripts.migrations apply [--dry-run] [--target=NAME]` via argparse + `get_trade_connection()`.

**Bootstrap concern**: `202605_add_redeem_operator_required_state.py` was already applied to production; the ledger must NOT re-apply it. Two options:
- (a) Seed `_migrations_applied` with the known-applied name at table-create time
- (b) Document that DDL is idempotent (CREATE TABLE IF NOT EXISTS, ALTER ... IF NOT EXISTS-style) so re-run is no-op

**Antibody**:
```python
def test_idempotent_apply(tmp_db):
    apply_migrations(tmp_db)
    apply_migrations(tmp_db)  # second run no-op
    count = tmp_db.execute("SELECT COUNT(*) FROM _migrations_applied").fetchone()[0]
    assert count == len(list(Path("scripts/migrations").glob("2*.py")))
```

**LOC**: ~60 lines. **Karachi blast radius**: ZERO (infrastructure-only).

---

## PR sequencing

1. **PR-L (F23 first)** — unblocks all future migrations including F7/F15 backfills; ~60 LOC; zero runtime risk
2. **PR-A (F2)** — 8 LOC, stops ongoing lineage hemorrhage
3. **PR-E (F15 backfill)** — 25 LOC, runs via PR-L's runner
4. **PR-J+ (F18 + F7 bundle)** — both touch execution/observability surfaces; ~30 LOC

**Do NOT bundle F2 with F7** — F7 requires F23's runner for its DDL migration.

## Cross-finding antibody

ONE parametrized test in `tests/state/test_lineage_join_keys.py` covers F2 + F7 + F25 (the triple-NULL family): after any write helper is called, assert designated FK column is non-null. 5 parametrized cases × 1 test file = 1 antibody covering the entire bug category.

## Total LOC delta

| Finding | LOC |
|---|---|
| F2 | ~8 |
| F7 | ~20 |
| F15 | ~25 |
| F18 | ~10 |
| F23 | ~60 |
| **Total** | **~123** |
