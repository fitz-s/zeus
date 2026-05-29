# Inference Selection Audit: "Many snapshots → one p_raw vector"
**Date:** 2026-05-28  
**Branch:** feat/ft-ship-64  
**Scope:** read-only semantic audit, no fixes applied

---

## Q1 — "Many snapshots → one p_raw vector": which cycle wins and by what rule?

**Path (entry):**
1. `evaluator.py:3657` calls `read_executable_forecast(conn, ..., source_id=cfg.source_id, data_version=data_version_for_track(track), ...)`.  
2. Inside `read_executable_forecast` (`executable_forecast_reader.py:1099`):
   - `_latest_producer_readiness(...)` fetches the scope-level readiness row (UPSERT → latest cycle overwrites, so only one row survives per scope).
   - `_candidate_forecast_bundles(...)` (`reader.py:1042`) enumerates **all** `source_run_coverage` rows for `(city, date, metric, source_id, source_transport, data_version)` — one per issue cycle (00Z, 12Z, etc.) — and gates each independently.
   - `min(candidates, key=_bundle_rank)` (`reader.py:1231`) selects **one** winner.

**Ranking key** (`_bundle_rank`, `reader.py:1023`):
```
(contributor_rank=0 if FULL_CONTRIBUTOR else 1,
 -source_cycle_time_epoch,   # most recent cycle first within class
 -available_at_epoch,
 -snapshot_id)
```

**Result:** A SINGLE snapshot is elected — no averaging, no stacking of multiple snapshots. Within the FULL_CONTRIBUTOR class the freshest (most-recent) qualifying cycle wins; a later NON_CONTRIBUTOR cycle (post-afternoon-peak 12Z run) CANNOT displace an earlier FULL_CONTRIBUTOR 00Z run.

**Path (monitor_refresh):**  
`monitor_refresh.py:263` calls the same `read_executable_forecast` with identical parameter contract. Same single-snapshot election logic applies.

---

## Q2 — TIGGE vs OpenData: are two products mixed for the same target?

**Short answer: No. The call is pinned to exactly one `source_id` + `data_version` pair per evaluation.**

- `evaluator.py:3664–3666`: `source_id = entry_forecast_cfg.source_id` (one config value, e.g. `ecmwf_open_data`), `data_version = data_version_for_track(track)` (one version string, e.g. `ecmwf_opendata_high_mx2t3`).
- `_source_run_coverages_for_scope` SQL (`reader.py:345–356`) has `AND source_id = ? AND source_transport = ? AND data_version = ?` — cross-product mixing is structurally impossible.
- The TIGGE vs OpenData distinction matters for **Platt calibration** not snapshot selection: `calibration_transfer_policy.py:61–67` defines `_TRANSFER_SOURCE_BY_OPENDATA_VERSION` which maps `ecmwf_opendata_high_mx2t3 → tigge_high_mx2t6_localday` so the evaluator applies the TIGGE-fit Platt to OpenData members. The physical members are IFS ensemble — the same model, different release channel. Only the calibration source differs; the forecast snapshot source is singular.

---

## Q3 — Is the selected snapshot's window verified to cover the target PM extremum?

**Yes — via `contributes_to_target_extrema` at read time**, but with nuances:

- `classify_forecast_extrema_authority` (`forecast_extrema_authority.py:125–130`) classifies each snapshot row as `FULL_CONTRIBUTOR`, `NON_CONTRIBUTOR`, `LEGACY_NULL_PASSTHROUGH`, or `UNKNOWN` based on `contributes_to_target_extrema` (DB column), `forecast_window_attribution_status`, and `boundary_ambiguous`.
- `read_executable_forecast_snapshot` (`reader.py:683–697`) blocks `NON_CONTRIBUTOR` and `UNKNOWN` immediately — a snapshot whose window does not cover the afternoon peak is dropped at the per-candidate gate.
- `_bundle_rank` (`reader.py:1031`) additionally prioritises `FULL_CONTRIBUTOR` over all other classes.
- The `snapshot_window_start` vs `coverage_window_start` match is enforced: `reader.py:875–878` returns `SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH` if these differ.
- `local_day_start_utc` in the snapshot is the start of the target local calendar day in UTC, computed by the ingest pipeline at write time; `expected_steps_json` / `observed_steps_json` checked at `reader.py:824–827` must be a subset for the coverage to be COMPLETE.

