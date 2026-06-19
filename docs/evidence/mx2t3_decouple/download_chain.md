# mx2t3 / mn2t3 OpenData Ingest Chain Map

**Branch audited:** `live/iteration-2026-06-13`
**Mapping date:** 2026-06-17
**Purpose:** Operator decision: stop ECMWF OpenData mx2t3/mn2t3 download if nothing live consumes it. This doc maps the complete chain and every gate.

---

## 1. Complete Call Chain

### Scheduler registration

**File:** `src/ingest_main.py`

| Site | Detail |
|------|--------|
| Line 62-69 | `_ingest_main_owns_opendata()` — guards ALL three opendata job registrations. Reads `ZEUS_FORECAST_LIVE_OWNER` env (default `"ingest_main"`); routes through `active_opendata_owner()` in `src/data/source_job_registry.py`. If owner != `"ingest_main"`, no opendata jobs are registered at all. |
| Line 1727-1733 | Daily HIGH job: `(_opendata_mx2t6_cycle, "cron", hour=7, minute=30, id="ingest_opendata_daily_mx2t6")` — only registered when `_ingest_main_owns_opendata()` is True |
| Line 1731-1733 | Daily LOW job: `(_opendata_mn2t6_cycle, "cron", hour=7, minute=35, id="ingest_opendata_daily_mn2t6")` — same guard |
| Line 1748-1751 | Boot catch-up: `(_opendata_startup_catch_up, "date", id="ingest_opendata_startup_catch_up")` — same guard |

**Registry record:** `src/data/source_job_registry.py` lines 137-151
- `ingest_opendata_daily_mx2t6` — `source_id="ecmwf_open_data"`, `owner_gated=True`
- `ingest_opendata_daily_mn2t6` — `source_id="ecmwf_open_data"`, `owner_gated=True`
- `ingest_opendata_startup_catch_up` — `source_id="ecmwf_open_data"`, `owner_gated=True`

**Config:** `config/settings.json` line 307: `"disable_legacy_opendata_forecast_live_jobs": false` — this key governs only the `forecast_live_daemon` parallel opendata jobs, NOT the `ingest_main` scheduler jobs.

---

### Cycle functions → track runner

**File:** `src/ingest_main.py`

```
_opendata_mx2t6_cycle()  [line 728]
  → _run_opendata_track("mx2t6_high")  [line 737]

_opendata_mn2t6_cycle()  [line 744]
  → _run_opendata_track("mn2t6_low")  [line 753]

_opendata_startup_catch_up()  [line 781]
  → _run_opendata_track("mx2t6_high")  [line 789]
  → _run_opendata_track("mn2t6_low")   [line 789]
```

**`_run_opendata_track(track)` [lines 759–777]:**
1. Calls `_is_source_paused(SOURCE_ID)` — if paused by control plane, returns immediately.
2. Acquires `OPENDATA_DAEMON_LOCK_KEY` via `src/data/dual_run_lock.py` — mutex with `forecast_live_daemon`.
3. Calls `collect_open_ens_cycle(track=track)` from `src/data/ecmwf_open_data.py`.

---

### `collect_open_ens_cycle` — the main download/ingest function

**File:** `src/data/ecmwf_open_data.py`, line 1324

**TRACKS config [lines 143–154]:**
- `"mx2t6_high"` → `open_data_param="mx2t3"`, `data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION` = `"ecmwf_opendata_mx2t3_local_calendar_day_max"` (per `src/contracts/snapshot_ingest_contract.py` line 42)
- `"mn2t6_low"` → `open_data_param="mn2t3"`, `data_version=ECMWF_OPENDATA_LOW_DATA_VERSION` = `"ecmwf_opendata_mn2t3_local_calendar_day_min"` (line 43)

**Execution flow within `collect_open_ens_cycle`:**

