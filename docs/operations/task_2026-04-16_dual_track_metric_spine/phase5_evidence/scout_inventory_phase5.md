# Phase 5 Scout Inventory

Author: scout-finn (sonnet, fresh)
Date: 2026-04-17
Branch: data-improve (HEAD: ef09dc3)

---

## 5A — Truth Authority Surface Audit

### `src/state/truth_files.py`

- `ACTIVE_MODES` imported from `src/config`; not defined here — no local definition to patch.
- `LEGACY_STATE_FILES` at line 17: tuple of filenames (no `authority` field anywhere in this file).
- `mode` appears as a routing key throughout: `build_truth_metadata` (L29-46), `annotate_truth_payload` (L58-70), `infer_mode_from_path` (L49-54), `read_mode_truth_json` (L119-120), `backfill_mode_truth_metadata` (L176-190), `backfill_truth_metadata_for_modes` (L193-197).
- **RED FLAG — 5A**: `authority` field is ABSENT from `truth_files.py`. `build_truth_metadata` emits `mode`, `generated_at`, `source_path`, `deprecated`, `archived_to` — but no `authority` key. B077/B078 confirm this: the `mode` field is present as a routing key, but no `authority` field is threaded through. Phase 5 must add `authority` to `build_truth_metadata` output and all callers.

### `src/state/portfolio.py`

- `PortfolioState` dataclass at L660. No `authority` field in the dataclass.
- `load_portfolio` at L913: calls `choose_portfolio_truth_source(snapshot.get("status"))` at L983. The policy module (`portfolio_loader_policy.py`) decides `source` and `reason` but does not expose an `authority` tag.
- `choose_portfolio_truth_source` in `portfolio_loader_policy.py` returns a policy struct with `source` and `reason`; no `authority` field emitted.
- **Call sites needing `authority` thread**: `load_portfolio` L913 (primary), `_load_portfolio_from_json_data` L726 (JSON fallback path).

### `src/state/db.py`

- `query_portfolio_loader_view` at L3134: returns `{"status": ..., ...}` dict. No `authority` key in return payload. The caller in `portfolio.py:L983` reads `snapshot.get("status")` only.
- `portfolio_loader_view` is the function name; `portfolio_loader_view` as a SQL VIEW does not exist — it's a Python function (confirmed by grep: only L3134 definition + L1958 comment about crash).

**5A call sites needing `authority` field threaded**:
1. `src/state/truth_files.py:29` — `build_truth_metadata` (add `authority` param + emit)
2. `src/state/truth_files.py:58` — `annotate_truth_payload` (pass through)
3. `src/state/db.py:3134` — `query_portfolio_loader_view` return dict
4. `src/state/portfolio.py:983` — `load_portfolio` (read `authority` from snapshot)
5. `src/state/portfolio_loader_policy.py:19` — `choose_portfolio_truth_source` (thread `authority`)

---

## 5B — Low-Lane Extractor Landing Zone

- `scripts/extract_tigge_mn2t6_localday_min.py` — **DOES NOT EXIST** (confirmed: `No such file or directory`). Must be created in Phase 5.
- GRIB directory `/51 source data/raw/tigge_ecmwf_ens_regions_mn2t6/` **EXISTS** with 4 region subdirs (`americas`, `asia`, `europe_africa`, `oceania`). Contains **511 `.grib` files** (excluding `.ok` sentinels). Date range: `20240101_20240103` (earliest) → `20251120_20251122` (latest). Parallel to mx2t6's 420 files; mn2t6 has ~22% more files (511 vs 420) likely due to extended coverage.
- `scripts/ingest_grib_to_snapshots.py:259-263` — `NotImplementedError` guard for `mn2t6_low` track is PRESENT and disk-verified. Only change needed: remove the 5-line guard block.
- **Hard-coded `'high'` / `HIGH_LOCALDAY_MAX` sites** in rebuild/refit scripts:
  - `rebuild_calibration_pairs_v2.py:L119` — `WHERE temperature_metric = 'high'`
  - `rebuild_calibration_pairs_v2.py:L209` — `float(obs["high_temp"])`
  - `rebuild_calibration_pairs_v2.py:L258-L259` — `metric_identity=HIGH_LOCALDAY_MAX`
  - `rebuild_calibration_pairs_v2.py:L293-299` (banner) — hard-codes "high track" and `HIGH_LOCALDAY_MAX`
  - `rebuild_calibration_pairs_v2.py:L301` — inner WHERE must parametrize `temperature_metric`
  - `refit_platt_v2.py:L87, L107` — `WHERE temperature_metric = 'high'`
  - `refit_platt_v2.py:L177, L187` — `metric_identity=HIGH_LOCALDAY_MAX`
  - `refit_platt_v2.py:L228-230` — error string hard-codes "high-track"

