# P3 Implementation Report — K1 Followups
**Date:** 2026-05-14
**Branch:** `fix/k1-p0-daily-obs-redirect-2026-05-14`
**Accumulated on:** P2 tip `7fe2af7bee`
**P3 tip:** `eeac727bb4`
**Executor:** Claude Sonnet 4.6 (session edfd67f6 → continuation)

---

## Topology Doctor Citation

`python3 scripts/topology_doctor.py --planning-lock --plan-evidence docs/operations/task_2026-05-14_k1_followups/IMPLEMENTATION_REVIEW_P2.md --files <changed-files>`

Result: `topology check ok` — confirmed for each commit group (C1, C2, C3, C4).

---

## Commits Delivered

| Commit | SHA | Scope |
|--------|-----|-------|
| P3 C1 | `1eb6cb5b96` | Move PlattModelView + get_active_platt_model → calibration/store.py |
| P3 C2 | `fee68948e5` | Ghost table cleanup script (operator-invoked, dry-run default) |
| P3 C3 | `0e87c75947` | Retire src/contracts/world_view/ + 3 test rewrites |
| P3 C4 | `eeac727bb4` | hole_scanner K1 split fix + A8/INV-37 allowlist + known_gaps entry |

---

## C1 — Caller Migration (PlattModelView + get_active_platt_model)

**Files changed:** `src/calibration/store.py`, `tests/test_phase1_critic_opus_fixes_2026_05_06.py`

**What:** Moved `PlattModelView` dataclass and `get_active_platt_model()` function from `src/contracts/world_view/calibration.py` to `src/calibration/store.py`. Updated test import at line 32 from `src.contracts.world_view.calibration` → `src.calibration.store`.

**Why this location:** `get_active_platt_model` delegates to `load_platt_model_v2` (already in store.py). Moving it here eliminates the cross-module delegation and collocates all Platt model logic.

**Field naming preserved exactly:** `param_A/param_B/param_C` (typed-view shape vs raw `A/B/C` from `load_platt_model_v2`). No shape refactor per advisor constraint.

**Verification:** `python -c "from src.calibration.store import PlattModelView, get_active_platt_model; print('OK')"` → OK. Fix B tests pass (12 passed, 2 skipped).

---

## C2 — Ghost Table Cleanup Script (D2 Operator Runbook)

**Files changed:** `scripts/drop_world_ghost_tables.py` (created)

**What:** Operator-invoked script to drop the 7 LEGACY_ARCHIVED forecast-class ghost copies from world.db. Default: dry-run. Requires explicit `--execute` flag.

**Safety gates:**
1. Registry class check — all 7 ghost tables confirmed LEGACY_ARCHIVED before any drop
2. DB identity sentinel check — refuses to run against non-world.db files
3. 90-day D2 retention window advisory (earliest authorised drop: 2026-08-09)
4. Row-count printout before drop for operator verification

**Ghost tables:** `observations`, `settlements`, `settlements_v2`, `source_run`, `market_events_v2`, `ensemble_snapshots_v2`, `calibration_pairs_v2`

**NOT executed:** This script was created for future operator use. No DB was modified.

---

## C3 — world_view/ Retirement + 3 Test Rewrites (D1)

**Files changed:**
- `src/contracts/world_view/` — DELETED (5 files: `__init__.py`, `calibration.py`, `forecasts.py`, `observations.py`, `settlements.py`)
- `tests/test_no_raw_world_attach.py` — 2 rewrites
- `tests/test_live_safety_invariants.py` — 1 rewrite
- `scripts/check_contract_source_fields.py` — baseline update
- `tests/conftest.py` — WLA allowlist addition

**Test rewrite 1 — `test_world_view_module_exists` → `test_world_view_module_retired`:**
Inverted assertion: from "must exist as approved read path" to "must NOT exist — retired in P3". This is the D1 retirement antibody — prevents accidental re-introduction of the directory.

**Test rewrite 2 — ATTACH error message:**
Updated `test_no_attach_database_in_trading_lane` error string from "use world_view accessors" → "use ConnectionTriple typed accessors".

**Test rewrite 3 — `test_settlement_readers_filter_verified_authority_before_downstream_use`:**
Removed `world_view/settlements.py` read + assertion. Replaced with `src/execution/harvester.py` VERIFIED-filter check (harvester.py uses application-layer `!= "VERIFIED"` guard at lines 505, 793). `replay.py` and `monitor_refresh.py` assertions unchanged.

