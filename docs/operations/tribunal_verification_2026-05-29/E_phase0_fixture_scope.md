# Phase 0 Fixture Scope — Before/After Validation Harness
# Created: 2026-05-29
# Authority: TRIBUNAL_DRAFT2_RESPONSE_2026-05-29.md §2c + direct DB query (read-only)
# Status: FEASIBILITY SCOPING ONLY — no DB writes, no capture executed

---

## 1. Current Serving Entry — Function the Fixture Capture Would Call

The production pipeline from snapshot to p_raw to p_cal runs in two distinct physical paths.

### 1a. Entry path (new positions at decision time)
Entry point: `src/execution/harvester.py` — decision loop at ~line 820 calls the forecast reader, then constructs `EnsembleSignal` and computes p_raw, then applies Platt calibration.

The function that a fixture-capture script must replicate:

```
read_executable_forecast(conn, city_id, city_name, city_timezone,
                         target_local_date, temperature_metric, source_id,
                         source_transport, data_version, track, strategy_key,
                         market_family, condition_id, decision_time)
    → ExecutableForecastBundleResult (snapshot + evidence)
```
File: `src/data/executable_forecast_reader.py:1099`

From that result, p_raw is computed via:
```
EnsembleSignal(members_hourly, times, city, target_d, ...).p_raw_vector(all_bins, n_mc=N)
  → delegates to p_raw_vector_from_maxes(member_maxes, ...) in src/signal/ensemble_signal.py:173
```

p_cal is then:
```
calibrate_and_normalize(p_raw_vector, cal, lead_days, bin_widths=[b.width for b in all_bins])
  → src/calibration/platt.py:317
```
where `cal` comes from `get_calibrator(conn, city, target_date, temperature_metric, ...)` in `src/calibration/manager.py:751`.

### 1b. Monitor path (existing positions, re-evaluation)
`src/engine/monitor_refresh.py:500-680` — same `p_raw_vector_from_maxes` + `calibrate_and_normalize` but uses live members from forecast reader result, not stored members_json.

### 1c. What is already stored in ensemble_snapshots
- `members_json`: 100% populated for eligible rows (all 6,167 joined rows) — sufficient to recompute p_raw without re-fetching ECMWF.
- `p_raw_json`: **sparsely populated** — only 248 of 6,167 joined rows (4%) have stored p_raw_json. P_raw is NOT a reliable stored artifact; it must be recomputed from members_json.
- `bin_grid_id`: populated on 6,167/6,167 of joined rows — bin grid recoverable for the replay.
- `source_cycle_time`, `lead_hours`, `settlement_unit`, `settlement_rounding_policy`: present on snapshot rows, sufficient to reproduce the exact bias+Platt bucket.

**Error-model path check (post-advisor query 2026-05-29)**: `provenance_json.error_model_family` is NULL/not-set on ALL 6,167 joined rows — 100% single-path. The `p_raw_vector_with_error_model()` branch (monitor_refresh.py:561) was NOT invoked for any OpenData mx2t3 row in the window. The capture script can call `p_raw_vector_from_maxes()` uniformly with no per-row branching needed. This is not guaranteed to hold after FT models are deployed to OpenData rows; fixture capture must re-verify this query at capture time.

**members_json format**: `json_array_length(members_json) = 51` uniformly across all 6,167 rows (51 = 50 perturbed members + 1 control, ECMWF ENS standard). Values in the sample row (13.89, 14.44, 15.52 °C …) are **daily calendar-day maxes in °C**, not hourly series. The daily-max extraction (incl. timezone-aware window selection) was already applied at ingest time by `scripts/ingest_grib_to_snapshots.py`. The recompute does NOT need to re-run `select_hours_for_target_date` — the stored floats are already the per-member daily maxes. This eliminates the timezone/DST extraction risk class noted in advisor review.

**Implication**: the fixture capture must recompute p_raw from stored members_json using `p_raw_vector_from_maxes`. This is deterministic given the same member values, bin grid, and n_mc seed (seed = sha256 of sorted member_maxes | n_mc | sigma | bin labels, per ensemble_signal.py:229). p_cal recomputed from the current live Platt model snapshot frozen at capture time.

---

## 2. Replay-Window Data Availability (Honest Count)

**Database**: `state/zeus-forecasts.db` (single DB — both snapshots and settlements live here)

Query criteria:
- `temperature_metric = 'high'`
- `data_version = 'ecmwf_opendata_mx2t3_local_calendar_day_max_v1'`
- `authority = 'VERIFIED'`, `causality_status = 'OK'`, `boundary_ambiguous = 0`
- Joined to `settlement_outcomes` on `(city, target_date, temperature_metric)` with `authority = 'VERIFIED'` and `settlement_value IS NOT NULL`

### Results

| Window | Distinct (city, target_date) pairs | Cities | Date range |
|--------|-----------------------------------|--------|------------|
| 90 days | **801** | 50 | 2026-05-06 – 2026-05-26 |
| 60 days | **801** | 50 | 2026-05-06 – 2026-05-26 |

