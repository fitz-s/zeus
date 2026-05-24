# City Onboarding Repoint Plan — 2026-05-24
# Authority: scripts/onboard_cities.py PIPELINE_STEPS (the operator's pipeline)
# Target cities: Jinan (ZSJN) + Zhengzhou (ZHCC)
# Created: 2026-05-24

## Protocol Authority

The operator's pipeline is `scripts/onboard_cities.py::PIPELINE_STEPS` (14 steps, 0-indexed).
`docs/operations/city_onboarding_protocol.md` does NOT exist on main — it was agent-authored
on a failed prior branch. Step numbers in the task brief map to PIPELINE_STEPS array positions.

## Stale Steps: Verdicts + Repoints

### Step 2 — market_events (`discover_market_events` in `onboard_cities.py:375`)
**STALE — REPOINT**
- Current: `INSERT INTO market_events` via `get_world_connection()` → world.db (0 rows)
- Canonical: `market_events_v2` in forecasts.db (14,924 rows)
- `market_events_v2` adds one column: `temperature_metric TEXT NOT NULL CHECK(IN('high','low'))`
- `find_weather_markets()` already returns `temperature_metric` per event (line 1650)
- Fix: use `get_forecasts_connection(write_class="bulk")` + INSERT into `market_events_v2` with `temperature_metric` field

Changes to `scripts/onboard_cities.py`:
1. Line 388: `get_world_connection(write_class="bulk")` → `get_forecasts_connection(write_class="bulk")`
2. Line 386: add import `from src.state.db import get_forecasts_connection`
3. Line 413: INSERT statement: table `market_events` → `market_events_v2`, add `temperature_metric` column (source: `event.get("temperature_metric", "high")`)
4. `_verification_tables()` (line 716): `"market_events"` → `"market_events_v2"` (note: world.db vs forecasts.db — verification query needs per-DB routing; see below)

### Step 8 — diurnal_curves (`etl_diurnal_curves.py`)
**CURRENT — NO CHANGE**
Reads `observation_instants_current` VIEW in world.db. VIEW wraps `observation_instants_v2`
(1,852,291 rows). No repoint needed.

### Step 10 — forecast_skill (`etl_forecast_skill_from_forecasts.py`)
**CURRENT — NO CHANGE**
Writes to `forecast_skill` in world.db (23,590 rows). This step replaced the older
`etl_historical_forecasts.py` as the live skill ETL.

### Step 11 — historical_forecasts (`etl_historical_forecasts.py`)
**VESTIGIAL — SKIP**
- Writes to `historical_forecasts` (0 rows, empty table in world.db)
- Calls `_compute_model_skill()` which does `DELETE FROM model_skill` — `model_skill` table
  does NOT exist in either world.db or forecasts.db → will raise OperationalError
- Successor: `etl_forecast_skill_from_forecasts.py` (step 10, CURRENT, 23,590 rows)
- The script predates the K1 split and the forecast_skill replacement
- Verdict: mark `optional=True` in PIPELINE_STEPS, or skip via `--start-from` past it
- Fix in `onboard_cities.py`: add `"optional": True` to the `historical_forecasts` step dict
  (line ~276), so step failure is logged but doesn't abort pipeline

