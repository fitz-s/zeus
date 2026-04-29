# Work Log — Slice K3.G10-scaffold

Created: 2026-04-26
Authority basis: `plan.md`, `scope.yaml`.

## 2026-04-26 — slice opened + landed

### Step 0: scaffold + plan
- Wrote plan.md + scope.yaml.
- Pre-recon confirmed:
  - 5 tick functions in `src/main.py:111-211` (_k2_daily_obs_tick, _k2_hourly_instants_tick, _k2_solar_daily_tick, _k2_forecasts_daily_tick, _k2_hole_scanner_tick) + `_ecmwf_open_data_cycle` at L237.
  - Underlying ETL functions (`daily_tick`, `hourly_tick`, etc.) are clean — no forbidden imports — so they can be safely called from the new ingest lane.
  - `scripts/ingest/` does not yet exist.

### Step 1: file creation (single GREEN commit, no RED phase)
9 new files:
- `scripts/ingest/__init__.py` — package marker + isolation contract documentation.
- `scripts/ingest/_shared.py` — `setup_tick_logging`, `world_connection` context manager, `run_tick` wrapper.
- `scripts/ingest/daily_obs_tick.py` — calls `daily_obs_append.daily_tick`.
- `scripts/ingest/hourly_instants_tick.py` — calls `hourly_instants_append.hourly_tick`.
- `scripts/ingest/solar_daily_tick.py` — calls `solar_append.daily_tick`.
- `scripts/ingest/forecasts_daily_tick.py` — calls `forecasts_append.daily_tick`.
- `scripts/ingest/hole_scanner_tick.py` — instantiates `HoleScanner` + calls `scan_all`.
- `tests/test_ingest_isolation.py` — 5 tests (directory shape + AST-walk forbidden-import enforcement + main-callable + lifecycle headers).
- packet body: plan/scope/work_log/receipt.

RED phase intentionally skipped: this slice is structural decoupling + antibody — production code must be correct from first commit (a tick with a forbidden import would just be a bug, not a meaningful RED). The antibody catches mistakes pre-commit if any.

### Step 2: empirical smoke test
Verified each tick imports cleanly via the venv:
```
from scripts.ingest.daily_obs_tick import main         → imported OK
from scripts.ingest.hourly_instants_tick import main   → imported OK
from scripts.ingest.solar_daily_tick import main       → imported OK
from scripts.ingest.forecasts_daily_tick import main   → imported OK
from scripts.ingest.hole_scanner_tick import main      → imported OK
```

Did NOT execute the ticks against production DB — that's an operator-controlled action.

### Step 3: regression + close
- `pytest tests/test_ingest_isolation.py` — 5/5 GREEN.
- Regression panel + adjacent test files: 5 pre-existing fails, delta=0.
- Registered `tests/test_ingest_isolation.py` in `architecture/test_topology.yaml`.
- Registered 6 new scripts in `architecture/script_manifest.yaml` (5 ticks + _shared.py).
- Wrote receipt.json.

### Notes for downstream

- **G10-cutover** (Wave 2) — removing `_k2_*_tick` from `src/main.py` scheduler. Requires operator decision + at least one launchd cycle proving scheduled execution works.
- **G10-source-split** (followup) — workbook lists 8 finer-grained ticks (separate WU/HKO/Ogimet ticks). Requires refactoring `daily_obs_append.daily_tick` to accept a source filter.
- **G10-launchd-plists** (followup) — 8 `.plist` files for launchd registration. Operator deploy concern.
- **ECMWF open-ens cycle** (followup) — separate cadence, not yet scaffolded.

The antibody (`tests/test_ingest_isolation.py`) is THE load-bearing artifact. As long as it stays green, the ingest lane stays decoupled.