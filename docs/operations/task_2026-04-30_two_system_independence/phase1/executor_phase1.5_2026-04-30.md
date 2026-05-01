# Phase 1.5 Executor Report — Harvester Split

**Date:** 2026-04-30
**Executor:** claude-sonnet-4-6 (Executor agent)
**Authority basis:** design.md §5 Phase 1.5

---

## Files Created / Modified

| File | Action | Lines |
|---|---|---|
| `src/ingest/harvester_truth_writer.py` | CREATED | 563 |
| `src/execution/harvester_pnl_resolver.py` | CREATED | 174 |
| `tests/test_harvester_split_independence.py` | CREATED | 229 |
| `src/data/dual_run_lock.py` | MODIFIED | +4 lines (added `harvester_truth`, `harvester_pnl` to `_KNOWN_TABLES`) |
| `src/ingest_main.py` | MODIFIED | +24 lines (`_harvester_truth_writer_tick` function + scheduler registration at minute=45) |
| `src/main.py` | MODIFIED | +20 lines (`_harvester_cycle` body replaced with pnl_resolver call + lock + fallback) |
| `tests/test_world_writer_boundary.py` | MODIFIED | +2 lines (added `src/ingest/harvester_truth_writer.py` to allowlist) |

---

## What Was Done

**A. `src/ingest/harvester_truth_writer.py`** — Ingest-side settlement truth writer. Contains an ingest-local copy of `_write_settlement_truth` logic (no import from `src.execution.harvester` to avoid circular reference). Entry point: `write_settlement_truth_for_open_markets(world_conn, *, dry_run)`. Feature-flag preserved. Writes only `settlements`, `settlements_v2`, `market_events_v2` via `world_conn`.

**B. `src/execution/harvester_pnl_resolver.py`** — Trading-side P&L resolver. Reads `world.settlements` (VERIFIED rows, last 200) via `world_conn`. Calls existing `_settle_positions` from `harvester.py` and `store_settlement_records` for `decision_log`. Returns `awaiting_truth_writer` status when no VERIFIED settlements found. Writes NO world tables.

**C. `src/data/dual_run_lock.py`** — Added `harvester_truth` and `harvester_pnl` to `_KNOWN_TABLES` frozenset.

**D. `src/ingest_main.py`** — Added `_harvester_truth_writer_tick` function + scheduler registration at `cron minute=45` (offset from trading's minute=0 to reduce contention). Acquires `acquire_lock("harvester_truth")`.

**E. `src/main.py`** — Replaced `_harvester_cycle` body: now acquires `acquire_lock("harvester_pnl")` and calls `resolve_pnl_for_settled_markets`. ImportError fallback to legacy `run_harvester()` for backward compat during transition.

**F. `tests/test_world_writer_boundary.py`** — Added `src/ingest/harvester_truth_writer.py` to allowlist alongside legacy `src/execution/harvester.py`.

**G. `tests/test_harvester_split_independence.py`** — Antibody #12, 4 tests: AST import scan (truth writer forbidden from trading modules; pnl resolver forbidden from ingest_main/scripts.ingest), grep write-verb scan (truth writer doesn't write trade tables; pnl resolver doesn't write world tables).

---

## Test Results

```
tests/test_harvester_split_independence.py  4 passed
tests/test_world_writer_boundary.py         3 passed
tests/test_dual_run_lock_obeyed.py          3 passed
tests/test_trading_isolation.py             3 passed
tests/test_platt.py                        28 passed
tests/test_phase4_platt_v2.py               7 passed
tests/test_load_platt_v2_data_version_filter.py 4 passed
tests/test_evaluator_explicit_n_mc.py       4 passed
tests/test_runtime_n_mc_floor.py            3 passed
tests/test_live_safe_strategies.py         12 passed
Total: 69 passed, 0 failed
```

## Blockers Encountered

None. One constraint: `harvester_truth_writer.py` copies helper functions from `harvester.py` (`_write_settlement_truth`, `_lookup_settlement_obs`, `_canonical_bin_label`, etc.) rather than importing them, to avoid `src.execution` circular dependency. The design doc anticipated this ("copy the needed logic; if too much, refactor common helpers into `src/execution/harvester_common.py` first"). The copy is ~180 lines of pure logic with no new abstractions; `harvester_common.py` refactor is deferred to Phase 3 when `harvester.py` is deprecated anyway.