### Step 13 — ens_backfill (`backfill_ens.py`)
**VESTIGIAL/BLOCKED — SKIP (optional=True), DISCLOSE IN REPORT**
- `get_settlements_in_window()` line 42: LEFT JOINs `ensemble_snapshots` (unsuffixed, doesn't exist)
- `get_bin_structure()` line 61: SELECT from `market_events` (unsuffixed, 0 rows in world.db)
- INSERT line 131: `ensemble_snapshots` (unsuffixed, doesn't exist)
- `ensemble_snapshots_v2` has ~40 columns; many NOT NULL without defaults:
  `temperature_metric`, `physical_quantity`, `observation_field`, `members_unit`,
  `ingest_backend`. This is NOT a table rename — the INSERT shape is fundamentally different.
  Rewriting backfill_ens.py to match the v2 schema is out-of-scope; it would require
  replicating the ECMWF Open Data ingest pipeline logic.
- API constraint: OpenMeteo `past_days` max = 93 days (script only covers 93-day window).
- **Canonical write path**: the live daemon (`src/main.py` via `src/ingest/`) writes to
  `ensemble_snapshots_v2` for all cities in `cities_by_name` on each cycle. New cities
  will accumulate rows after the next operator-initiated daemon restart.
- Fix: add `"optional": True` to step 13 in PIPELINE_STEPS so failure is logged not fatal.
  (Step 14 already has optional=True.)
- **Report to operator**: ensemble_snapshots_v2 rows for ZSJN/ZHCC will be 0 at end of run.
  This is correct fail-closed cold-start behavior. Will populate after daemon restart.

### Step 14 — calibration_pairs (`rebuild_calibration_pairs_canonical.py`)
**STALE + OPTIONAL — MARK SKIP, DISCLOSE IN REPORT**
Current PIPELINE_STEPS: `"extra_args": ["--dry-run"]` + `"optional": True`.
With `--dry-run`, reads `ensemble_snapshots` (unsuffixed, doesn't exist) at line 170.
Will OperationalError. Since ensemble_snapshots_v2 will be empty for new cities anyway
(see step 13), calibration_pairs_v2 will also be 0. Already optional — no change needed.
Disclose in report: calibration_pairs_v2 will be 0 for ZSJN/ZHCC (cold-start, expected).

### `_verification_tables()` (`onboard_cities.py:711`)
**STALE — REPOINT ALL**

Current stale entries → canonical replacements:
| Old (unsuffixed, mixed DBs) | Canonical | DB |
|---|---|---|
| `"settlements"` | `"settlements_v2"` | forecasts.db |
| `"observations"` | `"observations"` | world.db (unchanged) |
| `"observation_instants"` | `"observation_instants_v2"` | world.db |
| `"market_events"` | `"market_events_v2"` | forecasts.db |
| `"ensemble_snapshots"` | `"ensemble_snapshots_v2"` | forecasts.db |
| `"calibration_pairs"` | `"calibration_pairs_v2"` | forecasts.db |
| `"historical_forecasts"` | VESTIGIAL — remove | — |
| `"model_skill"` | VESTIGIAL — remove | — |

Note: current `_run_verification()` queries all tables via a single `get_world_connection()`.
After repoint, tables in forecasts.db need a separate connection. This requires refactoring
the verification function to route by DB class.

## Cities to Add to config/cities.json

From prior branch `feat/onboard-jinan-zhengzhou` (agent-authored params, verified against
WU station IDs from Polymarket market data):

Jinan:
- name: "Jinan", lat: 36.8572, lon: 117.0560, wu_station: "ZSJN"
- timezone: "Asia/Shanghai", unit: "C"
- settlement_source: "https://www.wunderground.com/history/daily/cn/jinan/ZSJN"
- historical_peak_hour: 15.0, diurnal_amplitude_c: 10.0
- country_code: "CN", cluster: "Asia-East-China"

Zhengzhou:
- name: "Zhengzhou", lat: 34.5197, lon: 113.8408, wu_station: "ZHCC"
- timezone: "Asia/Shanghai", unit: "C"
- settlement_source: "https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC"
- historical_peak_hour: 15.0, diurnal_amplitude_c: 10.0
- country_code: "CN", cluster: "Asia-East-China"

## Test Update Required

`tests/test_cities_config_authoritative.py:27`: `== 51` → `== 54` (52 current + 2 new cities)
(current count is 52 — Qingdao was added without updating the test; test is currently FAILING)

## 12z/00z ECMWF Cycle Note

The live daemon (`src/ingest/forecast_live_daemon.py`) already has separate 00Z and 12Z
job IDs for ECMWF Open Data. `backfill_openmeteo_previous_runs.py` uses the `ecmwf_ifs025`
model via OpenMeteo's previous_runs endpoint — no cycle distinction required for backfill.
No changes needed to backfill script for this item.

## Pipeline Run Plan (post-validation)

Run from: `/Users/leofitz/.openclaw/workspace-venus/zeus` (live checkout)
STATE_DIR must resolve to: `/Users/leofitz/.openclaw/workspace-venus/zeus/state`

```
cd /Users/leofitz/.openclaw/workspace-venus/zeus
source .venv/bin/activate
python scripts/onboard_cities.py --cities "Jinan" "Zhengzhou" --start-from config
```

Steps will execute in PIPELINE_STEPS order. Step 11 (historical_forecasts) to be skipped
via `optional=True` flag. Step 13 (ens_backfill) limited to 93-day window per API constraint.

## Expected Row Counts Post-Run

- `settlements_v2` (forecasts.db): +new settlement scaffold rows for ~90 days × 2 cities
- `market_events_v2` (forecasts.db): +current Polymarket markets for ZSJN/ZHCC if live
- `observation_instants_v2` (world.db): +hourly obs rows for 900 days × 2 cities
- `ensemble_snapshots_v2` (forecasts.db): +ENS rows for 93-day window × 2 cities (API limit)
- `platt_models_v2` (forecasts.db): 0 — requires calibration_pairs_v2 which requires
  sufficient ensemble history. Cold-start behavior is correct fail-closed. Will populate
  after sufficient live trading history.