---

## 5C — Replay MetricIdentity Propagation

`src/engine/replay.py` — `_forecast_rows_for` at L242-258: SQL query against `forecasts` table selects `forecast_high`, `forecast_low`, `temp_unit` but has **no `temperature_metric` filter**. The query returns rows for both tracks without discrimination.

`_forecast_reference_for` at L261-296: consumes `_forecast_rows_for` output and always uses `row["forecast_high"]` at L275 and L303 (two sites). **No metric branch** — low-track candidates would silently pull high values.

**Exact lines requiring metric filter**:
- `replay.py:L252` — add `AND temperature_metric = ?` (or equivalent) to WHERE clause
- `replay.py:L275` — `float(row["forecast_high"])` must become metric-conditional
- `replay.py:L303` — `float(row["forecast_high"])` same fix

---

## 5D — Absolute-Path Smell Scan (51 source data/scripts/)

18 files contain hardcoded `Path("/Users/leofitz/...")` or `Path("/Users/leofitz/.openclaw/workspace-venus/...")` patterns. This is a machine-portability issue — scripts will fail on any other machine. All flagged files are download/extract orchestrators, not Zeus core. Carryover from Phase 4 critic flag; not blocking Phase 5 but noted.

Key files with hardcoded ROOT:
- `download_ecmwf_open_ens.py:12`, `tigge_mx2t6_download_resumable.py:18`, `generate_full_tigge_manifest_from_geocoding.py:10`, `backfill_tigge_anchor_dates_resilient.py:13`, `scan_tigge_coverage_gaps.py:10`, `extract_tigge_city_member_vectors.py:12`, `extract_tigge_region_member_vectors_multistep.py:13`, `tigge_local_calendar_day_common.py:18`, `tigge_download_ecmwf_ens_region_multistep.py:18`, `tigge_mn2t6_download_resumable.py:12`, and 8+ more.

---

## 5E — File Provenance Header Compliance

Files scanned that LACK the canonical `# Lifecycle: / # Purpose: / # Reuse:` header:
- `scripts/ingest_grib_to_snapshots.py` — has docstring but no provenance header block
- `scripts/rebuild_calibration_pairs_v2.py` — has docstring, no provenance header
- `scripts/refit_platt_v2.py` — has docstring, no provenance header
- `tests/test_runtime_guards.py` — has module docstring only (`"""Runtime guard and live-cycle wiring tests."""`), no provenance header

None of the Phase 5 touch-targets carry the canonical header. Exec-emma should add headers when opening these files for Phase 5 edits (per CLAUDE.md file-header provenance rule).

---

## RED FLAGS Summary

| Flag | Severity | Description |
|---|---|---|
| `authority` absent from `truth_files.py` | RED | `build_truth_metadata` emits no `authority` field; B077/B078 blocked here |
| `replay.py` no `temperature_metric` filter | RED | `_forecast_reference_for` always uses `forecast_high`; low-track replay would silently corrupt |
| `extract_tigge_mn2t6_localday_min.py` missing | EXPECTED | Phase 5 deliverable; 511 GRIB files ready to process (2024-01-01 to 2025-11-22) |
| rebuild/refit 8 hard-coded `'high'` sites | YELLOW | Well-documented; exec-emma's 5B work order covers all of them |
| 18+ absolute-path hardcodes in 51-scripts | LOW | Machine-portability smell; not Phase 5 blocking |
| 4 Phase-5-touch files lack provenance headers | LOW | Add on first edit per CLAUDE.md protocol |
