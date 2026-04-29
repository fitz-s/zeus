# Work Log — Slice K3.G10-helper-extraction

Created: 2026-04-26
Authority basis: con-nyx APPROVE_WITH_CONDITIONS on G10-scaffold (`dfd5fb0`).

## 2026-04-26 — slice opened + landed (single GREEN commit)

### Step 0: pre-recon (verify con-nyx empirical claims)

Direct subprocess audit confirmed:
- `daily_obs_tick`: pulls `src.signal` + `src.signal.diurnal` (transitive via `daily_obs_append`)
- `hourly_instants_tick`: same (via `hourly_instants_append`)
- `solar_daily_tick`, `forecasts_daily_tick`, `hole_scanner_tick`: clean

`python scripts/ingest/daily_obs_tick.py` direct invocation pre-fix → `ModuleNotFoundError: No module named 'src'`.

### Step 1: helper extraction (MAJOR #1)

- Created `src/contracts/dst_semantics.py` with `_is_missing_local_hour` as canonical home.
- `src/signal/diurnal.py`: replaced local definition with `from src.contracts.dst_semantics import _is_missing_local_hour` (re-export). Back-compat preserved — all 5 existing callers + 1 test continue to work unchanged.
- `src/data/daily_obs_append.py:73`: switched to `from src.contracts.dst_semantics import _is_missing_local_hour`. Eliminates transitive `src.signal` pull from ingest lane.
- `src/data/hourly_instants_append.py:50`: same switch.

Object-identity verified: `from src.signal.diurnal import _is_missing_local_hour as f1; from src.contracts.dst_semantics import _is_missing_local_hour as f2; assert f1 is f2` ✓.

### Step 2: sys.path shim (MAJOR #2)

Added 3-line bootstrap to each of 5 ticks (matches `scripts/live_smoke_test.py:23` convention):
```python
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

Inserted BEFORE any `from src.X` or `from scripts.ingest._shared` import, with `# noqa: E402` on the project imports to suppress the linter complaint about non-top-level imports.

Empirical: 4/5 ticks (`daily_obs`, `solar_daily`, `forecasts_daily`, `hole_scanner`) now resolve project imports cleanly under direct invocation (run fails at runtime with empty test DB sqlite OperationalError — the EXPECTED runtime error, NOT an import error). 5th (`hourly_instants`) imports clean too — runs longer because it actually fetches Open-Meteo data.

### Step 3: 3 new antibody tests

Added to `tests/test_ingest_isolation.py`:

6. `test_no_forbidden_transitive_imports_in_ingest` — for each tick, runs `python -c "import {tick}; report new sys.modules entries"` in a subprocess, asserts no entry matches `FORBIDDEN_IMPORT_PREFIXES`. Subprocess isolation prevents pytest's own import graph from polluting the audit.

7. `test_each_tick_script_self_bootstraps_syspath` — substring grep that each `*_tick.py` contains `sys.path.insert(0, str(Path(__file__).resolve().parents[2]))`. Conservative literal match — if a future tick uses an equivalent bootstrap (e.g., `sys.path[:0] = [...]`), this test would surface and require update.

8. `test_antibody_self_test_catches_synthetic_violation` — programmatically writes a fake tick with `from src.engine.cycle_runner import KNOWN_STRATEGIES` AND `from src.signal.diurnal import _is_missing_local_hour` to a tmp dir, runs `_collect_imports`, asserts BOTH violations are detected. Without this, "8/8 GREEN" doesn't prove the antibody actually fires on real violations — it only proves current files happen to be clean (con-nyx pattern feedback #12).

### Step 4: regression + close

- `tests/test_ingest_isolation.py`: 8/8 GREEN.
- `tests/test_obs_v2_dst_missing_hour_flag.py`: 6/6 GREEN (back-compat verified — uses re-export).
- Adjacent obs_v2 test files: all green.
- Regression panel (`test_architecture_contracts + test_live_safety_invariants + test_cross_module_invariants`): 5 pre-existing fails, delta=0.

### Notes for downstream

- **G10-cutover** (Wave 2) is now UNBLOCKED on the helper-extraction front. Still gated on operator + launchd plists + cycle proof.
- **G10-launchd-plists** is now UNBLOCKED — sys.path shim handles the bootstrap, plists can use `python /path/to/scripts/ingest/X_tick.py` directly.
- **NICE-TO-HAVE #1** (dynamic-imports detection), **#3** (suffix broadening), **#4** (calibration fence) deferred to followup.
