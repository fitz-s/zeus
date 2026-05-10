# Track A.6 Run Record — db_writer_lock Daemon-Path Retrofit
**Date:** 2026-05-08
**Branch:** fix/246-track-a6-daemon-retrofit-2026-05-08
**PR:** #96
**Authority:** .omc/plans/track_a6_daemon_path_retrofit_2026_05_08.md

---

## Plan Items Completed

| Plan Item | Status |
|---|---|
| Change 1: wrap `_ingest_track()` in `tigge_pipeline.py` with BULK lock | DONE |
| Change 2: wrap ingest stage in `ecmwf_open_data.collect_open_ens_cycle()` with conditional BULK lock | DONE |
| Change 3: annotate 3 Group 2 raw-connect sites in `SQLITE_CONNECT_ALLOWLIST` | DONE |
| New test file `tests/test_daemon_ingest_acquires_bulk_lock.py` (R-1, R-2, R-3) | DONE |

---

## Sites Retrofitted

| File | Line (approx) | Change |
|---|---|---|
| `src/data/tigge_pipeline.py` | 60–61 (imports) | Added top-level `from src.state.db import ZEUS_WORLD_DB_PATH` and `from src.state.db_writer_lock import WriteClass, db_writer_lock` |
| `src/data/tigge_pipeline.py` | ~365 | Wrapped `conn = get_world_connection()` … `conn.close()` block with `with db_writer_lock(ZEUS_WORLD_DB_PATH, WriteClass.BULK):` — lock acquired BEFORE connection open |
| `src/data/ecmwf_open_data.py` | 50 (import) | Added `from contextlib import nullcontext` |
| `src/data/ecmwf_open_data.py` | 70–71 (imports) | Added `ZEUS_WORLD_DB_PATH` to db import; added `from src.state.db_writer_lock import WriteClass, db_writer_lock` |
| `src/data/ecmwf_open_data.py` | ~609–675 | Replaced `own_conn / if own_conn: conn = get_connection() / try:` block with conditional lock: `_lock_ctx = db_writer_lock(...)` when `own_conn`, else `nullcontext()`. Full ingest block (conn open → commit → conn close) inside `with _lock_ctx:` |
| `src/state/db_writer_lock.py` | `SQLITE_CONNECT_ALLOWLIST` | Added `src/ingest_main.py` (RO), `src/observability/status_summary.py` (RO), `src/riskguard/discord_alerts.py` (WRITE risk_state.db only) with rationale comments |
| `tests/conftest.py` | `_WLA_SQLITE_CONNECT_ALLOWLIST` | Updated comments for the 3 sites above: `pending_track_a6` → Track A.6 resolved annotation |

---

## Tests Added

**File:** `tests/test_daemon_ingest_acquires_bulk_lock.py`

| Test | Result |
|---|---|
| `test_tigge_ingest_track_acquires_bulk_lock` (R-1) | PASS |
| `test_opendata_collect_ens_cycle_acquires_bulk_lock` (R-2) | PASS |
| `test_opendata_collect_ens_cycle_skips_lock_for_injected_conn` (R-3) | PASS |

---

## AST Gate Result

`tests/test_db_writer_lock.py::test_wla_antibody_fires_on_new_unguarded_site` — **PASS**

Full targeted run: `pytest tests/test_daemon_ingest_acquires_bulk_lock.py tests/test_db_writer_lock.py tests/test_tigge_daily_ingest.py` → **39 passed**

Pre-existing failures on main (not introduced here):
- `tests/test_opendata_writes_v2_table.py::test_opendata_high_payload_lands_in_v2` — PHYSICAL_QUANTITY_MISMATCH (pre-existing, identical on main)
- `tests/test_opendata_writes_v2_table.py::test_collect_open_ens_cycle_writes_authority_chain_readable_by_live_reader` — same

---

## PR Link

https://github.com/fitz-s/zeus/pull/96
