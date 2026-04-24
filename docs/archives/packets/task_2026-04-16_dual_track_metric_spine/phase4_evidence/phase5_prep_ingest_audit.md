# Phase 5 Prep Ingest Audit

Author: exec-emma (sonnet, fresh — Phase 5 future-owner)
Date: 2026-04-16
Status: Read-only audit. No code changes.

All file:line citations disk-verified against current branch (`data-improve`).

---

## 1. Low-Track Symmetry Breakpoints in `scripts/ingest_grib_to_snapshots.py`

### Entry anchor: `NotImplementedError` at `ingest_grib_to_snapshots.py:253–257`

```python
if track == "mn2t6_low":
    raise NotImplementedError(
        "Phase 5 scope — mn2t6_low track requires boundary quarantine logic "
        "not yet implemented. Use track='mx2t6_high' for Phase 4B ingest."
    )
```

This is the intended Phase 5 entry point. Removing the guard and enabling the
`mn2t6_low` branch is the first action when Phase 5 opens. The surrounding
`_TRACK_CONFIGS` dict already wires `LOW_LOCALDAY_MIN` and the correct JSON
subdir (`tigge_ecmwf_ens_mn2t6_localday_min`) at lines 53–57, so the config
is already symmetric.

### Breakpoints that are already symmetric (no change needed)

- `ingest_grib_to_snapshots.py:34–41`: imports include `LOW_LOCALDAY_MIN` and
  all provenance contracts. Both identities are imported.
- `ingest_grib_to_snapshots.py:47–57`: `_TRACK_CONFIGS` dict has a
  `"mn2t6_low"` entry with correct `MetricIdentity` and JSON subdir.
- `ingest_grib_to_snapshots.py:106–119`: `_extract_causality_status` reads
  the `causality` dict from JSON with `status` defaulting to `"OK"`. The
  low-track extractor will emit `"N/A_CAUSAL_DAY_ALREADY_STARTED"` which
  passes through the allowed-set check at line 112 (`allowed` frozenset).
  **No change needed** here — the infrastructure is Phase-5-ready.
- `ingest_grib_to_snapshots.py:123–130`: `_extract_boundary_fields` reads
  `boundary_policy.boundary_ambiguous` from JSON. Low-track JSON will
  populate this field for real; high-track defaults to `0`. No change needed.
- `ingest_grib_to_snapshots.py:148–238`: `ingest_json_file` is fully
  symmetric — it consumes `metric: MetricIdentity` as a parameter and never
  hard-codes `high`. All DB writes use `metric.temperature_metric`,
  `metric.physical_quantity`, `metric.observation_field`.

### The one structural gap for Phase 5

The `ingest_track` function (lines 241–321) has the `NotImplementedError`
guard but no other asymmetry once the guard is removed. The boundary quarantine
logic the error message refers to (`boundary_ambiguous` determination) lives in
the **extractor** (`extract_tigge_mn2t6_localday_min.py`, to be created in
Phase 5), not in the ingestor. The ingestor merely reads `boundary_policy` from
the pre-extracted JSON. So the ingestor flip for Phase 5 is:

- `ingest_grib_to_snapshots.py:253–257`: Remove the `NotImplementedError`
  block. That is the only required code change to this file for low-track
  ingest.

---

## 2. Low-Track Symmetry in `rebuild_calibration_pairs_v2.py` and `refit_platt_v2.py`

### 2a. `ensemble_snapshots_v2` — no `source` column (still true)

`exec-carol` confirmed 27 columns, no `source`. Verified:
`rebuild_calibration_pairs_v2.py:184` sets `source = ""` explicitly with an
inline comment: "ensemble_snapshots_v2 has no source column; INV-15 gates on
data_version prefix." This is correct and the low rebuild must do the same.

### 2b. `rebuild_calibration_pairs_v2.py` breakpoints for low

