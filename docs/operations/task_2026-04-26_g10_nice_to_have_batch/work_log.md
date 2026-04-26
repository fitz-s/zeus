# Work Log — Slice K3.G10-nice-to-have-batch

Created: 2026-04-26
Authority basis: con-nyx G10-helper-extraction APPROVE verdict + 4 NICE-TO-HAVE residuals.

## 2026-04-26 — slice opened + landed (single GREEN commit)

### Step 0: pre-recon

- 7 callers of season helpers across 5 modules. Only `src/data/daily_obs_append.py` is in the ingest lane; rest are calibration-allowed.
- season helpers are pure calendar code (no calibration dependencies). Safe extraction candidate.
- AST `_collect_imports` only sees statement-level Import/ImportFrom; misses Call-node `__import__()` + `importlib.import_module()` patterns.

### Step 1: G10-calibration-fence

- Created `src/contracts/season.py` with `season_from_date`, `season_from_month`, `hemisphere_for_lat`, `_SH_FLIP`. Pure functions, no I/O, no DB.
- `src/calibration/manager.py:43-80` replaced with re-export block. 5 existing callers continue working unchanged.
- `src/data/daily_obs_append.py:69` switched to canonical `from src.contracts.season import season_from_date`.
- Added `src.calibration` to `FORBIDDEN_IMPORT_PREFIXES` in `tests/test_ingest_isolation.py`.

Object identity verified: `from src.calibration.manager import season_from_date as s1; from src.contracts.season import season_from_date as s2; assert s1 is s2` ✓.

### Step 2: G10-tick-suffix-broadening

- `test_each_tick_script_has_main_callable` filter changed from `endswith("_tick.py")` to `name not in {"_shared.py", "__init__.py"}`. Future `daily_runner.py` etc. now caught.

### Step 3: G10-AST-walk-dynamic-imports

- Added `_collect_dynamic_imports()` helper that walks `ast.Call` nodes for two patterns:
  - `__import__("module.name")` (ast.Name with id=="__import__", first arg ast.Constant str)
  - `importlib.import_module("module.name")` (ast.Attribute with attr=="import_module" + value ast.Name id=="importlib", first arg ast.Constant str)
- Added `test_no_forbidden_dynamic_imports_in_ingest` antibody applying same forbidden-prefix check.
- Non-string first args (`__import__(some_var)`) ignored — un-auditable statically; out-of-scope.

### Step 4: empirical verification

5/5 ticks subprocess audit clean post-fix (with `src.calibration` newly in forbidden):
```
daily_obs_tick:       24 src.* modules — FORBIDDEN: []
hourly_instants_tick: 24 src.* modules — FORBIDDEN: []
solar_daily_tick:     10 src.* modules — FORBIDDEN: []
forecasts_daily_tick: 10 src.* modules — FORBIDDEN: []
hole_scanner_tick:     8 src.* modules — FORBIDDEN: []
```

### Step 5: regression panel

- `test_ingest_isolation.py`: 9/9 GREEN (was 8; +1 dynamic-imports)
- Adjacent panel (calibration_bins_canonical, obs_v2_dst, hk_settlement_floor_rounding, plus regression panel): 5 pre-existing fails, delta=0.

### Notes for downstream

- **G10-source-split** (workbook 8-script split): per con-nyx Ask #6, defer the `daily_tick(conn, source: str | None = None)` API refactor to the source-split slice itself. Don't pre-build. Wave 2.
- **G10-cutover** (Wave 2): now unblocked on the helper-extraction + nice-to-have-batch front. Still gated on operator + launchd plists + cycle proof.