1. **Release calendar check** [line 1362] — `gate_source(SOURCE_ID)` + `_select_cycle_for_track()` [line 506]. If the cycle is not yet eligible (`FetchDecision` != `FETCH_ALLOWED`), returns early with no download.
2. **Parallel download** [via `_fetch_one_step` + `ThreadPoolExecutor`] — fetches per-step GRIB files from ECMWF OpenData API for param `mx2t3` or `mn2t3`. `_fetch_one_step` [line 1136] handles HTTP range-request download per step hour.
3. **On download failure** — calls `write_source_run(..., status="FAILED")` directly [lines 1510, 1579] — FAILED/NOT_RELEASED source_run rows written to `zeus-forecasts.db` with no `ensemble_snapshots` rows.
4. **Extract subprocess** [line 1665] — calls `scripts/extract_open_ens_localday.py` subprocess to produce per-(city, target_local_date, lead_day) JSON records.
5. **Ingest** [line 1774] — calls `_ingest_grib_ingest_track()` (imported as `scripts/ingest_grib_to_snapshots.ingest_track` at line 113). This function:
   - **Writes `ensemble_snapshots`** via `INSERT OR IGNORE INTO ensemble_snapshots` [scripts/ingest_grib_to_snapshots.py line 830]. Stamped with `source_run_id`, `data_version`, `source_id="ecmwf_open_data"`, `source_transport="ensemble_snapshots_db_reader"`.
6. **Authority chain write** [line 1836 area] — calls `_write_source_authority_chain()` [line 836] which:
   - **Writes `source_run`** via `write_source_run()` [line 937] in `src/state/source_run_repo.py` [line 98 = `INSERT OR REPLACE INTO source_run`]
   - **Writes `source_run_coverage`** via `write_source_run_coverage()` [line 1085] in `src/state/source_run_coverage_repo.py` [line 100 = `INSERT OR REPLACE INTO source_run_coverage`]

**All three writes go to `zeus-forecasts.db`** (the forecast-class DB, not world). `src/state/db.py` line 272 confirms forecasts DB owns: `ensemble_snapshots`, `source_run`, `source_run_coverage`.

---

## 2. Who Writes What — Source Isolation

### `ensemble_snapshots`

The **only** live writer for `ecmwf_open_data` rows is `scripts/ingest_grib_to_snapshots.py` line 830 (`INSERT OR IGNORE INTO ensemble_snapshots`), called via `collect_open_ens_cycle` → `_ingest_grib_ingest_track`.

Other writers that put rows into `ensemble_snapshots` (separate source families — NOT affected by stopping opendata):
- **TIGGE archive** — `src/data/tigge_pipeline.py` via `ingest_grib_to_snapshots.ingest_track`; writes `data_version` = `tigge_mx2t6_local_calendar_day_max` / `tigge_mn2t6_local_calendar_day_min`
- **AIFS** — `src/engine/evaluator.py` line 6714 / 6800 (v2 table path); `data_version` = AIFS family

### `source_run`

The **only** caller of `write_source_run()` is `src/data/ecmwf_open_data.py` (lines 937, 1510, 1579). This is confirmed by grep: no other `src/` file calls `write_source_run()` (verified above). Ingest_grib_to_snapshots does NOT call `write_source_run` directly — it receives a `SourceRunContext` from the caller and stamps snapshots with it, but the actual `source_run` table write is done by `ecmwf_open_data.py`'s `_write_source_authority_chain`.

TIGGE pipeline does NOT call `write_source_run` (grep confirmed zero hits in `src/data/tigge_pipeline.py`).

### `source_run_coverage`

The **only** caller of `write_source_run_coverage()` in all of `src/` is `src/data/ecmwf_open_data.py` line 1085. No other source (tigge, bayes_precision_fusion, open-meteo, replacement forecast) writes to `source_run_coverage`. This is the critical gate finding:

> **Stopping the OpenData mx2t3+mn2t3 ingest stops ALL new `source_run_coverage` rows for `source_id="ecmwf_open_data"`. No other job back-fills these rows.**

The `source_run_coverage` table is the family-readiness/LIVE_ELIGIBLE authority. The `forecast_snapshot_ready` event trigger reads `source_run_coverage` [src/events/triggers/forecast_snapshot_ready.py line 299, 563] to determine per-city forecast readiness. If opendata ingest stops, no new LIVE_ELIGIBLE coverage rows are written for the opendata source family — which would block decisions on ecmwf_open_data certificates.

### `raw_model_forecasts`

Completely separate pipeline. Written by:
- `src/data/bayes_precision_fusion_download.py` line 784 — `INSERT OR IGNORE INTO raw_model_forecasts`
- `src/data/replacement_forecast_production.py` line 267 — open-meteo single/previous runs

The OpenData mx2t3 ingest does NOT touch `raw_model_forecasts`. Stopping OpenData has zero effect on the replacement forecast pipeline.

---

## 3. Tests That Reference These Jobs / Would Break

### Tests directly testing the OpenData ingest pipeline (would fail if module removed):