**`_fetch_eligible_snapshots_v2` (lines 112–136)**: Hard-codes
`temperature_metric = 'high'` in the WHERE clause at line 119. For low rebuild,
this must become `temperature_metric = 'low'`.

**`_fetch_verified_observation` (lines 139–155)**: Hard-codes `high_temp` at
lines 147–150. For low rebuild this must become `low_temp`. This is the
critical observation-field swap. `LOW_LOCALDAY_MIN.observation_field` is
`'low_temp'` (verified at `src/types/metric_identity.py:88`).

**`_process_snapshot_v2` (lines 172–273)**:
- Line 209: `float(obs["high_temp"])` — must become `float(obs["low_temp"])`.
- Line 259: `metric_identity=HIGH_LOCALDAY_MAX` — must become
  `metric_identity=LOW_LOCALDAY_MIN`.
- Line 224: `p_raw_vector_from_maxes(...)` — semantically still valid for low
  (it runs MC + noise + rounding over member values). The member values for
  low are per-member calendar-day minimums, not maximums. The function name
  is misleading but the math path is the same. No new function needed for v1;
  document the naming discrepancy in a comment.

**`rebuild_v2` (lines 276–417)**:
- Lines 293–299 (print banner): Hard-codes `"high track"` and `HIGH_LOCALDAY_MAX`.
  Update to use the passed-in identity.
- Line 301: `_fetch_eligible_snapshots_v2(conn, city_filter=city_filter)` —
  the inner WHERE must use the passed-in `temperature_metric`.
- The script currently takes no `metric_identity` argument. Phase 5 will need
  either a `--track` CLI argument (mirroring the ingestor pattern) or a
  separate `rebuild_calibration_pairs_low_v2.py` script.

**Pattern recommendation**: Add a `--track` argument with choices
`["mx2t6_high", "mn2t6_low"]` exactly as the ingestor does. Reuse
`_TRACK_CONFIGS` from the ingestor (or duplicate the config dict locally).
This avoids a parallel-file maintenance burden.

### 2c. `refit_platt_v2.py` breakpoints for low

**`_fetch_buckets` (lines 81–95)**: Hard-codes `temperature_metric = 'high'`
at line 84. Must become `temperature_metric = 'low'` for low refit.

**`_fetch_pairs_for_bucket` (lines 98–114)**: Hard-codes
`temperature_metric = 'high'` at line 107. Same change needed.

**`_fit_bucket` (lines 117–203)**:
- Line 128: `bucket_key = f"high:{cluster}:..."` — must become
  `f"low:{cluster}:..."`.
- Lines 175 and 185: `metric_identity=HIGH_LOCALDAY_MAX` passed to
  `deactivate_model_v2` and `save_platt_model_v2`. Must become
  `LOW_LOCALDAY_MIN`.

**`refit_v2` (lines 206–280)**:
- Line 218: print banner hard-codes `HIGH_LOCALDAY_MAX`. Update for low.
- Same pattern: add `--track` CLI argument or separate script.

### 2d. `LOW_LOCALDAY_MIN` in `src/types/metric_identity.py` — verified

At lines 85–90:

```python
LOW_LOCALDAY_MIN = MetricIdentity(
    temperature_metric="low",
    physical_quantity="mn2t6_local_calendar_day_min",
    observation_field="low_temp",
    data_version="tigge_mn2t6_local_calendar_day_min_v1",
)
```

`observation_field='low_temp'` is correct per carol's dump. `data_version`
matches the exact tag from the remediation plan §7. No change needed here.

### 2e. `QUARANTINED_DATA_VERSIONS` — low tag is NOT in the quarantine set

Verified at `src/contracts/ensemble_snapshot_provenance.py:75–81`:
`"tigge_mn2t6_local_calendar_day_min_v1"` does not appear in
`QUARANTINED_DATA_VERSIONS` or `QUARANTINED_DATA_VERSION_PREFIXES`.
The prefix `"tigge_mn2t6_local_peak_window"` is also absent (that tag was
never produced for low). The low data_version tag is clear to enter the
pipeline. **No adjustment to `QUARANTINED_DATA_VERSIONS` needed for Phase 5.**