**Residual gap:** `contributes_to_target_extrema` is a **pre-computed boolean set at ingest time**, not re-evaluated at read time against current decision_time. If the ingest pipeline mislabelled a snapshot (e.g., a post-peak 12Z run classified as FULL_CONTRIBUTOR due to a window-computation bug), the reader would serve it. The classifier trusts the DB column; no re-derivation from raw lead-step data occurs at read time.

---

## Q4 — Does bias_c at inference match the cycle/lead of the selected snapshot?

**Partially — city + season match is enforced; cycle/lead of the snapshot is NOT threaded into the bias lookup.**

Entry path (`evaluator.py:3329`):
```python
season = season_from_date(str(target_date), lat=city.lat)
row = _read_bias_model_for_entry(conn,
    city=city.name, season=season, metric=metric_str,
    live_data_version=live_data_version, month=None,
    error_model_family="full_transport_v1")
```

Monitor path (`monitor_refresh.py:401–404`):
```python
row = conn.execute(
    "SELECT * FROM model_bias_ens_v2 WHERE city=? AND season=? AND metric=? "
    "AND live_data_version=? AND month=0 AND error_model_family=?",
    (city_name, season, metric, live_data_version, "full_transport_v1"))
```

**Lookup key is `(city, season, metric, live_data_version, month=0, family)`.** There is no `source_cycle_time`, `issue_time`, or `lead_hours` parameter. One bias_c value is served regardless of whether the selected snapshot came from the 00Z run (lead ~42h for D+2) or the 12Z run (lead ~30h for D+2). Both cycles produce member extrema that receive the same correction.

This is **by design**: the full-transport error model (`full_transport_v1`) was trained on per-member daily extrema aggregated across both 00Z and 12Z contributors (see `calibration_pairs_v2` populator), so the single bias_c represents the mean transport error across cycles within the season. It is a (city, season, metric) summary not a per-cycle value.

**Note on `month=None` in the entry path vs `month=0` in the monitor path.** `read_bias_model` converts `None → 0` (`base_params = (..., 0 if month is None else int(month))`). Both resolve to the same `month=0` sentinel, which is the "season-pooled" row. The two paths are equivalent.

**Remaining gap (open task #138 / R8):** `gate_set_hash` and `target_month` are NOT passed to `_resolve_ft_error_model_for_entry` or `_load_ft_error_model`. A stale-gate VERIFIED row (fit pre-MIN_PAIRED_N=5 change) could be served if the promotion pipeline did not delete it. The `read_bias_model` antibodies (`require_gate_set_hash`, `require_coverage_months`) exist but are **not invoked on the live inference read path** at this branch tip.

---

## Summary verdict

The inference path uses a **single coherent forecast snapshot**, elected by a well-designed contributor-first, recency-second ranking across all eligible issue cycles. Products are not mixed: snapshot selection is scoped to one `(source_id, data_version)` pair and Platt calibration applies a static TIGGE→OpenData transfer at Platt lookup, not at snapshot selection. The elected snapshot's window coverage is verified via `contributes_to_target_extrema` at read time.

One coherent structural gap remains: `bias_c` is applied per `(city, season, metric)` without discrimination by the selected snapshot's actual cycle time or lead step. This is intentional (season-pooled) but the live inference path does not enforce that the serving row was fit under the current gate set (`gate_set_hash` guard bypassed — task #138).

---

## Key file:line citations

| Concern | File | Lines |
|---------|------|-------|
| Cycle enumeration (one per 00Z/12Z) | `src/data/executable_forecast_reader.py` | 328–386 (SQL), 1042–1096 |
| Single-winner election | `src/data/executable_forecast_reader.py` | 1023–1034, 1231 |
| FULL_CONTRIBUTOR-first ranking | `src/data/executable_forecast_reader.py` | 1031 |
| One source_id / data_version per call | `src/engine/evaluator.py` | 3664–3666 |
| TIGGE→OpenData Platt transfer map | `src/data/calibration_transfer_policy.py` | 61–67 |
| contributes_to_target_extrema block | `src/data/executable_forecast_reader.py` | 683–697 |
| snapshot_window_start match check | `src/data/executable_forecast_reader.py` | 875–878 |
| bias_c lookup (entry path) | `src/engine/evaluator.py` | 3329–3356 |
| bias_c lookup (monitor path) | `src/engine/monitor_refresh.py` | 379–413 |
| No cycle/lead in bias key (confirmed) | `src/calibration/ens_bias_repo.py` | 542–546 |
| gate_set_hash not passed live | `src/engine/evaluator.py` | 3331–3338 (month=None, no hash arg) |
