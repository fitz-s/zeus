# Phase 3 → Phase 4 Learnings (exec-bob)

Date: 2026-04-16  
Author: exec-bob (Phase 3 observation_client + evaluator seam work)

---

## 1. Seams Phase 4 Will Rub Against

### `src/engine/evaluator.py:1601` — `_store_ens_snapshot`
Writes to `ensemble_snapshots` (legacy) or `ensemble_snapshots_v2` depending on `_ensemble_snapshots_table(conn)`. The INSERT at `:1617` includes `data_version="live_v1"` but **no `temperature_metric` column**. Phase 4's GRIB→v2 pipeline must add `temperature_metric` to the INSERT and the WHERE lookup at `:1642`. The `_ensemble_snapshots_table()` toggle already exists — Phase 4 must verify the v2 table schema includes `temperature_metric` as a required column (see `src/state/schema/v2_schema.py:305` for `day0_metric_fact`; confirm `ensemble_snapshots_v2` has the same).

### `src/engine/evaluator.py:659` — `_normalize_temperature_metric`
Currently reads from `getattr(candidate, "temperature_metric", "high")` with a default of `"high"`. This string default is a silent high-track fallback for any candidate missing the field. Phase 4 must harden this: if `temperature_metric` is absent, fail-closed rather than defaulting to high. SD-3 says no consumer defaults to high.

### `src/engine/evaluator.py:814-834` — `Day0Signal` construction
After Phase 3, `observed_low_so_far=float(candidate.observation.low_so_far)` passes the value unconditionally. `Day0Signal.__init__` at `src/signal/day0_signal.py:85` raises `NotImplementedError` for low metrics. Phase 4 does not change this — but Phase 6's Day0 split will need to branch here. The seam is clean for now; do not conflate Phase 4 v2 pipeline work with the Phase 6 class split.

### `src/engine/monitor_refresh.py:306-323` — Day0Signal construction in monitor
Same pattern as evaluator. After Phase 3, uses direct attribute access. Phase 4's Platt refit touches `get_calibrator()` at `monitor_refresh.py:329` — verify `get_calibrator` can handle v2 Platt models keyed by `(temperature_metric, cluster, season, data_version)`.

---

## 2. Forward Risks for Day0 Split (Phase 6)

### `Day0Signal.forecast_context()` returns a `dict` (`:256`)
`forecast_context()` returns an untyped dict. When Phase 6 splits into `Day0HighSignal` / `Day0LowNowcastSignal`, this return type must be typed or the evaluator's downstream consumers (currently at evaluator ~`:835`) will silently accept the wrong shape from the wrong class.

### `member_mins_remaining=remaining_member_extrema` is a placeholder
Evaluator `:826` passes the max array as mins — an explicit Phase 6 TODO. When Phase 6 lands, the extrema producer (`remaining_member_maxes_for_day0`) must be split to emit separate max/min arrays. The call site is `evaluator.py:770-788` — Phase 6 exec must update both the producer and both consumer sites (evaluator + monitor_refresh).

### `Day0ObservationContext` compat shims still present
`.get()` and `.__getitem__()` shims on `Day0ObservationContext` emit `DeprecationWarning`. After Phase 3, no live code in evaluator or monitor_refresh hits them. But any other module importing `get_current_observation` and using dict-style access will hit them silently. Phase 4 should grep for `observation\.get\(` and `observation\[` across all of `src/` to find remaining callers before the shims are removed.

---

## 3. Simplification Candidates

### Remove compat shims on `Day0ObservationContext`
`src/data/observation_client.py:71-88` — `.get()` and `.__getitem__()`. Phase 3 upgraded all 12 live callsites. Shims can be deleted once Phase 4 confirms no other callers (one grep pass suffices).

### Redundant lazy imports in `_get_asos_wu_offset`
`src/data/observation_client.py:438, 449` — `from src.contracts.exceptions import MissingCalibrationError` appears twice inside `_get_asos_wu_offset`, now redundant with the top-level import at `:24`. Safe to delete both inner imports.

### `_normalize_temperature_metric` is a one-call wrapper
`evaluator.py:277` wraps `MetricIdentity.from_raw()` with a docstring. Once the default-to-`"high"` fallback is hardened (see §1 above), this becomes a thin pass-through. Phase 4 can inline it or keep it — minor.

---

## 4. Observation → Calibration Chain

```
Day0ObservationContext (observation_client.py)
  ↓  candidate.observation.{high_so_far, low_so_far, current_temp, source}
evaluator.py:814  Day0Signal(observed_high_so_far=..., observed_low_so_far=...)
  ↓  day0.p_vector(bins) → p_raw np.ndarray
evaluator.py:850  _store_snapshot_p_raw(conn, snapshot_id, p_raw)
  → persists p_raw_json on ensemble_snapshots row
evaluator.py:873+  EdgeDecision(p_raw=p_raw, decision_snapshot_id=snapshot_id, ...)
  ↓  EdgeDecision returned to cycle_runner.py → position opened
execution/harvester.py:810  add_calibration_pair(conn, city, target_date, range_label, p_raw, outcome, ...)
  → INSERT INTO calibration_pairs (legacy table)
src/calibration/store.py:55  add_calibration_pair signature has NO temperature_metric / observation_field / data_version
```

**Phase 4 must-fix:** `add_calibration_pair` writes to the legacy `calibration_pairs` table with no metric identity. For `calibration_pairs_v2`, the function needs `temperature_metric`, `observation_field`, and `data_version` parameters, and `harvester.py:810` must pass them from the `EdgeDecision` / position context. The `decision_group_id` at `harvester.py:817` is computed without metric — that too must be extended or a new v2 compute function used.

---

## 5. INV-14/15/16 Violations Lurking (Not Touched in Phase 3)

### INV-14 — `_store_ens_snapshot` missing `temperature_metric`
`evaluator.py:1601-1650` — snapshot INSERT carries no `temperature_metric`. High and low snapshots for the same `(city, target_date, issue_time)` will collide at the INSERT OR IGNORE unless `temperature_metric` is part of the unique key. This is a concrete SD-2 violation waiting to manifest when low-track evaluation goes live.

### INV-14 — `add_calibration_pair` missing metric identity
`src/calibration/store.py:55` — as traced above, no `temperature_metric` column. High and low pairs will conflate in Platt fitting. This is the highest-impact INV-14 gap for Phase 4.

### INV-15 — Open-Meteo observation source not gated at training seam
`observation_client.py` now returns `Day0ObservationContext` with `source="openmeteo_hourly"` for fallback observations. This source string flows into `observation_source` in `Day0Signal` and into `EdgeDecision`. If harvester writes a calibration pair from an Open-Meteo-backed decision, `training_allowed` must be `false` — but `add_calibration_pair` has no `training_allowed` field and no source-based gate. Phase 4 must add source provenance to the calibration write path.

### INV-16 — No causality_status check before Platt lookup
`evaluator.py` (not touched in Phase 3) has no `N/A_CAUSAL_DAY_ALREADY_STARTED` branch. A low-track Day0 slot where the local day has already started will pass through to `Day0Signal` (which raises `NotImplementedError` today, masking the causal violation). When Phase 6 lifts the `NotImplementedError`, INV-16 becomes a live bug. Phase 6 must insert the causality gate before Day0Signal construction.
