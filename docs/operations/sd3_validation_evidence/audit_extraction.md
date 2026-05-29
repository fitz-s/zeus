# Daily-Extreme Extraction Provenance Audit
# Created: 2026-05-28
# Authority basis: Read-only code audit of worktree ens-bias-hierarchical

---

## Q1: Where does valid-time → local-day-window → max mapping live?

### TIGGE (mx2t6 / 6h product)

**File:** `scripts/extract_tigge_mx2t6_localday_max.py`

- **Line 164:** `day_start_utc, day_end_utc = _local_day_bounds_utc(target_date, city_tz)`
  Uses `ZoneInfo`-aware local-day bounds (midnight to midnight in city tz).
- **Lines 197–202 / 491–506 (batch path):** For each GRIB message, reads `startStep` and `endStep` from the GRIB header directly. Computes `window_start = issue_utc + timedelta(hours=startStep)` and `window_end = issue_utc + timedelta(hours=endStep)`. Calls `_overlap_seconds(window_start, window_end, day_start_utc, day_end_utc)`. Selects any window with `overlap > 0`.
- **Lines 224–227 / 543–547:** Per member: `max(member_values[member], value_native)` across all selected windows.

**Shared helper** (`scripts/_tigge_common.py`):
- **Line 161–165:** `_local_day_bounds_utc` uses `ZoneInfo` for DST-aware midnight; correct.
- **Line 168–177:** `_overlap_seconds` = `max(0, (min(window_end, target_end) - max(window_start, target_start)).total_seconds())`. Standard interval overlap.

### ECMWF OpenData (mx2t3 / 3h product)

**File:** `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/extract_open_ens_localday.py`

- **Line 389–390:** `local_start, local_end = local_day_bounds_utc(target_local_date, timezone_name)` — same ZoneInfo-aware midnight-to-midnight bounds.
- **Lines 397–414:** For each `(member, step_hours)` bucket: `window_end = issue_dt + timedelta(hours=step_hours)`, `window_start = window_end - timedelta(hours=STEP_HOURS)`. Then `_windows_overlap(...)` returns `(fully_inside, has_overlap)`. HIGH track uses `fully_inside` only (`members_inner`). LOW track distinguishes inner vs boundary for quarantine.
- **Line 429:** `value = max(vals)` over all inner values per member.

**STEP_HOURS import:** `from tigge_local_calendar_day_common import STEP_HOURS` — **this is the critical finding (see Q2).**

---

## Q2: Is the 6h vs 3h step difference handled correctly?

### TIGGE (correct):
`scripts/_tigge_common.py:52`: `AGGREGATION_WINDOW_HOURS = 6` / `_STEP_HOURS = 6`.
The extractor reads `startStep` and `endStep` directly from the GRIB header for each mx2t6 message. TIGGE mx2t6 messages carry `startStep = endStep - 6` natively. The window `[issue + startStep, issue + endStep]` is therefore exactly the 6h accumulation period per message. **This is correct.**

### OpenData — CRITICAL DEFECT:

`tigge_local_calendar_day_common.py:20`: `STEP_HOURS = 6`

The OpenData extractor imports this constant and uses it at line 399:
```python
window_start = window_end - timedelta(hours=STEP_HOURS)  # = window_end - 6h
```

But the product being ingested is **mx2t3** — a **3-hour** accumulation window. Each mx2t3 GRIB message at step `T` covers the 3-hour period `[T-3h, T]`, **not** `[T-6h, T]`.

By using `STEP_HOURS = 6`, the extractor computes `window_start = T - 6h` for every mx2t3 message. This means every window attribution is **doubled in width**: a message at step 6h is treated as covering `[0h, 6h]` instead of the correct `[3h, 6h]`. At steps 3h through 144h (the native 3h-stride range), each window is assigned twice the correct coverage width.