| Test file | What it tests |
|-----------|---------------|
| `tests/test_ecmwf_open_data_collect_cycle.py` | `collect_open_ens_cycle` end-to-end |
| `tests/test_ecmwf_open_data_hang_antibodies_2026_05_13.py` | Hang antibodies in `ecmwf_open_data.py` |
| `tests/test_ecmwf_open_data_ingest_metric_independence.py` | HIGH/LOW metric isolation in ingest |
| `tests/test_ecmwf_open_data_ingest.py` | Ingest correctness |
| `tests/test_ecmwf_open_data_parallel_fetch.py` | Parallel `_fetch_one_step` |
| `tests/test_ecmwf_open_data_source_failover.py` | Source failover logic |
| `tests/test_ecmwf_open_data_step_hours.py` | `STEP_HOURS` / `OPENDATA_MAX_STEP_HOURS` contract |
| `tests/test_ecmwf_open_data_subprocess_hardening.py` | Subprocess extract hardening |
| `tests/test_ecmwf_opendata_ingest_schedule.py` | Scheduler registration timing |
| `tests/test_opendata_data_version_producer_subset_gate.py` | data_version allowlist gate |
| `tests/test_opendata_future_target_contract.py` | Future target fetch contract |
| `tests/test_opendata_mx2t3_not_2t.py` | mx2t3 param (not 2t) antibody |
| `tests/test_opendata_observed_members_aggregation.py` | observed_members aggregation |
| `tests/test_opendata_ownership_singleton.py` | ingest_main vs forecast_live singleton |
| `tests/test_opendata_release_calendar_selection.py` | Release calendar cycle selection |
| `tests/test_opendata_tigge_equivalence_report.py` | OpenData/TIGGE equivalence |
| `tests/test_opendata_writes_v2_table.py` | Writes to v2 table contract |
| `tests/test_forecast_live_daemon.py` | forecast_live_daemon opendata jobs |
| `tests/test_forecast_live_opendata_producer_required_for_fsr.py` | FSR requires opendata producer |
| `tests/test_scheduler_adapter.py` | Scheduler registration |
| `tests/test_source_job_registry.py` | Job registry entries incl. opendata |

### Tests that use `ensemble_snapshots` table data (would need fixture updates if data_version removed from schema):

These tests seed `ensemble_snapshots` fixtures or check schema — they reference the table generically and would NOT necessarily break from stopping the live job, but would break if the `ecmwf_opendata_*` data_version strings were removed from the allowlist:
- `tests/test_ensemble_snapshots_bias_corrected_schema.py`
- `tests/test_ensemble_snapshots_executable_schema.py`
- `tests/test_ensemble_snapshots_ingest_backend.py`
- `tests/test_ingest_grib_source_run_context.py`
- `tests/test_source_run_coverage_schema.py`
- `tests/test_source_run_schema.py`
- Dozens more that seed fixtures using opendata data_versions

---

## 4. What Stops / What Survives if OpenData mx2t3+mn2t3 Jobs Are Disabled

### What STOPS (zero new data):
1. **`ensemble_snapshots` rows with `source_id="ecmwf_open_data"` and `data_version` in `{"ecmwf_opendata_mx2t3_local_calendar_day_max", "ecmwf_opendata_mn2t3_local_calendar_day_min"}`** — no new rows written after last run date.
2. **`source_run` rows for `source_id="ecmwf_open_data"`** — no new provenance rows.
3. **`source_run_coverage` rows for `source_id="ecmwf_open_data"`** — no new LIVE_ELIGIBLE coverage rows. This is the family-readiness gate: if anything downstream checks `source_run_coverage` for ecmwf_open_data, it will see stale/expired rows.
4. **The ECMWF API download itself** (param `mx2t3` / `mn2t3`) — no HTTP traffic to ECMWF OpenData endpoint.
5. **The `extract_open_ens_localday.py` subprocess** — not invoked.
6. **The `ingest_opendata_startup_catch_up` boot job** — also conditional on `_ingest_main_owns_opendata()`.

