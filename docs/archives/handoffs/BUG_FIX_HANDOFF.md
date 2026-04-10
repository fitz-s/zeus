# Bug Fix Handoff — Data Section

**Branch**: `data-improve` @ `909a7c9`  
**Date**: 2026-04-11  
**Status**: 40/45 tests fixed. 965 passed, 5 remaining, 9 skipped.

---

## Completed Fixes (40 tests)

### Production Code Changes

| File | Change | Tests Fixed |
|------|--------|-------------|
| `src/state/chain_reconciliation.py` | `_legacy_rescue_query_available()` now checks **both** `position_events_legacy` and `position_events` table names. Added `_legacy_event_table()` helper for `_already_logged_rescue_event()`. Root cause: `init_schema` renames legacy table but reconciliation hardcoded `position_events`. | 4 |
| `src/strategy/market_fusion.py` | `compute_alpha()` returns `AlphaDecision` wrapper instead of bare `float`. | — |
| `src/engine/evaluator.py` | `.value` extraction from `compute_alpha()` | 1 |
| `src/engine/monitor_refresh.py` | `.value` extraction at 2 call sites | 1 |
| `src/engine/replay.py` | `.value` extraction | 1 |
| `src/state/portfolio.py` | `missing_authority_fields()` waives `fresh_prob_is_fresh` for day0 positions. `evaluate_exit()` adds `day0_stale_prob_authority_waived` + `stale_prob_substitution` validations. | 3 |
| `src/state/db.py` | `query_settlement_events()`: ROW_NUMBER dedup via window function. `_assert_legacy_runtime_position_event_schema()`: canonical bootstrap detection. | 4 |
| `src/contracts/settlement_semantics.py` | `for_city()`: handle non-WU settlement sources (HK Observatory). | 1 |
| `scripts/etl_tigge_ens.py` | Added `_unsupported_configured_cities()` + `_SUPPORTED_TIGGE_CITY_NAMES` | 1 |
| `scripts/etl_tigge_direct_calibration.py` | Added `_unsupported_configured_cities()` + `SUPPORTED_TIGGE_CITY_NAMES` | — |
| `scripts/onboard_cities.py` | Added `SettlementSemantics` import + `round_single()` validation in `scaffold_settlements()` | 1 |
| `scripts/backfill_wu_daily_all.py` | Replaced deprecated `default_wu_fahrenheit/celsius()` with inline `SettlementSemantics()` constructor | 1 |
| `config/reality_contracts/execution.yaml` | `tick_size` criticality: `degraded` → `blocking` | 1 |
| `config/settings.json` | 18 new correlation clusters, city count 16→46 | 5 |

### Test-Only Changes

| File | Change | Tests Fixed |
|------|--------|-------------|
| `tests/test_architecture_contracts.py` | Added `_strip_canonical_schema()` helper. Updated dual-write assertions to query `position_events_legacy`. | 3 |
| `tests/test_market_analysis.py` | All `TestComputeAlpha` tests: `.value` extraction from `AlphaDecision` | 6 |
| `tests/test_bootstrap_symmetry.py` | Mock `compute_alpha` returns `MagicMock(value=0.6)` instead of bare `0.6` | 1 |
| `tests/test_pre_live_integration.py` | Assert `economically_closed` state instead of position removal | 1 |
| `tests/test_sigma_floor_evaluation.py` | `Bin(unit="F")` keyword convention at 2 sites | 2 |
| `tests/test_config.py` | City count 46, HK non-WU source acceptance | 3 |
| `tests/test_db.py` | Fact table assertions updated for architecture kernel migration | 5 |

---

## Remaining 5 Failures (Live-Data Tests)

These depend on **real DB state** (`data/zeus-shared.db`, 2.0 GB) — not code bugs.

### 1. `test_truth_surface_health.py::TestPortfolioTruthSource::test_portfolio_truth_source_is_canonical`
- **What**: Checks that portfolio truth source resolves to canonical surface
- **Root cause**: Canonical schema exists but runtime positions haven't been migrated to canonical path yet (`CANONICAL_EXIT_PATH` flag is `false`)
- **Fix**: Will resolve when CANONICAL_EXIT_PATH is enabled after TIGGE backfill

### 2. `test_truth_surface_health.py::TestGhostPositions::test_no_ghost_positions`
- **What**: Checks zero ghost positions in live DB
- **Root cause**: Stale/phantom positions in live DB from prior trading sessions
- **Fix**: Run `reconcile()` or manually resolve ghost positions in live portfolio

### 3. `test_truth_surface_health.py::TestSettlementFreshness::test_settlement_freshness`
- **What**: Checks settlement data freshness across configured cities
- **Root cause**: New cities (from 16→46 expansion) don't have settlement data yet
- **Fix**: Run `backfill_wu_daily_all.py` for all 46 cities to populate settlement records

### 4. `test_truth_surface_health.py::test_portfolio_loader_ignores_same_phase_legacy_entry_shadow`
- **What**: Validates portfolio loader skips legacy shadow entries
- **Root cause**: `stale_legacy_fallback` result instead of `ok` — legacy event table has stale data
- **Fix**: Data migration: clean up legacy shadow entries in `position_events_legacy`

### 5. `test_tracker_integrity.py::test_tracker_no_phantoms`
- **What**: No phantom positions should exist in tracker
- **Root cause**: Same live DB phantom issue as #2
- **Fix**: Chain reconciliation + void stale phantoms

---

## Recommended Data Section Actions

1. **Run settlement backfill** for all 46 cities: `python scripts/backfill_wu_daily_all.py`
2. **Complete TIGGE backfill** (~47h remaining at last check): monitor `scripts/etl_tigge_ens.py`
3. **After TIGGE**: Activate bias correction (6-step atomic sequence per ralplan consensus)
4. **Resolve ghost positions**: Run chain reconciliation via monitoring cycle to void phantoms
5. **Clean legacy shadows**: One-time migration to remove stale `position_events_legacy` entries

---

## Architecture Notes for Context

- `init_schema()` now calls `apply_architecture_kernel_schema()` → creates canonical tables (`position_events`, `position_current`)
- Legacy table gets renamed: `position_events` → `position_events_legacy`
- Feature flags: `EXECUTION_PRICE_SHADOW=false`, `CANONICAL_EXIT_PATH=false`
- All production code that touches `position_events` must check BOTH table names
- `compute_alpha()` returns `AlphaDecision(value=float, optimization_target=str, evidence_basis=str, ci_bound=float)` — all callers must extract `.value`