**check_contract_source_fields.py baseline:**
- Removed 4 world_view/ entries (files deleted)
- Bumped `execution_intent.py` 6→7 (pre-P3 drift, pre-existing; internal-label classified)

**"world_view" string references remaining in test files:** All benign — test method names (`test_world_view_calibration_threads_phase2_keys`) and docstring comments. No live import paths from deleted module.

---

## C4 — hole_scanner K1 Split Fix + A8/INV-37 Resolution

**Files changed:** `src/data/hole_scanner.py`, `tests/state/test_table_registry_coherence.py`, `docs/to-do-list/known_gaps.md`

**Root cause (HIGH silent wrong-answer):** `hole_scanner.main()` opened only `get_world_connection()`. Post-K1, `observations` is on forecasts.db. The `_get_physical_table_keys` method for `DataTable.OBSERVATIONS` would silently return empty (OperationalError swallowed by fallback) — scanner would flood `data_coverage` with spurious MISSING rows for observations it already has.

**Fix:** Added optional `forecasts_conn: Optional[sqlite3.Connection]` to `HoleScanner.__init__`. `_get_physical_table_keys` routes `DataTable.OBSERVATIONS` to `self.forecasts_conn`; all other tables and all writes stay on `self.conn` (world.db). CLI `main()` opens both connections.

**INV-37 antibody catch (A8) — Fitz #3 success story:**
The P1 A8 antibody `test_a8_no_cross_db_write_transaction_in_src` correctly flagged `hole_scanner.main()` for opening both `get_world_connection` and bare `get_forecasts_connection` in the same function. This is exactly what the antibody was built to catch. The resolution: hole_scanner.py added to `WHOLE_FILE_ALLOWLIST` with cited reason — `forecasts_conn` is read-only (`SELECT` only in `_get_physical_table_keys`). No cross-DB write seam exists; this is a genuine A8 false-positive per the test's own documented allowlist policy. The write path (`data_coverage`, `record_missing`, etc.) touches only world.db via `self.conn`.

**Deferred scripts fixes (per PLAN §4.5):**
`scripts/healthcheck.py`, `scripts/verify_truth_surfaces.py`, `scripts/venus_sensing_report.py` — K1-broken hardcoded world.db paths for tables now on forecasts.db. These were already broken pre-P3 (not new breakage). Deferred to a follow-up packet. Entry added to `docs/to-do-list/known_gaps.md` (at file end) with:
- Affected files cited
- Fix pattern: `get_forecasts_connection()` for observations queries (same as hole_scanner P3 fix)
- Live-money impact: LOW (read-only diagnostic scripts)

---

## Boot Wiring Deferral (P4)

PLAN §2 P3 listed `assert_db_matches_registry` boot wiring. This was NOT included in the user's P3 dispatch brief and is explicitly deferred to P4. The wiring would call `assert_db_matches_registry(world_conn, DBIdentity.WORLD)` and `assert_db_matches_registry(forecasts_conn, DBIdentity.FORECASTS)` at daemon boot in `src/main.py`. P4 owns this.

---

## Stop Conditions Status

| Stop # | Description | Status |
|--------|-------------|--------|
| #1 | source_run_coverage cross-DB | CLEARED P1 (world_class confirmed) |
| #8 | ATTACH raw sqlite_master | CLOSED P2 (registry-driven) |
| #13 | executable_forecast_reader symbols | CONFIRMED ABSENT (evaluator.py:308, schema_introspection.py:40) |

---

## Invariant Compliance

- **INV-37:** A8 antibody fired correctly. Resolved via allowlist (read-only false-positive). 0 unresolved INV-37 violations.
- **INV-05:** Not touched in P3 (boot wiring deferred to P4).
- **D1 (world_view retirement):** Complete — directory deleted, tests inverted, imports updated.
- **D2 (ghost table retain):** Script created; 90-day window enforced; no DB modified.

---

## Test Results at P3 Tip

All tests pass except 2 pre-existing failures (documented in IMPLEMENTATION_REVIEW_P2.md):
- `test_no_get_trade_connection_with_world_in_trading_lane` — pre-existing (replay_selection_coverage.py uses legacy helper; out of P3 scope)
- `test_refit_per_bucket_savepoint_isolation` — pre-existing (requires sentinel row in zeus_meta)
