# FIX_K1_READERS — F40 + F41 Ship-Ready Fix Shape

Generated: 2026-05-17. Authority: task brief + live source audit.

---

## §A F40 — bridge_oracle_to_calibration.py

### Current code (verbatim)

**Line 71** (module-level):
```python
DB_PATH = ROOT / "state" / "zeus-world.db"
```

**Lines 161–167** (bridge() early-exit path):
```python
    conn = sqlite3.connect(str(DB_PATH))
    settlements = _load_settlements(conn)

    snapshots = _load_snapshots()
    if not snapshots:
        logger.info("No shadow snapshots found in %s", oracle_snapshot_dir())
        conn.close()
        return {"cities": 0, "comparisons": 0}
```

**Lines 180+195** (observation_instants_v2 queries inside `_get_day_coverage` closure):
```python
            FROM observation_instants_v2
            WHERE city = ? AND target_date = ? AND source = ?
```
Note: `observation_instants_v2` is `world_class` (db: world). `settlements` is `forecast_class` (db: forecasts). Both are read on a single bare `conn` opened against `zeus-world.db` — correct for world tables, wrong for `settlements`.

**Line 401** (normal exit):
```python
    conn.close()
```

### Test code that patches DB_PATH

`tests/test_bridge_oracle_to_calibration.py:53`:
```python
@patch("scripts.bridge_oracle_to_calibration.DB_PATH")
def test_bridge_coverage_filtering(
    mock_db_path, mock_db, storage_root_with_snapshot, tmp_path
):
    db_path, conn = mock_db
    mock_db_path.__str__.return_value = str(db_path)
```

The mock calls `DB_PATH.__str__()` to intercept `sqlite3.connect(str(DB_PATH))`. After the fix removes that connect call, this patch resolves to a now-unused symbol.

### AFTER-state diff

```diff
-DB_PATH = ROOT / "state" / "zeus-world.db"
+# DB_PATH removed: use get_forecasts_connection_with_world() — see K1 fix F40 2026-05-17

+from src.state.db import get_forecasts_connection_with_world  # noqa: E402

-import sqlite3
+import sqlite3  # kept: _load_settlements signature unchanged

 def bridge(dry_run: bool = False) -> dict:
-    conn = sqlite3.connect(str(DB_PATH))
-    settlements = _load_settlements(conn)
-
-    snapshots = _load_snapshots()
-    if not snapshots:
-        logger.info("No shadow snapshots found in %s", oracle_snapshot_dir())
-        conn.close()
-        return {"cities": 0, "comparisons": 0}
+    with get_forecasts_connection_with_world() as conn:
+        settlements = _load_settlements(conn)
+        snapshots = _load_snapshots()
+        if not snapshots:
+            logger.info("No shadow snapshots found in %s", oracle_snapshot_dir())
+            return {"cities": 0, "comparisons": 0}
 
         # ... (closure _get_day_coverage, city_stats loop — all indent +4 under with)
 
-    conn.close()
-    return {
+        return {
             "cities": len(city_stats),
             ...
         }
```

**Test fix required (same PR):** Replace `@patch("scripts.bridge_oracle_to_calibration.DB_PATH")` with `@patch("src.state.db.get_forecasts_connection_with_world")` and return a mock context manager wrapping the existing `mock_db` fixture connection. Executor must rewrite the fixture accordingly — the `mock_db_path.__str__` trick is incompatible with the context-manager helper.

**LOC delta:** −4 production (remove DB_PATH, two conn.close(), sqlite3.connect), +3 (with block header, import). Test fixture rewrite ~10 LOC net change. Net: ~−1 production, ~±10 test.

**Karachi blast radius: ZERO.** Verified:
```
grep -rn "from scripts.bridge\|import bridge\|from scripts.evaluate_calibration" src/
# zero matches for bridge and evaluate_calibration
```

---

## §B F41 — evaluate_calibration_transfer_oos.py

### Current code (verbatim, lines 684–700)

```python
    from src.state.db import get_world_connection

    conn = get_world_connection(write_class="bulk")
    conn.execute("PRAGMA busy_timeout = 30000")
    logger.info("busy_timeout=30000ms set on connection")

    try:
        if not args.skip_lock_check:
            ok, msg = _check_daemon_down(conn)
            ...
        summary = run_oos_evaluation(
            conn,
```

**Table access audit:**
- READS: `calibration_pairs_v2` (forecast_class, on forecasts.db — currently 91M rows live, 0 in world)
- WRITES: `validated_calibration_transfers` (world_class, on world.db)

This is a cross-DB script. `get_forecasts_connection_with_world()` is the correct fix: forecasts.db as MAIN (resolves `calibration_pairs_v2` reads), world ATTACHed (resolves `validated_calibration_transfers` writes via `world.validated_calibration_transfers` qualification — executor must verify bare name vs qualified name for the INSERT).

### AFTER-state diff