Note: the 60-day and 90-day counts are identical because OpenData mx2t3 live ingest only began ~2026-05-06 (23 days of actual data as of 2026-05-29). The window is effectively **~21-23 days**, not 60-90.

**Raw snapshot rows** (before settlement join, same filters, 90d): 11,418 total. After joining to VERIFIED settlements: 6,167 rows across 801 distinct (city, target_date) pairs.

**Honest assessment**: 801 city-date pairs across 50 cities and ~3 weeks. This is a thin but real window.
- ~16 dates × 50 cities = ~800 pairs — consistent with ~1 snapshot per city-date once duplicates by lead/cycle are collapsed.
- **Effective independent n ≈ 801 settled outcomes** (upper bound). The 6,167 rows are autocorrelated within a target across lead horizons and correlated across cities sharing the same weather regime on a given date. For Phase-6 bootstrap LCB, the effective independent sample size is ~801 settled targets at most, realistically a few hundred. Do NOT read statistical power off the 6,167 row count.
- For proper-score OOS evaluation, 801 targets is enough for equivalence testing (Phase 5 analytic-vs-MC, the bar is |Δ|≤2e-4) but **too thin for improvement-mode LCB on fine-keyed buckets** (Phase 6). The Phase 6 settlement-OOS gate will need to accumulate more data before it can accept non-raw corrections on a per-bucket basis.
- Recommendation: use the **full available window** (2026-05-06 to settlement frontier, ~23 days) as the before-fixture snapshot. Do not artificially truncate to 60 days.

---

## 3. Settlement-Truth Join

**Location**: `state/zeus-forecasts.db`, table `settlements`.

Schema key fields:
- `city TEXT`, `target_date TEXT`, `temperature_metric TEXT` — join key to `ensemble_snapshots_v2`
- `settlement_value REAL` — the settled temperature observation
- `authority TEXT` — use `authority = 'VERIFIED'` only
- `unit TEXT`, `settlement_source_type TEXT`, `data_version TEXT` — provenance

**Is it joinable read-only?** Yes — both `ensemble_snapshots_v2` and `settlements` live in the same database (`zeus-forecasts.db`). A single SQLite connection with `PRAGMA query_only = ON` suffices. No cross-DB join required for this fixture.

**INV-37 note**: INV-37 (ATTACH + SAVEPOINT, never independent connections) applies to cross-DB *writes*. This fixture capture is read-only across a single DB — INV-37 does not apply. If a future step requires joining against `zeus-world.db` (e.g., for trade_decisions.p_calibrated), that join must use `ATTACH DATABASE` on a read-only connection to avoid violating INV-37's connection-isolation intent.

**zeus-world.db settlements**: zero rows in the VERIFIED/90d HIGH window. The authoritative settlement source is `zeus-forecasts.db`.

**Platt calibrator coverage for HIGH/OpenData (post-advisor query 2026-05-29)**:
`platt_models_v2` in both `zeus-forecasts.db` and `zeus-world.db` contains **only `tigge_mars` source models for HIGH** (137 models each, TIGGE pipeline only). Zero models exist for any OpenData / `ecmwf_opendata` source_id.

Consequence: `get_calibrator()` falls through to `IdentityCalibrator` for every one of the 801 city-date pairs in the window. **p_cal = p_raw for the entire OpenData mx2t3 HIGH population** — calibration is a no-op. This has two implications:
1. The equivalence test (Phase 5) for calibration is trivially satisfied (Δ = 0 by construction) — the calibration path is not being exercised for this metric/source combination.
2. The "before" fixture for p_cal is identical to p_raw. This is not a defect in the fixture; it accurately reflects current production state for this data_version. However, the tribunal should note explicitly that the calibration improvement gate (Phase 6) cannot be tested on OpenData rows until Platt models are fitted and deployed for this source.

---

## 4. Draft Capture Approach (NOT executed — design only)

### 4a. What to freeze

For each qualifying (city, target_date) pair:

| Field | Source | Notes |
|-------|--------|-------|
| `snapshot_id` | `ensemble_snapshots.snapshot_id` | primary key, immutable |
| `city`, `target_date`, `temperature_metric` | snapshot | join key |
| `members_json` | snapshot | raw member array, basis for p_raw recompute |
| `members_unit` | snapshot | degC for all current mx2t3 rows |
| `lead_hours` | snapshot | used for lead_bucket keying |
| `source_cycle_time` | snapshot | 00z / 12z cycle |
| `data_version` | snapshot | `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` |
| `bin_grid_id`, `bin_schema_version` | snapshot | recover exact bin grid |
| `settlement_unit`, `settlement_rounding_policy` | snapshot | critical for °C→°F + rounding preimage chain (§3d) |
| `p_raw_vector` | **recomputed** from members_json via `p_raw_vector_from_maxes` | deterministic given seed |
| `p_cal_vector` | **recomputed** from current live Platt model using `calibrate_and_normalize` | frozen at capture time |
| `platt_model_snapshot` | `load_platt_model_v2(conn, ...)` at capture time | freeze A, B, C, n_samples, input_space per bucket |
| `bias_applied` | from `ens_bias_repo.read_bias_model` at capture time | city × season delta_g applied |
| `settlement_value` | `settlements.settlement_value` | observed truth |
| `winning_bin` | `settlements.winning_bin` | outcome label |
| `n_mc_seed` | sha256(sorted_members | n_mc | sigma | bins) | reproducibility anchor |