### What SURVIVES (unaffected):
1. **TIGGE archive ingest** (`ingest_opendata_daily_mx2t6` is separate from `ingest_tigge_archive_backfill` which runs at 14:00 UTC) — TIGGE writes its own `ensemble_snapshots` rows under `data_version="tigge_mx2t6_local_calendar_day_max"`.
2. **`raw_model_forecasts` pipeline** — bayes_precision_fusion download and replacement forecast production are completely separate.
3. **K2 ingest jobs** (obs, forecasts, solar, HKO) — independent.
4. **The `forecast_live_daemon` replacement forecast jobs** — driven by `raw_model_forecasts`, not `ensemble_snapshots`.
5. **`source_run_coverage` for non-opendata sources** — only ecmwf_open_data writes to this table, so other sources (TIGGE does NOT write source_run_coverage per grep) are not affected. Note: if TIGGE is the fallback forecast source, it has NO coverage rows in `source_run_coverage` regardless — this pre-exists the opendata disable question.
6. **Settlement and observation ingestion** — K2 pipeline unaffected.

### Key risk to confirm before disabling:
> Does any live decision path actually read `ensemble_snapshots` rows filtered to `source_id="ecmwf_open_data"` / `data_version IN (ecmwf_opendata_mx2t3_...)` to produce a buy/sell decision?

If `data_version_priority_for_metric()` [ecmwf_open_data.py line 1896] returns the opendata data_version FIRST (higher priority than tigge), and the decision kernel reads snapshots in that priority order, then stopping opendata means the kernel falls through to TIGGE — provided TIGGE rows are present and within staleness tolerance. If TIGGE rows are also absent or expired, the family goes to NO_FORECAST_AVAILABLE.

The `disable_legacy_opendata_forecast_live_jobs` config key (`config/settings.json:307 = false`) applies to `forecast_live_daemon` (parallel opendata owner), NOT to `ingest_main` scheduler jobs. To disable the `ingest_main` jobs, either:
- Set `ZEUS_FORECAST_LIVE_OWNER=forecast_live` (transfers opendata ownership to the daemon, which then obeys `disable_legacy_opendata_forecast_live_jobs`)
- Or set `disable_legacy_opendata_forecast_live_jobs: true` in settings AND ensure `forecast_live_daemon` is the owner

The cleanest surgical stop is: set `disable_legacy_opendata_forecast_live_jobs: true` + set `ZEUS_FORECAST_LIVE_OWNER=forecast_live`. This makes `_ingest_main_owns_opendata()` return False (no ingest_main jobs registered) AND the daemon's own opendata jobs are disabled by the config flag.

---

## 5. File:Line Summary

| Layer | File | Line(s) |
|-------|------|---------|
| Scheduler registration HIGH | `src/ingest_main.py` | 727, 1729–1730 |
| Scheduler registration LOW | `src/ingest_main.py` | 743, 1731–1732 |
| Scheduler registration BOOT | `src/ingest_main.py` | 780, 1750–1751 |
| Ownership guard | `src/ingest_main.py` | 62–69 |
| Track runner | `src/ingest_main.py` | 759–777 |
| Control plane pause check | `src/ingest_main.py` | 769–771 |
| Lock acquire | `src/data/dual_run_lock.py` | (OPENDATA_DAEMON_LOCK_KEY) |
| Main download + ingest | `src/data/ecmwf_open_data.py` | 1324–1941 |
| TRACKS config (param=mx2t3/mn2t3) | `src/data/ecmwf_open_data.py` | 143–154 |
| `_fetch_one_step` | `src/data/ecmwf_open_data.py` | 1136 |
| `_select_cycle_for_track` | `src/data/ecmwf_open_data.py` | 506 |
| `_ingest_grib_ingest_track` import | `src/data/ecmwf_open_data.py` | 113 |
| `ingest_track` call | `src/data/ecmwf_open_data.py` | 1774 |
| `_write_source_authority_chain` | `src/data/ecmwf_open_data.py` | 836–1130 |
| `write_source_run` (success path) | `src/data/ecmwf_open_data.py` | 937 |
| `write_source_run` (fail/not-released) | `src/data/ecmwf_open_data.py` | 1510, 1579 |
| `write_source_run_coverage` | `src/data/ecmwf_open_data.py` | 1085 |
| `INSERT OR IGNORE INTO ensemble_snapshots` | `scripts/ingest_grib_to_snapshots.py` | 830 |
| `INSERT OR REPLACE INTO source_run` | `src/state/source_run_repo.py` | 98 |
| `INSERT OR REPLACE INTO source_run_coverage` | `src/state/source_run_coverage_repo.py` | 100 |
| data_version HIGH constant | `src/contracts/snapshot_ingest_contract.py` | 42 |
| data_version LOW constant | `src/contracts/snapshot_ingest_contract.py` | 43 |
| Job registry entries | `src/data/source_job_registry.py` | 137–151 |
| Config disable key | `config/settings.json` | 307 |