**Consequences:**
1. **Day-boundary contamination (HIGH track):** A 3h window at step 3h that falls entirely within a local day would still be included by the 6h-wide overlap test, but the wrong `window_start` can pull a window from the previous local day into the current day's max when the true 3h window would not overlap. Specifically, for UTC-positive cities, the step-3h window on an early-morning issue will have `window_start = issue - 3h` (UTC previous calendar day) instead of `issue + 0h`. **This is the analog of the documented 12z night-window bug for 3h data.**
2. **`fully_inside` test is too permissive (LOW track):** A 3h window straddling midnight would have `window_start = T - 6h` which may fall on the previous day, causing `fully_inside = False` correctly, but a 3h window entirely within the day may have its synthetic 6h window straddle midnight and be misclassified as `boundary` instead of `inner`. This inflates boundary_ambiguous counts, suppressing valid LOW members.
3. **`step_label` in output is wrong:** `step_label = f"{step_hours - STEP_HOURS}-{step_hours}"` → `f"{step_hours - 6}-{step_hours}"`. For step_hours=3, this emits `"-3-3"` which is physically nonsensical (negative startStep). These strings are recorded in `selected_step_ranges` and propagate to `forecast_window_start_utc` via `ingest_grib_to_snapshots._forecast_window_from_payload` (lines 349–351), producing incorrect window-evidence timestamps.

**Root:** The docstring of `extract_open_ens_localday.py` (lines 28–42) says "6-hour aggregations" — it was written when OpenData served mx2t6. The 2026-05-07 cutover to mx2t3 updated the `TrackConfig.open_data_param` and `short_name` fields (lines 110–128), but did **not** update `STEP_HOURS` or the window reconstruction logic. The `tigge_local_calendar_day_common.py` shared constant was never overridden per-product.

**Evidence summary:**
- `extract_open_ens_localday.py:81`: `from tigge_local_calendar_day_common import ... STEP_HOURS`
- `tigge_local_calendar_day_common.py:20`: `STEP_HOURS = 6` (hardcoded, product-agnostic)
- `extract_open_ens_localday.py:399`: `window_start = window_end - timedelta(hours=STEP_HOURS)` — uses 6h for 3h product
- `ecmwf_open_data.py:36–40` (docstring note): "Data versions are unchanged; calibration learns the 3h→6h envelope mapping downstream" — this is an architectural decision, not a code fix.

### TIGGE `compute_required_max_step` (secondary, documented):
`scripts/_tigge_common.py:87–96`: Uses fixed UTC offset, not ZoneInfo, for the step-horizon calculation. Documented as known divergence from `required_period_end_steps()` (which uses ZoneInfo). The DST-aware override at extraction time (`extract_tigge_mx2t6_localday_max.py:241`) reads the actual ZoneInfo offset at `issue_utc`, which mitigates this for TIGGE. The OpenData extractor does not call `compute_required_max_step` at all — it sets `step_horizon_hours = float(max_step)` unconditionally (line 456), which is correct since the full step range is always downloaded.

---

## Q3: Unit conversion — is it consistent per product?

### TIGGE:
- `extract_tigge_mx2t6_localday_max.py:413–420`: `_kelvin_to_native(value_k, city_unit)` — converts Kelvin → °C or °F per city manifest `unit` field. Applied at read time (line 215 / line 533).
- `ingest_grib_to_snapshots.py:110–114`: `_normalize_unit` maps `'C'` → `'degC'`, `'F'` → `'degF'`. Called at ingest (line 525). `validate_members_unit` checks the normalized value (line 526).
- **Correct and consistent.**

### OpenData:
- `extract_open_ens_localday.py:78–88`: imports `kelvin_to_native` from `tigge_local_calendar_day_common` — the same Kelvin→°C/°F function.
- Applied at line 407: `value_native = kelvin_to_native(value_k, unit)`.
- The `unit` field comes from the city manifest (same source as TIGGE).
- `ingest_grib_to_snapshots.py:524–526`: same `_normalize_unit` / `validate_members_unit` gate applies at ingest time for OpenData JSON too (it is processed by the same `ingest_json_file` path).
- **Correct and consistent.** Unit conversion is product-agnostic (always from Kelvin, always per-city manifest `unit`).