```diff
-    from src.state.db import get_world_connection
+    from src.state.db import get_forecasts_connection_with_world

-    conn = get_world_connection(write_class="bulk")
-    conn.execute("PRAGMA busy_timeout = 30000")
-    logger.info("busy_timeout=30000ms set on connection")
-
-    try:
+    with get_forecasts_connection_with_world(write_class="bulk") as conn:
+        conn.execute("PRAGMA busy_timeout = 30000")
+        logger.info("busy_timeout=30000ms set on connection")
         if not args.skip_lock_check:
             ...
         summary = run_oos_evaluation(conn, ...)
         ...
-        return 0
-    finally:
-        conn.close()
+        return 0
```

**Executor must verify:** The `INSERT INTO validated_calibration_transfers` at line ~381 uses a bare table name. Under `get_forecasts_connection_with_world`, MAIN=forecasts; world tables resolve as `world.table_name`. If the INSERT is bare, add `world.` prefix. Grep: `grep -n "INSERT INTO validated_calibration_transfers" scripts/evaluate_calibration_transfer_oos.py`.

**F39 dependency:** If operator unloads the plist (F39-A), F41 fix is still correct to ship — script is then invoked manually for diagnostics only.

**LOC delta:** −4 (remove get_world_connection import, connect, busy_timeout, try/finally), +2 (with block). Net ~−2, plus potential INSERT qualification.

**Karachi blast radius: ZERO.** Same grep above; evaluate_calibration_transfer not imported by src/execution/.

---

## §C Joint antibody — tests/test_k1_reader_isolation.py

```python
# Created: 2026-05-17
# Authority: F40/F41 K1-reader regressions — prevent world-DB direct access for forecast_class tables
import ast, re, pytest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = list((REPO / "scripts").glob("*.py"))

FORECAST_CLASS_TABLES = {
    "observations", "settlements", "settlements_v2", "source_run",
    "job_run", "source_run_coverage", "readiness_state",
    "market_events_v2", "ensemble_snapshots_v2", "calibration_pairs_v2",
}
BAD_PATTERNS = [r'state/zeus-world\.db', r'get_world_connection']
ALLOWLIST = {"migrate_observations_k1.py"}  # v1: K1-split migration scripts only

@pytest.mark.parametrize("script", SCRIPTS)
def test_no_forecast_class_via_world_connection(script):
    if script.name in ALLOWLIST:
        pytest.skip(f"{script.name} is an allowlisted migration script")
    src = script.read_text()
    uses_bad = any(re.search(p, src) for p in BAD_PATTERNS)
    uses_forecast_table = any(t in src for t in FORECAST_CLASS_TABLES)
    assert not (uses_bad and uses_forecast_table), (
        f"{script.name}: uses world-DB access AND forecast_class table — "
        f"use get_forecasts_connection_with_world() instead"
    )
```

Initially mark `xfail` for bridge (F40) and evaluate_calibration_transfer (F41); flip to passing assertions as each PR lands.

---

## §D PR sequencing

| PR | Contents | Gate |
|----|----------|------|
| PR-X | `tests/test_k1_reader_isolation.py` (xfail for F40+F41) | Ships first; establishes antibody |
| PR-Y | F40 fix + test fixture rewrite | Flips F40 xfail; verify `data/oracle_error_rates.json` populated |
| PR-Z | F41 fix + INSERT qualification | Flips F41 xfail; priority reduced if F39-A plist unloaded |

§A and §B **cannot** ship in one PR: F41 requires executor-verify of INSERT qualification (unknown until executor reads line ~381), which may add a line not in this spec.

---

## §E Karachi ship safety

Import audit command and result:
```
grep -rn "from scripts.bridge_oracle\|from scripts.evaluate_calibration\|import bridge_oracle\|import evaluate_calibration" src/
# zero matches
```
Neither script is imported by `src/execution/` or any live-daemon module. F40 + F41 can ship **between cycle ticks** (interval ~10–30 min) without halting Karachi operations.

---

## §F Reviewer checklist

1. `DB_PATH` line 71 removed; no remaining `sqlite3.connect(str(DB_PATH))` in bridge script.
2. Both `conn.close()` calls (lines 167, 401) removed; no manual close anywhere in bridge.
3. Entire `bridge()` body (including early-exit return) is inside the `with` block.
4. Test fixture at line 53 patches `get_forecasts_connection_with_world`, not the removed `DB_PATH`.
5. F41 `INSERT INTO validated_calibration_transfers` uses `world.` prefix if bare name (executor must grep and confirm).
6. F41 `conn.close()` removed from `finally` block; context manager handles it.
7. Antibody `ALLOWLIST` names only confirmed migration scripts; no production readers exempted.
8. `calibration_pairs_v2` in antibody table set matches yaml (`forecast_class`, db: forecasts).
9. Blast-radius grep re-run on final diff confirms zero execution-cascade hits.
10. `observation_instants_v2` confirmed `world_class` — bridge reads it correctly via ATTACH; antibody table set omits it (not a misrouted forecast table).