### 4b. Capture approach

```python
# DRAFT — not executed
# Script: scripts/capture_before_fixture.py
# Created: 2026-05-29
# Authority: E_phase0_fixture_scope.md §4
#
# 1. Open zeus-forecasts.db read-only (PRAGMA query_only = ON)
# 2. Query the eligible join set (SQL from §2 above)
# 3. For each row:
#    a. Deserialize members_json → np.ndarray
#    b. Recover bin grid from bin_grid_id (bins_from_grid_id or stored bin_schema_version)
#    c. Call p_raw_vector_from_maxes(member_maxes, city, semantics, all_bins, n_mc=N)
#       — record the deterministic seed (sha256 per ensemble_signal.py:229)
#    d. Call get_calibrator(conn, city, target_date, temperature_metric, ...) 
#       → freeze model params (A, B, C, n_samples) in fixture row
#    e. Call calibrate_and_normalize(p_raw_vector, cal, lead_days, bin_widths)
#       → p_cal_vector
#    f. Read bias_model via ens_bias_repo.read_bias_model at capture time
# 4. Write fixture to: docs/operations/before_after_fixture_2026-05-29/
#    - fixture.parquet (all rows, all fields above)
#    - fixture_meta.json (capture_ts, code_commit, n_rows, platt_model_hashes by bucket)
# 5. Compute immutability checksum:
#    sha256(sorted fixture.parquet bytes) → fixture_meta.json::checksum
#    sha256 per row of (snapshot_id | p_raw_vector | p_cal_vector | settlement_value)
#      → stored in fixture as row_checksum column
# 6. Do NOT write to any DB table. All output is files only.
```

### 4c. Immutability scheme

- Fixture is a single Parquet file + JSON metadata, committed to the repo under `docs/operations/before_after_fixture_2026-05-29/`.
- `fixture_meta.json` contains: `capture_ts`, `code_commit` (git SHA at capture), `n_rows`, `settlement_frontier` (max target_date in fixture), `file_sha256` (checksum of fixture.parquet), `platt_model_hashes` per bucket (so any model drift is detectable).
- Row-level `row_checksum` = sha256(snapshot_id | members_json | p_raw_vector_json | p_cal_vector_json | settlement_value) — allows per-row tamper detection.
- Fixture file is never overwritten; each subsequent capture (if any) creates a new dated directory.

### 4d. INV-37 compliance

The capture script uses a single `zeus-forecasts.db` connection in read-only mode. No ATTACH, no writes, no cross-DB transaction. INV-37 (ATTACH+SAVEPOINT) applies to the write path; the fixture capture is pure read + file write. No violation.

If the harness later needs `trade_decisions.p_calibrated` from `zeus-world.db` for comparison, open a second independent read-only connection (no write path, no SAVEPOINT) — the INV-37 constraint on ATTACH applies specifically to write transactions spanning both DBs.

---

## 5. Key Risks and Caveats

1. **p_raw_json sparsity (4% coverage)**: p_raw cannot be assumed pre-stored. The fixture must recompute from members_json. This is deterministic but requires the same n_mc and sigma_instrument values used at original serve time — these must be frozen from `settings.json` at capture commit, not read dynamically.

2. **Platt model coverage: zero for OpenData HIGH** — `platt_models_v2` has only `tigge_mars` models. All 801 OpenData pairs serve p_cal = p_raw via IdentityCalibrator. The Phase 5 calibration equivalence test is trivially satisfied; Phase 6 calibration improvement test is not yet exercisable on this source. Platt model drift risk from a refit is zero for OpenData rows (no model to refit). Document this in fixture_meta.json.

3. **Thin window (~23 days)**: sufficient for equivalence testing (Phase 5) but not for LCB improvement-mode gates (Phase 6). Phase 6 gates must either wait for more data to accumulate or relax to a wider window including TIGGE rows (which have ~800 pairs/day going back years, but are a different pipeline and therefore not suitable for the "before" fixture of the OpenData path).

4. **Settlement completeness**: 801 city-date pairs with VERIFIED settlements — but the max target_date in the snapshot set is 2026-05-26, so some recent target_dates (05-27 to 05-29) likely lack settlements yet. The fixture should be captured against the settled frontier only (`target_date <= <settlement_frontier>`).

5. **bin_grid_id recovery**: `bin_grid_id` is populated on all joined rows but the recovery function (`bins_from_grid_id` or equivalent) must be audited before the capture script runs to confirm it returns the identical bin objects used at serve time.