---

## Q4: `contributes_to_target_extrema` and `boundary_ambiguous` — are they product-step-aware?

### Computation path:
`ingest_grib_to_snapshots._contract_evidence_fields` (lines 366–474):
1. Calls `_forecast_window_from_payload` → reads `selected_step_ranges` → reconstructs `forecast_window_start_utc` / `_end_utc` from min/max step range endpoints (lines 349–361).
2. Passes those UTC/local windows to `ForecastToBinEvidence.from_snapshot_payload` → `forecast_calibration_domain.py:319–324`: `_classify_window_attribution(contract_domain, window_start_local, window_end_local)` → returns `"FULLY_INSIDE_TARGET_LOCAL_DAY"` → `contributes = True`.

**Defect propagation:** Because `selected_step_ranges` for OpenData contain the wrong startStep (negative for step=3, or 6h-wide instead of 3h), the reconstructed `forecast_window_start_utc` will be shifted 3h earlier than reality. This means `_classify_window_attribution` operates on a 6h window `[T-6h, T]` instead of the correct 3h window `[T-3h, T]`. For HIGH track:
- Windows that are `FULLY_INSIDE_TARGET_LOCAL_DAY` under the 3h truth may become `AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY` under the spurious 6h window (if `T-6h` crosses midnight). This would set `contributes_to_target_extrema = 0` incorrectly.
- Conversely, a 3h window that truly straddles midnight might have its spurious 6h predecessor fall fully inside the previous day and be passed as `DETERMINISTICALLY_PREVIOUS_LOCAL_DAY`.

`boundary_ambiguous` for HIGH is always `False` (set unconditionally in `extract_open_ens_localday.py:459`). For LOW, `boundary_ambiguous` is computed from per-member `inner_min` vs `boundary_min` at lines 478–483. The inner/boundary split itself is already corrupted by the wrong `STEP_HOURS` (see Q2), so `boundary_ambiguous` counts for LOW are unreliable.

**The flags are NOT product-step-aware.** The same `STEP_HOURS = 6` constant flows from window computation through step_label through `contributes_to_target_extrema`. There is no branch on `aggregation_window_hours` or product type anywhere in the extraction or ingest pipeline.

---

## Summary of findings

| Question | TIGGE (6h) | OpenData (3h) |
|---|---|---|
| Local-day bounds | Correct (ZoneInfo DST-aware midnight) | Correct (same function) |
| Window reconstruction | Correct (reads startStep/endStep from GRIB) | **DEFECTIVE** — uses STEP_HOURS=6 for 3h product |
| max-over-windows | Correct | Correct within the wrongly-defined windows |
| Unit conversion | Correct | Correct |
| step_label strings | Correct | Wrong (negative startStep for step=3) |
| forecast_window_start_utc evidence | Correct | Shifted 3h earlier than reality |
| contributes_to_target_extrema | Step-accurate | Step-inaccurate (inherits window defect) |
| boundary_ambiguous (HIGH) | N/A (always False by design) | N/A (always False by design) |
| boundary_ambiguous (LOW) | Correct semantics | Unreliable (wrong inner/boundary split) |

**Key files and lines:**
- `extract_open_ens_localday.py:81` — imports `STEP_HOURS = 6` from shared common
- `extract_open_ens_localday.py:399` — `window_start = window_end - timedelta(hours=STEP_HOURS)` — wrong for mx2t3
- `extract_open_ens_localday.py:408` — `step_label = f"{step_hours - STEP_HOURS}-{step_hours}"` — negative for step=3
- `tigge_local_calendar_day_common.py:20` — `STEP_HOURS = 6` (not overridden per product)
- `ingest_grib_to_snapshots.py:349–361` — reconstructs window from step ranges, inherits defect
- `src/calibration/forecast_calibration_domain.py:319–324` — classifies attribution from reconstructed window