---

## 3. Day0 Causality N/A — The Hard Edge

### Where "already started" detection lives

The causality N/A check is NOT currently in the evaluator's Day0 code path.
It is pre-computed in the **extractor** (to be written in Phase 5 as
`extract_tigge_mn2t6_localday_min.py`) and written into each JSON file's
`causality.status` field. The ingestor stores this as `causality_status` in
`ensemble_snapshots_v2`. The rebuild script gates on `causality_status='OK'`
in its WHERE clause (`rebuild_calibration_pairs_v2.py:121–122`), so
`N/A_CAUSAL_DAY_ALREADY_STARTED` slots are already structurally excluded from
calibration training.

The Phase 6 seam — where the **runtime evaluator** must detect Day0 low and
refuse the historical Platt path — lives at:

- `src/engine/evaluator.py:814–833`: `Day0Signal` construction. At line 85–91
  of `src/signal/day0_signal.py`, `Day0Signal.__init__` raises
  `NotImplementedError` for any `LOW_LOCALDAY_MIN` metric. This is the Phase 6
  guard. The `Day0LowNowcastSignal` class that replaces it must be created in
  Phase 6 and wired in at `evaluator.py:814`.

- The "already started" runtime detection would live in the `Day0LowNowcastSignal`
  class itself — it computes `pure_forecast_valid = issue_utc <= local_day_start_utc`
  at construction time and raises or returns a "N/A" signal rather than
  consulting the historical Platt model. This is Phase 6 scope. The correct
  file for the new class: `src/signal/day0_signal.py` (add alongside
  `Day0Signal`) or a new `src/signal/day0_low_signal.py`.

**For Phase 5 recon**: no runtime code changes are needed. Phase 5 only
populates training data. The Phase 6 seam attach point is
`evaluator.py:814` (currently raises `NotImplementedError` for low via
`Day0Signal.__init__`) and the corresponding class would live in
`src/signal/day0_signal.py:85–91`.

---

## 4. Boundary Quarantine Rate Monitoring

### Where it would land

The remediation plan §6 and §8 require per-city `quarantine_rate` reporting
with `WARN_HIGH_QUARANTINE_RATE` when `quarantine_rate > 0.20`.

The natural home for this is a **standalone coverage scanner script**,
mirroring the high-track pattern:

- Existing high-track model: none yet (Phase 4.5 introduces the first
  standalone coverage scanner via `scan_tigge_mx2t6_localday_coverage.py`
  per the remediation plan's recommended scripts).
- Low-track equivalent: `scripts/scan_tigge_mn2t6_localday_coverage.py`
  (named in the remediation plan §3).

This script reads the extracted low-track JSON files (not the DB) and emits
the coverage states (`OK`, `MISSING_EXTRACT`, `N/A_CAUSAL_DAY_ALREADY_STARTED`,
`REJECTED_BOUNDARY_AMBIGUOUS`, etc.) plus the per-city quarantine table. It
does NOT write to the DB.

**No existing dashboard pattern in `src/analysis/`** to mirror —
`src/analysis/__init__.py` is the only file there. The existing
`scripts/venus_sensing_report.py` and `scripts/antibody_scan.py` are the
closest operational-report patterns. The scanner should follow their pattern:
standalone script, JSON + console output, no DB write. Output could land in
`tmp/tigge_mn2t6_coverage_<date>.json` (per plan §3) and a summary table
printed to stdout.

**For Phase 5**: write `scripts/scan_tigge_mn2t6_localday_coverage.py` as a
companion to the extractor. Run it after every extraction batch, before
rebuild, to confirm quarantine rates are not above the 20% alarm threshold
for any city.

---

## 5. Forward Hazards — Top 3

### Hazard 1: `low_temp` observation backfill gaps will breach the 30% abort gate

`rebuild_calibration_pairs_v2.py:381–389` aborts if more than 30% of eligible
snapshots have no matching `VERIFIED` observation. The high track has 42,504
observation rows (per exec-carol Phase 3 context). The low track's `low_temp`
column must be backfilled equivalently. If it is not, the rebuild will abort
immediately with a `snapshots_no_observation` ratio far above 30%.

**Pre-check before Phase 5 opens**:

```sql
SELECT COUNT(*) FROM observations
WHERE low_temp IS NOT NULL AND authority = 'VERIFIED';
```

Compare against the expected slot count for the low archive date range
(2024-01-01..2025-09-24). If the ratio is above 30% missing, fix the
backfill (`backfill_wu_daily_all.py` or equivalent) before starting Phase 5
rebuild. Do not start Phase 5 rebuild blind.

### Hazard 2: High quarantine rates will block cities from reaching calibration maturity

The remediation plan §6 and §12 warn that some cities' local sunrise hours
resonate with UTC six-hour GRIB bucket boundaries, causing Tmin to frequently
land in a `boundary_ambiguous` bucket. A quarantine rate above 20% is possible
for certain cities. `MIN_DECISION_GROUPS` is `calibration_maturity_thresholds()[2]`
(`refit_platt_v2.py:60`); if a city's quarantine rate is 40%, it may lose
enough training rows to fall below the maturity gate and produce no Platt model.

**Mitigation**: Run the coverage scanner on the Phase 5 pilot cities before
full extraction. Surface the per-city quarantine table. Cities with
`quarantine_rate > 0.20` need explicit human sign-off before Phase 5 proceeds.
Do not launch a full 420-file extraction equivalent for low without this check.

### Hazard 3: `data_version` tag mismatch between extractor output and `LOW_LOCALDAY_MIN`

`LOW_LOCALDAY_MIN.data_version` is `"tigge_mn2t6_local_calendar_day_min_v1"`
(disk-verified at `src/types/metric_identity.py:89`). The extractor must emit
exactly this string in its JSON `data_version` field; the ingestor passes it
through `assert_data_version_allowed`, and the rebuild gates on it in the
eligibility WHERE clause. A single character difference (e.g., `_min_` vs
`_localmin_` or `_v2` vs `_v1`) causes all rows to be quarantined or to fail
the ingest guard.

**Mitigation**: Before running the Phase 5 extractor at scale, test one JSON
file through `ingest_json_file` in a temp DB and verify the `data_version`
written matches `LOW_LOCALDAY_MIN.data_version` exactly. This is a 5-minute
smoke test that prevents a complete wasted extraction run.

---

## Summary

| Area | Status | Phase 5 delta |
|---|---|---|
| `ingest_grib_to_snapshots.py` low flip | 1-line guard removal at line 253 | Trivial — infrastructure is already symmetric |
| `rebuild_calibration_pairs_v2.py` low | 5 hard-coded `'high'` / `HIGH_LOCALDAY_MAX` sites + `--track` arg | Scoped, no new abstractions |
| `refit_platt_v2.py` low | 4 hard-coded `'high'` / `HIGH_LOCALDAY_MAX` sites + `--track` arg | Scoped, no new abstractions |
| `LOW_LOCALDAY_MIN` correctness | Verified on disk — correct fields, correct data_version | No change needed |
| `QUARANTINED_DATA_VERSIONS` | Low tag not quarantined — correct | No change needed |
| Day0 causality N/A runtime seam | `evaluator.py:814` / `day0_signal.py:85` — Phase 6 scope | No Phase 5 code change; note the attach point |
| Boundary quarantine rate monitoring | No scanner exists yet — needs `scripts/scan_tigge_mn2t6_localday_coverage.py` | New script, companion to extractor |
| `low_temp` observation coverage | Unknown backfill state — pre-check SQL needed | Must verify before starting rebuild |
