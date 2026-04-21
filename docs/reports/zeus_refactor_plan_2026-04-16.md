# Zeus Incremental Refactor Plan

> LEGACY EXTRACTION SOURCE - NOT DEFAULT READ.
> Durable architecture orientation has been extracted into
> `docs/reference/zeus_architecture_reference.md`; durable failure classes route
> through `docs/reference/zeus_failure_modes_reference.md`. This plan is
> historical planning evidence, not active work authority.
>
> **Version:** 1.0
> **Author:** Venus/Mars collaborative session
> **Date:** 2026-04-16
> **Branch:** `data-improve` (base) → feature branches per phase
> **Companion docs:** `docs/artifacts/zeus_architecture_deep_map_2026-04-16.md`, `docs/reports/zeus_system_constitution_2026-04-16.md`, `docs/reports/zeus_pathology_registry_2026-04-16.md`
> **Status:** PLANNING — no code changes until Phase 0 acceptance criteria are defined

---

## 1. Executive Summary

### 1.1 Current State

Zeus is a live weather probability trading system on Polymarket — 116 source files, ~37K LOC, 117 test files (~49K LOC). It trades 51 cities across 35 timezones with 4 strategies on a $150 bankroll ($5 safety cap). The system is architecturally sound in its probability chain and K-zone governance, but has accumulated structural debt across 6 confirmed pathologies, 7 contamination vectors, and 7 time bombs.

**What works well:**
- Probability chain (ENS → MC → Platt → posterior → Kelly) is mathematically rigorous
- K-zone governance (K0–K4) and forbidden import rules are well-defined
- Contracts layer (20 files, 2,517 LOC) provides strong typed boundaries
- 80+ test files covering critical invariants, behavioral contracts, and structural linting
- Event-sourced position ledger (append-only `position_events` + derived `position_current`)

**What needs work:**
- `db.py` god file: 4,472 LOC, 41 CREATE TABLE, all queries, all connections
- `evaluator.py` monolith: 892-line `evaluate_candidate()` function
- `portfolio.py` god object: Position dataclass with 79+ fields
- Opaque DI: `deps=sys.modules[__name__]` pattern across 7+ call sites
- Dual-write order inversion: portfolio JSON saved before DB commit (P1/C6)
- Phantom position persistence: chain recon `skip_voiding` on empty API (P14/C3)
- Monitor hardcodes `model_agreement="AGREE"` creating asymmetric entry/exit α (P9)
- No test coverage for K0 ledger/projection/lifecycle modules

### 1.2 Target State

After all 8 phases:
- `db.py` decomposed into repository modules per table group (~300 LOC each)
- Probability chain extracted as a pure, testable pipeline module
- Position decomposed: identity + state + monitor + exit evaluator
- Type-safe DI replacing `deps=sys.modules[__name__]`
- All CRITICAL/HIGH pathologies (P1, P9, P14) eliminated
- 100% test coverage on K0 (ledger, projection, lifecycle) and probability chain
- Structured logging with consistent fields
- Formal state machines for cycle runner and risk FSM

### 1.3 Constraints

| Constraint | Implication |
|------------|-------------|
| **Live system** | Cannot stop trading during refactor. Every commit must be deployable. |
| **$150 bankroll / $5 cap** | Low capital = low blast radius, but any loss is proportionally large. |
| **Single daemon process** | No blue-green deployment. Restart = brief downtime (~5s). |
| **SQLite WAL** | Schema changes must be additive (new columns with defaults, never remove). |
| **117 existing tests** | Zero test regressions. New tests add, never subtract. |
| **K-zone governance** | K0 changes require schema packet + invariant proof. K3 math changes require numerical equivalence proof. |
| **APScheduler daemon** | Job graph changes must preserve scheduling semantics. |
| **Branch strategy** | `data-improve` is base. Each phase gets a feature branch. Merge back after acceptance. |

---

## 2. Principles

### 2.1 Incremental Delivery
Every phase produces a deployable system. No phase requires the next phase to function. Each phase's output is strictly superior to its input — never a lateral move.

### 2.2 Test-First
For every file touched, write the parity test BEFORE the refactor. The parity test captures current behavior (inputs → outputs) so the refactor can be verified against it. Tests are the antibodies — if a refactor breaks something, the test must catch it before merge.

### 2.3 Contract-Preserving
The 8 mathematical invariants (INV-01 through INV-08), 10 behavioral invariants (BEH-01 through BEH-10), and 7 structural invariants (STR-01 through STR-07) defined in the constitution are non-negotiable. Any refactor that changes these is not a refactor — it's a redesign and requires a separate decision.

### 2.4 No-Downtime
Zeus daemon restarts take ~5 seconds (schema init + wallet check + scheduler start). Refactors that only change internal structure can be deployed with a single restart. Schema migrations must be backward-compatible (new code reads old schema, old code reads new schema).

### 2.5 Rollback-First
Before starting each phase, verify that `git checkout data-improve` + daemon restart returns to pre-refactor state. No phase may create irreversible schema changes. If a phase adds columns, the old code must tolerate their presence.

### 2.6 Relationship Tests Over Function Tests
Per the core methodology: test the relationship between modules, not just individual functions. If Module A's output flows into Module B, the parity test must verify "property X survives the A→B boundary." The existing `test_cross_module_invariants.py` and `test_cross_module_relationships.py` are the canonical examples.

---

## 3. Migration Order

### 3.1 Dependency Graph (Text)

```
Phase 0: Foundation (no behavior change)
    │
    ├── Phase 1: Data Layer ──────────────────┐
    │                                         │
    ├── Phase 2: Core Math Extraction ────────┤
    │       (depends on Phase 0 types)        │
    │                                         │
    ├── Phase 3: Risk & Sizing ───────────────┤
    │       (depends on Phase 2 math)         │
    │                                         │
    └── Phase 4: Execution Layer ─────────────┤
            (depends on Phase 1 DB)           │
                                              │
    Phase 5: Strategy Layer ──────────────────┤
        (depends on Phases 2, 3, 4)           │
                                              │
    Phase 6: Scheduler & Orchestration ───────┤
        (depends on Phase 5)                  │
                                              │
    Phase 7: Observability & Operations ──────┘
        (independent, can run in parallel with 5-6)
```

### 3.2 Parallelization Opportunities

- **Phase 1 and Phase 2** can run in parallel (data layer and math have no cross-dependencies)
- **Phase 7** can start as early as Phase 1 (observability is additive)
- **Phase 3** requires Phase 2 (math extraction) to complete
- **Phases 4, 5, 6** are sequential (each builds on the previous)

### 3.3 Critical Path

```
Phase 0 → Phase 2 → Phase 3 → Phase 5 → Phase 6
```

This is the longest dependency chain. Phase 1 and Phase 7 can be interleaved with the critical path.

---

## 4. Invariant Preservation Checklist

Every phase must verify these invariants are preserved. The checklist is cumulative — later phases must also pass all earlier checks.

### 4.1 Mathematical Invariants

| ID | Function | Test File | Phases That Touch It |
|----|----------|-----------|---------------------|
| INV-01 | `p_raw_vector_from_maxes()` | `test_cross_module_invariants.py`, `test_ensemble_signal.py` | 2 |
| INV-02 | `round_wmo_half_up_values()` | `test_cross_module_invariants.py` | 1 (P16 fix) |
| INV-03 | `calibrate_and_normalize()` | `test_platt.py`, `test_calibration_manager.py` | 2 |
| INV-04 | `compute_posterior()` | `test_cross_module_invariants.py` | 2 |
| INV-05 | `kelly_size()` | `test_kelly.py`, `test_kelly_live_safety_cap.py` | 3 |
| INV-06 | `dynamic_kelly_mult()` | `test_kelly_cascade_bounds.py` | 3 |
| INV-07 | `fdr_filter()` / `benjamini_hochberg_mask()` | `test_fdr.py` | 2 |
| INV-08 | `bin_probability_from_values()` | `test_market_analysis.py` | 2 |

### 4.2 Behavioral Invariants

| ID | Behavior | Test File | Phases That Touch It |
|----|----------|-----------|---------------------|
| BEH-01 | Entry gates (8 conditions) | `test_auto_pause_entries.py` | 5, 6 |
| BEH-02 | Exit triggers (8 layers) | `test_churn_defense.py`, `test_exit_authority.py` | 4 |
| BEH-03 | Risk levels | `test_riskguard.py` | 3 |
| BEH-04 | Chain reconciliation | `test_lifecycle.py` | 1, 4 |
| BEH-05 | Settlement flow | `test_pnl_flow_and_audit.py` | 1, 4 |
| BEH-06 | Gate_50 irrevocability | `test_riskguard.py` | 3 |
| BEH-07 | Mode rejection | `test_config.py` | None |
| BEH-08 | Wallet fail-closed | `test_wallet_source.py` | None |
| BEH-09 | Limit-only enforcement | `test_executor.py` | 4 |
| BEH-10 | Authority violation on UNVERIFIED | `test_authority_gate.py` | 1, 2 |

### 4.3 Structural Invariants

| ID | Invariant | Phases That Touch It |
|----|-----------|---------------------|
| STR-01 | `position_events` append-only | 1 |
| STR-02 | `position_current` is derived projection | 1 |
| STR-03 | `EpistemicContext.decision_time_utc` tz-aware | None |
| STR-04 | `Bin.__post_init__` validates | None |
| STR-05 | `validate_bin_topology()` partition | None |
| STR-06 | `_cycle_lock` serializes discovery | 6 |
| STR-07 | Chain recon before trading | 6 |

---

## 5. Parity Test Strategy

### 5.1 What Is a Parity Test

A parity test captures the **exact input→output mapping** of a function before refactoring, then asserts the refactored version produces identical output. It is NOT a unit test (which tests correctness against a spec) — it tests equivalence between old and new implementations.

### 5.2 Parity Test Protocol

For each function being refactored:

1. **Capture**: Run the old function with representative inputs. Serialize outputs (JSON, pickle, or inline fixtures).
2. **Freeze**: Commit the parity test with captured outputs to the feature branch.
3. **Refactor**: Modify the function.
4. **Verify**: Run the parity test. Outputs must match exactly (within floating-point tolerance for math functions: `rtol=1e-12`).
5. **Promote**: If parity holds, the parity test becomes a permanent regression test.

### 5.3 Parity Test Template

```python
# tests/parity/test_parity_{module}.py
"""Parity test: {function_name} produces identical output before/after refactor."""
import numpy as np
from src.{module} import {function_name}

# Frozen inputs captured from live system
FROZEN_INPUTS = { ... }
FROZEN_OUTPUTS = { ... }

def test_{function_name}_parity():
    result = {function_name}(**FROZEN_INPUTS)
    np.testing.assert_allclose(result, FROZEN_OUTPUTS, rtol=1e-12)
```

### 5.4 What Needs Parity Tests

| Module | Function(s) | Phase |
|--------|-------------|-------|
| `ensemble_signal.py` | `p_raw_vector_from_maxes()` | 2 |
| `platt.py` | `calibrate_and_normalize()` | 2 |
| `market_fusion.py` | `compute_posterior()`, `compute_alpha()` | 2 |
| `kelly.py` | `kelly_size()`, `dynamic_kelly_mult()` | 3 |
| `fdr_filter.py` | `benjamini_hochberg_mask()` | 2 |
| `evaluator.py` | `evaluate_candidate()` | 2, 5 |
| `portfolio.py` | `evaluate_exit()` | 4 |
| `chain_reconciliation.py` | `reconcile()` | 4 |
| `harvester.py` | `harvest()` settlement flow | 4 |
| `riskguard.py` | Risk level computation | 3 |

---

## 6. Kill Switch Protocol

### 6.1 Per-Phase Kill Switch

At any point during a phase, the refactor can be halted and reverted:

```bash
# 1. Stop daemon
launchctl unload ~/Library/LaunchAgents/com.openclaw.zeus.plist

# 2. Revert to pre-refactor
cd workspace-venus/zeus
git checkout data-improve
git branch -D feature/phase-N-{name}  # optional cleanup

# 3. Restart daemon
launchctl load ~/Library/LaunchAgents/com.openclaw.zeus.plist
```

### 6.2 Schema Rollback

All schema changes are additive (new columns with DEFAULT, new tables with IF NOT EXISTS). Old code on `data-improve` will:
- Ignore new columns (SQLite allows extra columns)
- Ignore new tables (never referenced by old code)
- Continue using old connection patterns

### 6.3 Data Rollback

If a phase corrupts data:
1. **portfolio.json**: Rebuild from `position_events` via `nuke_rebuild_projections.py`
2. **zeus_trades.db**: Restore from backup (operator must back up before each phase)
3. **zeus-world.db**: Re-run ETL pipeline (`_etl_recalibrate()`)
4. **risk_state.db**: Delete and let RiskGuard rebuild from scratch on next tick
5. **calibration_pairs/platt_models**: Re-run `refit_platt.py`

### 6.4 Pre-Phase Backup Protocol

Before starting ANY phase:

```bash
cd workspace-venus/zeus/state
cp zeus_trades.db zeus_trades.db.pre-phase-N
cp zeus-world.db zeus-world.db.pre-phase-N
cp portfolio.json portfolio.json.pre-phase-N
cp risk_state.db risk_state.db.pre-phase-N
```

---

## 7. Phase 0: Foundation

### 7.1 Objective

Prepare the codebase for safe refactoring: remove dead code, add type annotations to critical interfaces, close test gaps on K0 modules, and standardize logging — all with zero behavior change.

### 7.2 Branch

`feature/phase-0-foundation` from `data-improve`

### 7.3 Preconditions

- All 117 existing tests pass (`python -m pytest tests/ -x`)
- Daemon is stopped (refactor work, not live changes)
- State backups taken per §6.4

### 7.4 Work Items

#### 7.4.1 Dead Code Removal

| Item | File(s) | Action | LOC Impact |
|------|---------|--------|------------|
| Remove `ecmwf_open_data.py` if confirmed dead | `src/data/ecmwf_open_data.py` (139 LOC) | Delete if TIGGE is sole ENS path (verify §16 of deep map). Remove import from `main.py`. | −139 |
| Remove `zeus.db` references | `src/state/db.py` | Remove legacy `get_legacy_connection()` if exists; remove `zeus.db` from connection patterns | −20 est. |
| Remove semantic provenance dead code | `src/execution/harvester.py`, others | Remove `if False: _ = None.selected_method` patterns (P16 cosmetic) | −30 est. |
| Remove `ExitContext.missing_authority_fields` duplicate append | `src/state/portfolio.py` L103-107 | Fix P15: remove second `missing.append("fresh_prob_is_fresh")` | −1 |
| Audit `src/calibration/effective_sample_size.py` SHADOW_ONLY | `src/calibration/effective_sample_size.py` | Confirm SHADOW_ONLY = True. If no live callers, add `_SHADOW_ONLY` flag check at module boundary. | 0 |
| Audit `src/calibration/blocked_oos.py` SHADOW_ONLY | `src/calibration/blocked_oos.py` | Same as above. | 0 |

#### 7.4.2 Type Annotations for K0/K1 Interfaces

| Module | Functions to Annotate | Zone |
|--------|-----------------------|------|
| `src/state/ledger.py` | `append_event_and_project()`, `append_many_and_project()` | K0 |
| `src/state/projection.py` | `upsert_position_current()`, `validate_event_projection_pair()` | K0 |
| `src/state/lifecycle_manager.py` | All 9 `enter_*` transition functions + `fold_lifecycle_phase()` | K0 |
| `src/engine/lifecycle_events.py` | All event builder functions | K0/K2 |
| `src/contracts/settlement_semantics.py` | `round_wmo_half_up_values()`, `SettlementSemantics` | K0 |
| `src/riskguard/policy.py` | `resolve_strategy_policy()` | K1 |
| `src/riskguard/risk_level.py` | `overall_level()`, `RiskLevel` | K1 |

#### 7.4.3 Test Gap Closure (K0 Critical Path)

| Module | Current Coverage | Tests to Write | Priority |
|--------|-----------------|----------------|----------|
| `src/state/ledger.py` | **NO TESTS** | `test_ledger.py`: append event, idempotent projection, sequence_no uniqueness, concurrent append | CRITICAL |
| `src/state/projection.py` | **NO TESTS** | `test_projection.py`: upsert creates/updates, event-projection pair validation | CRITICAL |
| `src/state/lifecycle_manager.py` | **NO TESTS** | `test_lifecycle_manager.py`: all legal transitions, illegal transition rejection, phase folding | CRITICAL |
| `src/engine/lifecycle_events.py` | **NO TESTS** | `test_lifecycle_events.py`: event builders produce valid schemas | HIGH |
| `src/execution/fill_tracker.py` | **NO TESTS** | `test_fill_tracker.py`: entry fill, entry void, grace period, quarantine on DB failure | HIGH |
| `src/data/polymarket_client.py` | **NO TESTS** | `test_polymarket_client.py`: credential resolution mock, lazy init, error handling | MEDIUM |

#### 7.4.4 Logging Standardization

| Action | Scope |
|--------|-------|
| Audit all `except Exception: pass` patterns (7 in src/) | System-wide |
| Replace `except Exception: pass` in `fill_tracker.py` with logged + flagged errors | `src/execution/fill_tracker.py` |
| Replace `datetime.utcnow()` with `datetime.now(timezone.utc)` | `src/state/db.py` L2419 |
| Ensure all loggers use `zeus.*` hierarchy | System-wide audit |

### 7.5 Files Touched

```
src/state/ledger.py                    (type annotations)
src/state/projection.py                (type annotations)
src/state/lifecycle_manager.py         (type annotations)
src/engine/lifecycle_events.py         (type annotations)
src/contracts/settlement_semantics.py  (type annotations)
src/riskguard/policy.py                (type annotations)
src/riskguard/risk_level.py            (type annotations)
src/state/portfolio.py                 (P15 fix, 1 line)
src/execution/fill_tracker.py          (exception handling)
src/state/db.py                        (utcnow fix)
src/data/ecmwf_open_data.py            (remove if dead)
src/execution/harvester.py             (dead code cleanup)
src/main.py                            (remove dead import if ecmwf_open_data removed)
tests/test_ledger.py                   (NEW)
tests/test_projection.py               (NEW)
tests/test_lifecycle_manager.py        (NEW)
tests/test_lifecycle_events.py         (NEW)
tests/test_fill_tracker.py             (NEW)
tests/test_polymarket_client.py        (NEW)
```

### 7.6 Acceptance Criteria

1. All 117 existing tests pass (zero regressions)
2. New K0 tests pass (ledger, projection, lifecycle_manager, lifecycle_events)
3. New fill_tracker tests pass
4. `python -m pytest tests/ -x --tb=short` exits 0
5. No `except Exception: pass` remains in `fill_tracker.py`
6. No `datetime.utcnow()` calls remain in `src/`
7. All K0/K1 interface functions have type annotations
8. `test_architecture_contracts.py` passes (import boundaries intact)

### 7.7 Rollback Plan

```bash
git checkout data-improve
# No schema changes in Phase 0 — instant rollback
```

### 7.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Type annotations cause runtime errors | LOW | LOW | Annotations are not enforced at runtime unless `from __future__ import annotations` is used |
| Dead code removal breaks an untested path | MEDIUM | LOW | Grep all imports before deleting; run full test suite |
| New tests reveal existing bugs | HIGH | POSITIVE | This is a feature, not a bug. Document findings, don't fix in Phase 0. |

### 7.9 Estimated Scope

- Files modified: ~15
- Files created: ~6 (new test files)
- LOC removed: ~190 (dead code)
- LOC added: ~800 (tests + annotations)
- Net: +610 LOC (mostly tests)

### 7.10 Dependencies

None. Phase 0 is the foundation — all other phases depend on it.

---

## 8. Phase 1: Data Layer

### 8.1 Objective

Normalize the database layer: decompose `db.py` god file, remove dual-write artifacts, consolidate settlement sources, and add data validation contracts.

### 8.2 Branch

`feature/phase-1-data-layer` from `data-improve` (after Phase 0 merged)

### 8.3 Preconditions

- Phase 0 complete and merged
- All K0 tests pass (ledger, projection, lifecycle)
- State backups taken per §6.4

### 8.4 Work Items

#### 8.4.1 Decompose `db.py` (4,472 LOC → 6-8 modules)

Split by table group and responsibility:

| New Module | Tables Owned | Functions Moved | Approx LOC |
|------------|-------------|-----------------|------------|
| `src/state/db_connections.py` | None (connection management) | `get_trade_connection()`, `get_world_connection()`, `get_backtest_connection()`, `get_trade_connection_with_world()`, WAL/FK pragmas | ~150 |
| `src/state/db_schema.py` | All (DDL only) | `init_schema()`, `apply_architecture_kernel_schema()`, table existence checks | ~400 |
| `src/state/db_world.py` | observations, ensemble_snapshots, calibration_pairs, platt_models, settlements, solar_daily, data_coverage, control_overrides | All world-data queries | ~800 |
| `src/state/db_trades.py` | trade_decisions, chronicle, position_events, position_current, execution_log | All trade-data queries | ~800 |
| `src/state/db_derived.py` | forecast_skill, model_bias, diurnal_curves, temp_persistence, etc. | ETL-derived table queries | ~500 |
| `src/state/db_risk.py` | risk_state, risk_actions, alert_cooldown | RiskGuard-related queries | ~200 |
| `src/state/db.py` | None (re-export facade) | Re-exports all public functions for backward compatibility | ~100 |

**Migration strategy:** The old `db.py` becomes a thin re-export facade (`from .db_connections import *`, etc.) so all existing imports continue to work. Callers migrate to direct imports in subsequent phases.

#### 8.4.2 Fix Dual-Write Order Inversion (P1/C6)

**Current (harvester.py L293-300):**
```python
save_portfolio(portfolio)    # JSON first
trade_conn.commit()          # DB second
shared_conn.commit()         # DB third
```

**Fix:**
```python
trade_conn.commit()          # DB first
shared_conn.commit()         # DB second
save_portfolio(portfolio)    # JSON last (derived from DB)
```

**Files:** `src/execution/harvester.py`
**Test:** Add to `test_pnl_flow_and_audit.py`: crash simulation between commit and save should not corrupt state.

#### 8.4.3 Fix Harvester WMO Rounding (P16)

**Current (harvester.py L680):**
```python
settlement_value = round(float(settlement_value))  # banker's rounding
```

**Fix:**
```python
from src.contracts.settlement_semantics import round_wmo_half_up_value
settlement_value = round_wmo_half_up_value(float(settlement_value))
```

**Files:** `src/execution/harvester.py`
**Test:** Add case for half-integer settlement values (e.g., 60.5°F) to `test_pnl_flow_and_audit.py`.

#### 8.4.4 Fix Hardcoded WU API Key (Security)

**Current (wu_daily_collector.py L24):**
```python
WU_API_KEY = "6532d6454b8aa370768e63d6ba5a832e"
```

**Fix:**
```python
WU_API_KEY = os.environ.get("WU_API_KEY", "")
if not WU_API_KEY:
    raise SystemExit("WU_API_KEY environment variable not set")
```

**Files:** `src/data/wu_daily_collector.py`

#### 8.4.5 Add Data Validation Contracts

| Contract | Purpose | File |
|----------|---------|------|
| `ObservationRecord` | Typed observation with authority, source, provenance | `src/contracts/observation_record.py` (NEW) |
| `EnsembleRecord` | Typed ensemble snapshot with member count validation | `src/contracts/ensemble_record.py` (NEW) |
| `CalibrationPairRecord` | Typed calibration pair with decision_group_id | `src/contracts/calibration_pair_record.py` (NEW) |

These are frozen dataclasses with `__post_init__` validation. All write paths to the corresponding tables must construct these records, preventing invalid data from entering the DB.

#### 8.4.6 Add Harvester Concurrency Guard (TB-7)

**Current (main.py):** Harvester job has no `max_instances` guard.

**Fix:** Add `max_instances=1` to the harvester job in APScheduler configuration.

**Files:** `src/main.py`

#### 8.4.7 Consolidate Settlement Source Map

Fix the 4 known discrepancies documented in §8 of the deep map:
1. Istanbul: Update `cities.json` to `settlement_source_type: "ogimet_metar"`
2. Moscow: Same fix
3. Tel Aviv: Update to `settlement_source_type: "wu_icao"`
4. Taipei: Remove or document `cwa_station` field in City dataclass if unused

**Files:** `config/cities.json`, `src/config.py`

### 8.5 Files Touched

```
src/state/db.py                           (decompose → facade)
src/state/db_connections.py               (NEW — connection management)
src/state/db_schema.py                    (NEW — DDL)
src/state/db_world.py                     (NEW — world data queries)
src/state/db_trades.py                    (NEW — trade data queries)
src/state/db_derived.py                   (NEW — ETL-derived queries)
src/state/db_risk.py                      (NEW — risk queries)
src/execution/harvester.py                (P1 fix, P16 fix)
src/data/wu_daily_collector.py            (hardcoded key fix)
src/contracts/observation_record.py       (NEW)
src/contracts/ensemble_record.py          (NEW)
src/contracts/calibration_pair_record.py  (NEW)
src/main.py                               (TB-7 harvester guard)
config/cities.json                        (settlement source fixes)
src/config.py                             (CWA field audit)
tests/test_db_decomposition.py            (NEW — verify re-exports work)
tests/parity/test_parity_db_queries.py    (NEW — query output parity)
```

### 8.6 Acceptance Criteria

1. All 117+ tests pass (Phase 0 tests included)
2. `db.py` is < 200 LOC (re-export facade only)
3. All callers of `db.py` functions work without modification (re-export facade)
4. `save_portfolio()` call is AFTER both `commit()` calls in harvester
5. Harvester uses `round_wmo_half_up_value()` for settlement values
6. No hardcoded API keys in source
7. Harvester job has `max_instances=1`
8. Settlement source types in `cities.json` match actual implementation

### 8.7 Rollback Plan

```bash
git checkout data-improve
# db.py facade re-exports mean old imports still work
# Schema is unchanged — no DB rollback needed
```

### 8.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `db.py` decomposition breaks an import | MEDIUM | LOW | Re-export facade catches all; full test suite validates |
| P1 fix changes settlement timing | LOW | MEDIUM | Parity test: same inputs → same settlement records |
| WU API key env var not set in production | MEDIUM | HIGH | Add to launchd plist env vars before deploying |

### 8.9 Estimated Scope

- Files modified: ~8
- Files created: ~10 (new modules + tests)
- LOC removed: ~4,000 (from db.py, moved to new modules)
- LOC added: ~5,000 (new modules + validation contracts + tests)
- Net: +1,000 LOC (mostly moved, not new)

### 8.10 Dependencies

Phase 0 must complete first.

---

## 9. Phase 2: Core Math Extraction

### 9.1 Objective

Extract the probability chain (ENS → MC → Platt → posterior → edge → FDR) into a pure, testable pipeline module decoupled from DB access and side effects.

### 9.2 Branch

`feature/phase-2-math-extraction` from `data-improve` (after Phase 0 merged)

### 9.3 Preconditions

- Phase 0 complete and merged
- All K0 tests and parity tests pass
- Probability chain functions have frozen parity test fixtures

### 9.4 Work Items

#### 9.4.1 Create Probability Pipeline Module

Extract the complete probability chain into `src/math/probability_pipeline.py`:

```python
# src/math/probability_pipeline.py
"""Pure probability chain — no DB, no I/O, no side effects."""

@dataclass(frozen=True)
class ProbabilityInput:
    member_maxes: np.ndarray          # (51,) float
    bins: list[Bin]                    # market bin topology
    settlement_semantics: SettlementSemantics
    calibrator: PlattParams | None     # None → use P_raw
    lead_days: float
    market_prices: np.ndarray          # VWMP per bin
    alpha_params: AlphaParams          # base_alpha, spread, agreement, etc.
    n_mc: int = 5000
    n_bootstrap: int = 500

@dataclass(frozen=True)
class ProbabilityOutput:
    p_raw: np.ndarray
    p_cal: np.ndarray
    p_posterior: np.ndarray
    alpha: float
    edges: dict[int, EdgeResult]       # bin_idx → edge result
    selected_hypotheses: list[Hypothesis]  # post-FDR

def compute_probability_chain(inp: ProbabilityInput) -> ProbabilityOutput:
    """Pure function: inputs → outputs. No DB. No I/O."""
    p_raw = p_raw_vector_from_maxes(...)
    p_cal = calibrate_and_normalize(...)
    alpha = compute_alpha(...)
    p_posterior = compute_posterior(...)
    edges = scan_edges(...)
    selected = apply_fdr(...)
    return ProbabilityOutput(...)
```

#### 9.4.2 Extract Edge Detection from Evaluator

The 892-line `evaluate_candidate()` in `evaluator.py` mixes:
- Data fetching (ENS, GFS, market prices)
- Probability computation (the math)
- Risk checking (portfolio limits)
- DB recording (decision logging)
- Snapshot storage (ensemble snapshots)

Split into:
1. **Data assembly** (stays in evaluator): Fetch ENS, construct MarketCandidate
2. **Math** (moves to probability_pipeline): P_raw → P_cal → posterior → edges → FDR
3. **Risk** (stays in evaluator, delegates to risk_limits): Position limits check
4. **Recording** (stays in evaluator): Decision log, probability trace

#### 9.4.3 Property-Based Tests for Probability Chain

| Property | Test |
|----------|------|
| `p_raw` sums to 1.0 | `assert abs(sum(p_raw) - 1.0) < 1e-10` |
| `p_cal` sums to 1.0 after normalization | `assert abs(sum(p_cal) - 1.0) < 1e-10` |
| `p_posterior` sums to 1.0 after normalization | `assert abs(sum(p_posterior) - 1.0) < 1e-10` |
| `alpha ∈ [0.20, 0.85]` | `assert 0.20 <= alpha <= 0.85` |
| `p_raw` monotone in threshold for single-sided bins | Property test with Hypothesis |
| `p_cal` preserves ordering of `p_raw` | If `p_raw[i] > p_raw[j]` then `p_cal[i] > p_cal[j]` (approximate) |
| FDR controls false discovery rate | Simulation test with known-null hypotheses |
| `compute_probability_chain()` is deterministic with fixed RNG seed | `np.random.seed(42)` → same output |

#### 9.4.4 Fix P9: Monitor Hardcoded `model_agreement="AGREE"`

**Current (monitor_refresh.py L189, L379):**
```python
model_agreement="AGREE"  # HARDCODED
```

**Fix:** Wire actual GFS crosscheck into monitor path, or at minimum pass through the entry-time `model_agreement` value stored on Position.

**Approach:** Add `entry_model_agreement` field to Position (or retrieve from `trade_decisions` table). Monitor uses entry-time agreement as fallback if fresh GFS is unavailable.

**Files:** `src/engine/monitor_refresh.py`, `src/state/portfolio.py` (add field)

### 9.5 Files Touched

```
src/math/__init__.py                       (NEW)
src/math/probability_pipeline.py           (NEW — pure probability chain)
src/math/alpha_params.py                   (NEW — alpha parameter container)
src/engine/evaluator.py                    (extract math to pipeline)
src/engine/monitor_refresh.py              (P9 fix)
src/state/portfolio.py                     (entry_model_agreement field if needed)
tests/test_probability_pipeline.py         (NEW — pure math tests)
tests/test_probability_properties.py       (NEW — property-based tests)
tests/parity/test_parity_evaluator.py      (NEW — evaluator output parity)
tests/parity/test_parity_probability.py    (NEW — chain output parity)
```

### 9.6 Acceptance Criteria

1. All existing tests pass
2. `compute_probability_chain()` produces identical output to old evaluator for same inputs (parity test)
3. Property-based tests pass for p_raw, p_cal, p_posterior, alpha, FDR
4. `evaluator.py` delegates math to `probability_pipeline.py` (no inline math remains)
5. `probability_pipeline.py` has zero imports from `src/state/`, `src/data/`, `src/execution/`
6. Monitor uses actual model_agreement (not hardcoded "AGREE")
7. INV-01 through INV-08 parity tests pass

### 9.7 Rollback Plan

```bash
git checkout data-improve
# evaluator.py unchanged on data-improve — instant rollback
# New src/math/ module is additive — ignored by old code
```

### 9.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Floating-point divergence after extraction | MEDIUM | HIGH | Parity tests with rtol=1e-12; same RNG seed |
| P9 fix changes exit timing for held positions | HIGH | MEDIUM | Run parity test on historical positions; monitor behavior in first 24h |
| Property tests find real math bugs | MEDIUM | POSITIVE | Document and fix in this phase |

### 9.9 Estimated Scope

- Files modified: ~4
- Files created: ~6 (new modules + tests)
- LOC removed: ~400 (from evaluator, moved to pipeline)
- LOC added: ~1,200 (pipeline + property tests + parity tests)
- Net: +800 LOC

### 9.10 Dependencies

Phase 0 must complete. Can run in parallel with Phase 1.

---

## 10. Phase 3: Risk & Sizing

### 10.1 Objective

Formalize the risk FSM, extract position sizing as a pure function, and make Kelly sizing contracts explicit and testable.

### 10.2 Branch

`feature/phase-3-risk-sizing` from `data-improve` (after Phase 2 merged)

### 10.3 Preconditions

- Phase 0 and Phase 2 complete and merged
- Probability pipeline module exists and is tested
- Kelly/risk parity tests frozen

### 10.4 Work Items

#### 10.4.1 Formalize Risk FSM

**Current:** `overall_level(*levels)` takes `max()`. No explicit state machine — each RiskGuard tick independently computes level.

**Target:** Create explicit `RiskFSM` class:

```python
# src/riskguard/risk_fsm.py
class RiskFSM:
    """Explicit risk state machine with transition logging."""
    
    def __init__(self):
        self.current_level = RiskLevel.GREEN
        self.transition_history: list[RiskTransition] = []
    
    def evaluate(self, metrics: RiskMetrics) -> RiskLevel:
        new_level = self._compute_level(metrics)
        if new_level != self.current_level:
            self._record_transition(self.current_level, new_level, metrics)
            self.current_level = new_level
        return self.current_level
```

This preserves the existing stateless semantics (no hysteresis) while adding transition logging for observability.

#### 10.4.2 Extract Position Sizer as Pure Function

**Current:** Kelly sizing logic is split across `kelly.py`, `risk_limits.py`, `evaluator.py`, and `cycle_runtime.py`.

**Target:** Single `compute_position_size()` function:

```python
# src/strategy/position_sizer.py
@dataclass(frozen=True)
class SizingInput:
    p_posterior: float
    entry_price: float
    bankroll: float
    ci_width: float
    lead_days: float
    rolling_win_rate: float | None
    portfolio_heat: float
    drawdown_pct: float
    safety_cap_usd: float
    strategy_policy: StrategyPolicy

@dataclass(frozen=True)
class SizingOutput:
    raw_kelly: float
    dynamic_mult: float
    clipped_size_usd: float
    rejection_reason: str | None  # None = allowed

def compute_position_size(inp: SizingInput) -> SizingOutput:
    """Pure sizing function — no DB, no portfolio state."""
```

#### 10.4.3 Kelly Contract Hardening

Add explicit assertions to `kelly_size()` and `dynamic_kelly_mult()`:

| Contract | Current | Target |
|----------|---------|--------|
| `p_posterior ∈ (0, 1)` | Not checked | `assert 0 < p_posterior < 1` |
| `entry_price ∈ (0, 1)` | Not checked | `assert 0 < entry_price < 1` |
| `bankroll > 0` | Checked in risk_limits | Also checked in kelly_size |
| `kelly_mult > 0` | Checked via ValueError | Preserved |
| `safety_cap_usd > 0` | Implicit | Explicit assert |

### 10.5 Files Touched

```
src/riskguard/risk_fsm.py                 (NEW — explicit FSM)
src/riskguard/riskguard.py                (use risk_fsm)
src/riskguard/metrics.py                  (type annotations)
src/strategy/position_sizer.py            (NEW — pure sizing)
src/strategy/kelly.py                     (contract hardening)
src/strategy/risk_limits.py               (delegate to position_sizer)
tests/test_risk_fsm.py                    (NEW)
tests/test_position_sizer.py              (NEW)
tests/parity/test_parity_kelly.py         (NEW)
tests/parity/test_parity_risk.py          (NEW)
```

### 10.6 Acceptance Criteria

1. All existing tests pass
2. `RiskFSM.evaluate()` produces same levels as old `overall_level()` for same metrics
3. `compute_position_size()` produces same output as old Kelly+risk_limits pipeline
4. `dynamic_kelly_mult()` raises `ValueError` on 0/NaN (INV-06 preserved)
5. Kelly contract assertions do not trigger on any historical trade data
6. Parity tests for Kelly sizing pass

### 10.7 Rollback Plan

```bash
git checkout data-improve
# risk_fsm and position_sizer are additive — old code ignores them
```

### 10.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| New assertions trigger on edge cases | LOW | MEDIUM | Test against all historical trade inputs first |
| Risk FSM changes level computation | LOW | HIGH | Parity test: same metrics → same level |
| Sizing changes affect live trade sizes | LOW | HIGH | Frozen parity tests on historical inputs |

### 10.9 Estimated Scope

- Files modified: ~5
- Files created: ~5 (new modules + tests)
- LOC added: ~600 (FSM + sizer + tests)
- Net: +500 LOC

### 10.10 Dependencies

Phase 2 (math extraction) must complete. Phase 0 must complete.

---

## 11. Phase 4: Execution Layer

### 11.1 Objective

Formalize order lifecycle as a state machine, fix fill tracker reliability, abstract the Polymarket client for testability, and resolve phantom position persistence (P14).

### 11.2 Branch

`feature/phase-4-execution` from `data-improve` (after Phase 1 merged)

### 11.3 Preconditions

- Phase 0 and Phase 1 (data layer) complete
- DB decomposition in place
- State backups taken

### 11.4 Work Items

#### 11.4.1 Order Lifecycle State Machine

**Current:** Exit state encoded as strings (`""`, `"exit_intent"`, `"sell_placed"`, etc.) with implicit transitions.

**Target:** Explicit `OrderState` enum + `OrderFSM`:

```python
# src/execution/order_fsm.py
class OrderState(Enum):
    NONE = ""
    EXIT_INTENT = "exit_intent"
    SELL_PLACED = "sell_placed"
    SELL_PENDING = "sell_pending"
    SELL_FILLED = "sell_filled"
    RETRY_PENDING = "retry_pending"
    BACKOFF_EXHAUSTED = "backoff_exhausted"

LEGAL_TRANSITIONS = {
    OrderState.NONE: {OrderState.EXIT_INTENT},
    OrderState.EXIT_INTENT: {OrderState.SELL_PLACED, OrderState.RETRY_PENDING},
    OrderState.SELL_PLACED: {OrderState.SELL_PENDING},
    OrderState.SELL_PENDING: {OrderState.SELL_FILLED, OrderState.RETRY_PENDING},
    OrderState.RETRY_PENDING: {OrderState.NONE},
    # Terminal states:
    OrderState.SELL_FILLED: set(),
    OrderState.BACKOFF_EXHAUSTED: set(),
}
```

#### 11.4.2 Fix Phantom Position Persistence (P14/C3)

**Current:** `skip_voiding = active_local > 0 and len(chain_positions) == 0`

**Fix:** Add a staleness counter — if chain returns 0 for N consecutive cycles (e.g., 3), accept the empty response as legitimate and void the positions:

```python
# Track consecutive empty responses
if len(chain_positions) == 0 and active_local > 0:
    portfolio._chain_empty_count = getattr(portfolio, '_chain_empty_count', 0) + 1
    if portfolio._chain_empty_count >= CHAIN_EMPTY_THRESHOLD:  # e.g., 3
        # Legitimate empty — proceed with voiding
        skip_voiding = False
    else:
        skip_voiding = True  # Still suspicious
else:
    portfolio._chain_empty_count = 0
```

**Files:** `src/state/chain_reconciliation.py`

#### 11.4.3 Fix PolymarketClient Per-Order Construction (P10)

**Current:** `executor.py` creates `PolymarketClient()` per order → Keychain subprocess per order.

**Fix:** Accept client as parameter (already partially done — `clob` parameter exists in cycle_runner). Ensure executor uses the passed-in client, not a fresh one.

**Files:** `src/execution/executor.py`

#### 11.4.4 Fill Tracker Reliability

**Current:** DB write failures return `False` silently (corrected from P8 — they log, but don't retry).

**Fix:** Add retry with exponential backoff for DB writes in fill tracker. If retry exhausts, position enters explicit `quarantine_fill_failed` state (already partially implemented).

**Files:** `src/execution/fill_tracker.py`

#### 11.4.5 Polymarket Client Abstraction

Create a protocol/interface for the CLOB client:

```python
# src/data/clob_protocol.py
from typing import Protocol

class CLOBClient(Protocol):
    def place_limit_order(self, ...) -> dict: ...
    def get_order_status(self, order_id: str) -> dict: ...
    def get_balance(self) -> float: ...
    def cancel_order(self, order_id: str) -> dict: ...
```

This allows test doubles without monkeypatching.

### 11.5 Files Touched

```
src/execution/order_fsm.py                (NEW — order state machine)
src/execution/exit_lifecycle.py            (use order_fsm)
src/execution/executor.py                 (P10 fix, use passed client)
src/execution/fill_tracker.py             (retry logic)
src/state/chain_reconciliation.py         (P14 fix)
src/data/clob_protocol.py                 (NEW — CLOB protocol)
src/data/polymarket_client.py             (implement protocol)
tests/test_order_fsm.py                   (NEW)
tests/test_chain_reconciliation_phantom.py (NEW — P14 regression test)
tests/parity/test_parity_reconciliation.py (NEW)
```

### 11.6 Acceptance Criteria

1. All existing tests pass
2. OrderFSM rejects illegal state transitions
3. P14: After 3 consecutive empty chain responses, phantom positions are voided
4. P10: Executor uses passed-in CLOB client (no new construction)
5. Fill tracker retries DB writes before giving up
6. `test_lifecycle.py` passes (BEH-04 preserved)
7. `test_executor.py` passes (BEH-09 preserved)
8. CLOB protocol is implemented by PolymarketClient

### 11.7 Rollback Plan

```bash
git checkout data-improve
# P14 behavioral change — need to verify no phantom positions are created during rollback window
```

### 11.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| P14 fix voids real positions on transient API failure | MEDIUM | HIGH | 3-cycle threshold provides ~45 minutes of tolerance |
| Order FSM rejects a currently-valid transition | LOW | MEDIUM | Enumerate all transitions from production logs before defining LEGAL_TRANSITIONS |
| Fill tracker retry causes duplicate operations | LOW | MEDIUM | Idempotent DB operations (INSERT OR IGNORE, UPSERT) |

### 11.9 Estimated Scope

- Files modified: ~6
- Files created: ~5 (new modules + tests)
- LOC added: ~700 (FSM + protocol + tests)
- Net: +500 LOC

### 11.10 Dependencies

Phase 0 and Phase 1 must complete. Phase 2 is not required.

---

## 12. Phase 5: Strategy Layer

### 12.1 Objective

Define strategies as a formal protocol/interface, formalize the signal pipeline, and create a strategy registry that replaces the current `KNOWN_STRATEGIES` set and `_classify_strategy()` dispatch.

### 12.2 Branch

`feature/phase-5-strategy` from `data-improve` (after Phases 2, 3, 4 merged)

### 12.3 Preconditions

- Phases 0, 2, 3, 4 complete
- Probability pipeline, position sizer, and order FSM in place
- All parity tests pass

### 12.4 Work Items

#### 12.4.1 Strategy Protocol

```python
# src/strategy/strategy_protocol.py
from typing import Protocol

class Strategy(Protocol):
    """What every strategy must implement."""
    name: str
    discovery_mode: DiscoveryMode
    
    def matches(self, candidate: MarketCandidate) -> bool:
        """Does this strategy apply to this candidate?"""
        ...
    
    def compute_signal(self, candidate: MarketCandidate) -> SignalResult:
        """Generate probability signal for this candidate."""
        ...
    
    def edge_filter(self, edges: list[EdgeResult]) -> list[EdgeResult]:
        """Apply strategy-specific edge filters (e.g., center_buy price gate)."""
        ...
    
    def sizing_params(self) -> SizingParams:
        """Return strategy-specific Kelly adjustments."""
        ...
```

#### 12.4.2 Strategy Implementations

| Strategy Class | Current Location | Refactored Location |
|----------------|-----------------|---------------------|
| `SettlementCaptureStrategy` | Inline in cycle_runner + day0_signal | `src/strategy/strategies/settlement_capture.py` |
| `OpeningInertiaStrategy` | Inline in cycle_runner + ensemble_signal | `src/strategy/strategies/opening_inertia.py` |
| `ShoulderSellStrategy` | Inline in cycle_runner + market_fusion | `src/strategy/strategies/shoulder_sell.py` |
| `CenterBuyStrategy` | Inline in cycle_runner + evaluator | `src/strategy/strategies/center_buy.py` |

#### 12.4.3 Strategy Registry

```python
# src/strategy/registry.py
STRATEGY_REGISTRY: dict[str, Strategy] = {
    "settlement_capture": SettlementCaptureStrategy(),
    "opening_inertia": OpeningInertiaStrategy(),
    "shoulder_sell": ShoulderSellStrategy(),
    "center_buy": CenterBuyStrategy(),
}

def get_strategy(name: str) -> Strategy:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {name}")
    return STRATEGY_REGISTRY[name]
```

#### 12.4.4 Replace `deps=sys.modules[__name__]` (P3)

**Current:** `cycle_runner.py` passes `deps=sys.modules[__name__]` to `cycle_runtime.py`.

**Target:** Explicit `CycleDeps` container:

```python
@dataclass
class CycleDeps:
    evaluate_candidate: Callable
    execute_intent: Callable
    get_connection: Callable
    # ... all dependencies explicitly typed
```

This makes dependencies visible, type-safe, and testable.

#### 12.4.5 Signal Pipeline Formalization

**Current:** Signal generation is split across `ensemble_signal.py`, `day0_signal.py`, `model_agreement.py`, `diurnal.py`, `forecast_uncertainty.py`.

**Target:** Each signal component produces a typed `SignalContribution` that feeds into the probability pipeline:

```python
@dataclass(frozen=True)
class SignalContribution:
    source: str              # "ensemble", "day0_observation", "gfs_crosscheck"
    p_vector: np.ndarray     # probability vector contribution
    confidence: float        # signal confidence
    metadata: dict           # source-specific metadata
```

### 12.5 Files Touched

```
src/strategy/strategy_protocol.py                 (NEW)
src/strategy/registry.py                           (NEW)
src/strategy/strategies/__init__.py                (NEW)
src/strategy/strategies/settlement_capture.py      (NEW)
src/strategy/strategies/opening_inertia.py         (NEW)
src/strategy/strategies/shoulder_sell.py            (NEW)
src/strategy/strategies/center_buy.py              (NEW)
src/engine/cycle_runner.py                         (P3 fix, strategy dispatch)
src/engine/cycle_runtime.py                        (P3 fix, typed deps)
src/engine/cycle_deps.py                           (NEW — typed DI container)
tests/test_strategy_protocol.py                    (NEW)
tests/test_strategy_registry.py                    (NEW)
tests/parity/test_parity_strategy_dispatch.py      (NEW)
```

### 12.6 Acceptance Criteria

1. All existing tests pass
2. `KNOWN_STRATEGIES` set replaced by `STRATEGY_REGISTRY`
3. `_classify_strategy()` replaced by `strategy.matches(candidate)` dispatch
4. `deps=sys.modules[__name__]` eliminated — all DI through typed `CycleDeps`
5. Each strategy implements the `Strategy` protocol
6. Parity: same candidates → same strategy classification
7. `test_auto_pause_entries.py` passes (BEH-01 preserved)

### 12.7 Rollback Plan

```bash
git checkout data-improve
# Strategy protocol and registry are additive
# cycle_runner changes are the only behavioral change — must be tested thoroughly
```

### 12.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Strategy dispatch change misclassifies an edge case | MEDIUM | HIGH | Parity test against all known market candidates from production logs |
| DI container misses a dependency | MEDIUM | MEDIUM | Type checker + runtime fail-fast on missing attribute |
| Strategy protocol too rigid for future strategies | LOW | LOW | Protocol is minimal — easy to extend |

### 12.9 Estimated Scope

- Files modified: ~4
- Files created: ~10 (strategies + protocol + registry + tests)
- LOC added: ~1,500 (strategy implementations + typed DI)
- Net: +1,200 LOC

### 12.10 Dependencies

Phases 2, 3, 4 must complete. Phase 1 is recommended but not strictly required.

---

## 13. Phase 6: Scheduler & Orchestration

### 13.1 Objective

Formalize the job graph, add cycle watchdog, and improve error recovery — making the daemon's operational behavior explicit and monitorable.

### 13.2 Branch

`feature/phase-6-scheduler` from `data-improve` (after Phase 5 merged)

### 13.3 Preconditions

- Phase 5 (strategy layer) complete
- All strategy dispatch tests pass
- CycleDeps typed container in place

### 13.4 Work Items

#### 13.4.1 Explicit Job Graph

**Current:** Jobs defined inline in `main.py` with hardcoded schedules.

**Target:** `JobGraph` configuration:

```python
# src/engine/job_graph.py
@dataclass
class JobSpec:
    name: str
    func: Callable
    schedule: IntervalTrigger | CronTrigger
    lock: threading.Lock | None
    max_instances: int
    timeout_seconds: int | None
    on_failure: str  # "log", "alert", "halt"

JOB_GRAPH: list[JobSpec] = [
    JobSpec("opening_hunt", _run_mode(OPENING_HUNT), interval(minutes=30), _cycle_lock, 1, 1800, "log"),
    JobSpec("update_reaction", _run_mode(UPDATE_REACTION), cron("07,09,19,21"), _cycle_lock, 1, 1800, "log"),
    JobSpec("day0_capture", _run_mode(DAY0_CAPTURE), interval(minutes=15), _cycle_lock, 1, 900, "log"),
    JobSpec("harvester", _harvester_cycle, interval(hours=1), None, 1, 3600, "alert"),
    JobSpec("wu_daily", _wu_daily_collection, cron("12:00"), None, 1, 600, "alert"),
    # ...
]
```

#### 13.4.2 Cycle Watchdog (P12)

**Current:** `_cycle_lock.acquire(blocking=False)` — if a cycle hangs, all subsequent cycles skip silently forever.

**Fix:** Add a watchdog thread that monitors lock hold duration:

```python
# src/engine/watchdog.py
class CycleWatchdog:
    def __init__(self, max_duration_seconds: int = 1800):
        self.max_duration = max_duration_seconds
        self._lock_acquired_at: float | None = None
    
    def on_lock_acquired(self):
        self._lock_acquired_at = time.monotonic()
    
    def on_lock_released(self):
        self._lock_acquired_at = None
    
    def check(self) -> bool:
        """Returns True if cycle is overdue."""
        if self._lock_acquired_at is None:
            return False
        elapsed = time.monotonic() - self._lock_acquired_at
        return elapsed > self.max_duration
```

When the watchdog fires:
1. Log `CRITICAL` message
2. Send Discord alert
3. Force-release the lock (with position-safety check)

#### 13.4.3 Cycle State Enum (P6)

**Current:** 8 if/elif checks as strings in `cycle_runner.py`.

**Target:** Explicit `CyclePhase` enum:

```python
class CyclePhase(Enum):
    CHAIN_SYNC = "chain_sync"
    PENDING_RECONCILIATION = "pending_reconciliation"
    MONITORING = "monitoring"
    DISCOVERY_BLOCKED = "discovery_blocked"
    DISCOVERY = "discovery"
    SAVE = "save"
    COMPLETE = "complete"
    ERROR = "error"
```

#### 13.4.4 Improve ETL Error Recovery

**Current:** Each ETL step logs "OK" or "FAIL" independently. No retry. No alert.

**Fix:**
1. Add retry (1 attempt) for failed ETL steps
2. Send Discord alert on ETL failure (calibration staleness is high-risk)
3. Log ETL step duration for performance monitoring

### 13.5 Files Touched

```
src/engine/job_graph.py                   (NEW — job specification)
src/engine/watchdog.py                    (NEW — cycle watchdog)
src/engine/cycle_phase.py                 (NEW — cycle state enum)
src/engine/cycle_runner.py                (use CyclePhase, P6 fix)
src/main.py                              (use job_graph, add watchdog)
tests/test_watchdog.py                    (NEW)
tests/test_cycle_phase.py                 (NEW)
tests/test_job_graph.py                   (NEW)
```

### 13.6 Acceptance Criteria

1. All existing tests pass
2. Job graph produces identical schedule to current inline definitions
3. Watchdog fires after 30 minutes (configurable)
4. Cycle phases are explicit enum values (no more string comparisons for blocking reasons)
5. ETL failures produce Discord alerts
6. `_cycle_lock` behavior unchanged for normal operations
7. `test_runtime_guards.py` passes

### 13.7 Rollback Plan

```bash
git checkout data-improve
# Job graph and watchdog are additive
# CyclePhase enum may require reverting cycle_runner changes
```

### 13.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Watchdog force-releases lock during legitimate long cycle | LOW | HIGH | 30-min threshold is 2x longest observed cycle; alert before force-release |
| Job graph schedule drift | LOW | MEDIUM | Parity test: same job at same cron/interval |
| CyclePhase enum doesn't cover an edge case | LOW | LOW | `ERROR` state as catch-all |

### 13.9 Estimated Scope

- Files modified: ~3
- Files created: ~5 (new modules + tests)
- LOC added: ~600 (job graph + watchdog + tests)
- Net: +500 LOC

### 13.10 Dependencies

Phase 5 must complete. All earlier phases must be merged.

---

## 14. Phase 7: Observability & Operations

### 14.1 Objective

Add structured logging, metrics collection, and alerting contracts — making the system's operational state transparent without changing any business logic.

### 14.2 Branch

`feature/phase-7-observability` from `data-improve` (can start after Phase 1)

### 14.3 Preconditions

- Phase 0 complete (logging standardization done)
- Phase 1 recommended (DB decomposition makes observability easier)

### 14.4 Work Items

#### 14.4.1 Structured Logging

**Current:** `logging.INFO` with string formatting: `"%(asctime)s [%(name)s] %(levelname)s: %(message)s"`

**Target:** JSON-structured log lines for machine parsing:

```python
# src/observability/structured_logging.py
import json, logging

class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
        }
        if hasattr(record, 'extra'):
            log_entry.update(record.extra)
        return json.dumps(log_entry)
```

**Backward compatibility:** Add structured logging as an additional handler, not replacement. Existing human-readable logs continue to work.

#### 14.4.2 Metrics Collection

Key operational metrics to track (written to `state/metrics.json` periodically):

| Metric | Source | Frequency |
|--------|--------|-----------|
| Cycle duration (per mode) | `cycle_runner.py` | Per cycle |
| Orders placed/filled/voided | `executor.py`, `fill_tracker.py` | Per cycle |
| Active position count | `portfolio.py` | Per cycle |
| Portfolio heat | `portfolio.py` | Per cycle |
| Bankroll | `portfolio.py` | Per cycle |
| ENS fetch latency | `ensemble_client.py` | Per fetch |
| CLOB latency | `polymarket_client.py` | Per order |
| Risk level | `riskguard.py` | Per tick |
| Calibration pair count | `calibration/store.py` | Daily |
| ETL step duration | `main.py` | Per ETL run |

#### 14.4.3 Alerting Contracts

Formalize the alerting contract:

```python
# src/observability/alert_contract.py
@dataclass(frozen=True)
class AlertSpec:
    name: str
    severity: str           # "info", "warning", "critical"
    cooldown_minutes: int
    message_template: str
    fields: list[str]       # required context fields

ALERT_CATALOG: list[AlertSpec] = [
    AlertSpec("risk_escalation", "critical", 30, "Risk escalated: {old} -> {new}", ["old", "new"]),
    AlertSpec("trade_placed", "info", 0, "Order: {direction} {city} {threshold}", ["direction", "city"]),
    AlertSpec("etl_failure", "warning", 60, "ETL step failed: {step}", ["step"]),
    AlertSpec("watchdog_fire", "critical", 30, "Cycle watchdog fired after {seconds}s", ["seconds"]),
    AlertSpec("wallet_low", "warning", 360, "Wallet balance: ${balance}", ["balance"]),
]
```

#### 14.4.4 Authority Bypass Fix (C2)

**Current (monitor_refresh.py L174-186):**
```python
try:
    _unverified_pairs = _get_pairs(conn, city.cluster, _cal_season, authority_filter='UNVERIFIED')
except Exception:
    _unverified_pairs = []  # silent authority bypass
```

**Fix:** If `_get_pairs` throws, flag the position's authority as degraded rather than silently proceeding:

```python
try:
    _unverified_pairs = _get_pairs(conn, ...)
except Exception as e:
    logger.error("Authority check failed for %s: %s", city.name, e)
    _authority_degraded = True  # Flag for downstream
```

### 14.5 Files Touched

```
src/observability/structured_logging.py   (NEW)
src/observability/metrics.py              (NEW)
src/observability/alert_contract.py       (NEW)
src/observability/status_summary.py       (integrate metrics)
src/engine/monitor_refresh.py             (C2 fix)
src/main.py                              (add structured logging handler)
src/riskguard/discord_alerts.py           (use alert catalog)
tests/test_structured_logging.py          (NEW)
tests/test_metrics.py                     (NEW)
tests/test_alert_contract.py             (NEW)
```

### 14.6 Acceptance Criteria

1. All existing tests pass
2. Structured logging produces valid JSON on every handler
3. Metrics are written to `state/metrics.json` every cycle
4. Alert catalog covers all existing Discord alert types
5. C2 authority bypass is logged and flagged (not silently skipped)
6. Human-readable log format still works (dual handler)

### 14.7 Rollback Plan

```bash
git checkout data-improve
# All observability changes are additive — no behavior change
```

### 14.8 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| JSON logging breaks log parsing scripts | LOW | LOW | Dual handler preserves old format |
| Metrics collection adds latency | LOW | LOW | JSON file write is < 1ms |
| C2 fix changes monitoring behavior | MEDIUM | MEDIUM | Only adds logging/flagging, doesn't change probability computation |

### 14.9 Estimated Scope

- Files modified: ~5
- Files created: ~5 (new modules + tests)
- LOC added: ~800 (metrics + logging + alert catalog + tests)
- Net: +700 LOC

### 14.10 Dependencies

Phase 0 must complete. Can run in parallel with Phases 2-6.

---

## 15. Phase Summary Matrix

| Phase | Name | Branch | Files Modified | Files Created | LOC Impact | Dependencies | CRITICAL Pathologies Fixed |
|-------|------|--------|---------------|---------------|------------|--------------|---------------------------|
| 0 | Foundation | `phase-0-foundation` | ~15 | ~6 | +610 | None | P15 |
| 1 | Data Layer | `phase-1-data-layer` | ~8 | ~10 | +1,000 | Phase 0 | P1/C6, P16, TB-7, Security |
| 2 | Core Math | `phase-2-math-extraction` | ~4 | ~6 | +800 | Phase 0 | P9 |
| 3 | Risk & Sizing | `phase-3-risk-sizing` | ~5 | ~5 | +500 | Phase 2 | — |
| 4 | Execution | `phase-4-execution` | ~6 | ~5 | +500 | Phase 1 | P14/C3, P10 |
| 5 | Strategy | `phase-5-strategy` | ~4 | ~10 | +1,200 | Phases 2,3,4 | P3 |
| 6 | Scheduler | `phase-6-scheduler` | ~3 | ~5 | +500 | Phase 5 | P6, P12 |
| 7 | Observability | `phase-7-observability` | ~5 | ~5 | +700 | Phase 0 | C2 |
| **Total** | | | **~50** | **~52** | **+5,810** | | **10 of 16 pathologies** |

---

## 16. Pathology Resolution Map

Cross-reference from pathology registry to refactor phase:

| ID | Severity | Description | Fixed In | Fix Description |
|----|----------|-------------|----------|-----------------|
| P1/C6 | HIGH | Portfolio save before DB commit | Phase 1 | Reorder to commit-then-save |
| P2 | MEDIUM | Position god object (79 fields) | Phase 4-5 | Decompose incrementally (not a single phase) |
| P3 | MEDIUM | `deps=sys.modules[__name__]` opaque DI | Phase 5 | Typed `CycleDeps` container |
| P4 | HIGH | Evaluator monolith (892 lines) | Phase 2 | Extract math to probability_pipeline |
| P5 | MEDIUM | Scripts shadow API (116 scripts) | Deferred | Separate initiative — scripts are K4 |
| P6 | MEDIUM | Implicit state machine in cycle_runner | Phase 6 | CyclePhase enum |
| P8 | LOW-MEDIUM | fill_tracker exception handling | Phase 0 | Log + flag instead of silent return |
| P9 | HIGH | Monitor hardcodes model_agreement="AGREE" | Phase 2 | Wire real agreement into monitor |
| P10 | MEDIUM | New PolymarketClient per order | Phase 4 | Use passed-in client |
| P12 | MEDIUM | Cycle lock with no watchdog | Phase 6 | CycleWatchdog with 30-min timeout |
| P13 | LOW | Ensemble cache unbounded | — | Overstated; not addressed |
| P14/C3 | CRITICAL | Phantom position persistence | Phase 4 | Staleness counter (3 consecutive empties) |
| P15 | LOW | Duplicate append in ExitContext | Phase 0 | Remove duplicate line |
| P16 | MEDIUM | Harvester uses round() not WMO | Phase 1 | Import round_wmo_half_up_value() |
| C1 | HIGH | Harvester stale JSON fallback | Phase 1 | Fixed by P1 fix (JSON written after commit) |
| C2 | HIGH | Monitor authority bypass on exception | Phase 7 | Log + flag degraded authority |
| C4 | MEDIUM | Exit lifecycle no fallback after exhaustion | Phase 4 | Document as design decision (hold to settlement) |
| TB-1 | LOW | DST in hours_since_open | — | Low risk, not addressed |
| TB-2 | MEDIUM | Season flip mid-position | — | Document as known limitation |
| TB-3 | HIGH | Gamma API pagination O(n) | Phase 4 | Add `since` date filter to harvester |
| TB-5 | MEDIUM | smoke_test_portfolio_cap never removed | Phase 3 | Add removal condition |
| TB-6 | MEDIUM | Harvester hourly vs real-time | Phase 6 | Document as design tradeoff |
| TB-7 | MEDIUM | Harvester no max_instances | Phase 1 | Add max_instances=1 |

---

## 17. Deferred Work (Not In This Plan)

These items are important but are out of scope for this refactor plan:

| Item | Reason for Deferral |
|------|-------------------|
| P5: Scripts shadow API (116 files, 25K LOC) | K4 zone — disposable by definition. Address separately. |
| Position god object full decomposition (P2) | Requires touching 40+ call sites. Do incrementally across Phases 4-5, complete in a future plan. |
| Replay engine refactor (2,068 LOC) | Replay is a diagnostic tool, not live path. Low priority. |
| Full test coverage for all 116 src files | Aspirational. Phase 0 covers K0; expand organically. |
| SQLite to PostgreSQL migration | Not needed at current scale ($150 bankroll). Revisit if capital > $10K. |
| Blue-green deployment | Not possible with single-process launchd daemon. Revisit with containerization. |
| Automated DB backups | Should exist but is operational, not refactor work. |
| TB-3: Gamma API pagination | Partially addressed in Phase 4 harvester work. Full fix needs Gamma API date filter. |

---

## 18. Success Metrics

### 18.1 Technical Debt Reduction

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| `db.py` LOC | 4,472 | ~100 (facade) | < 200 |
| `evaluator.py` longest function | 892 lines | < 100 lines | < 150 |
| `except Exception: pass` count | 7 | 0 | 0 |
| K0 test coverage (ledger/projection/lifecycle) | 0 files | 4 files | 4 files |
| CRITICAL pathologies open | 2 (P1, P14) | 0 | 0 |
| HIGH pathologies open | 5 (P4, P9, C1, C2, TB-3) | 0 | 0 |
| Type-annotated K0/K1 interfaces | ~30% | 100% | 100% |

### 18.2 Operational Quality

| Metric | Before | After |
|--------|--------|-------|
| Structured logging | No | Yes (JSON handler) |
| Cycle watchdog | No | Yes (30-min timeout) |
| ETL failure alerting | No | Yes (Discord) |
| Metrics export | No | Yes (JSON file) |
| Order lifecycle FSM | Implicit strings | Explicit enum + transitions |
| Risk FSM | Stateless max() | Explicit FSM with transition logging |

---

## 19. Execution Protocol

### 19.1 Per-Phase Checklist

Before starting a phase:
- [ ] Prior phase merged to `data-improve`
- [ ] All tests pass on `data-improve`
- [ ] State backups taken (section 6.4)
- [ ] Feature branch created
- [ ] Parity test fixtures captured for affected functions

During a phase:
- [ ] Run `python -m pytest tests/ -x` after every file change
- [ ] Commit working increments (don't batch large changes)
- [ ] Update this plan with actual findings

After a phase:
- [ ] All tests pass (existing + new)
- [ ] All parity tests pass
- [ ] No new `except Exception: pass` patterns
- [ ] `test_architecture_contracts.py` passes
- [ ] `test_cross_module_invariants.py` passes
- [ ] PR created, reviewed, merged to `data-improve`
- [ ] Daemon restarted and verified operational

### 19.2 Decision Log

Each phase should maintain a brief decision log in the PR description:

```
## Phase N Decision Log
- DECISION: [what was decided]
- REASON: [why]
- ALTERNATIVE REJECTED: [what else was considered]
- EVIDENCE: [test results, parity verification]
```

### 19.3 Communication Protocol

Before starting each phase:
1. Notify operator (Fitz) with: phase number, estimated file count, pathologies addressed
2. Get explicit approval to proceed
3. After completion: summary of changes, test results, any unexpected findings

---

## Appendix A: File Index by Phase

### Phase 0 (Foundation)
```
MODIFY: src/state/ledger.py
MODIFY: src/state/projection.py
MODIFY: src/state/lifecycle_manager.py
MODIFY: src/engine/lifecycle_events.py
MODIFY: src/contracts/settlement_semantics.py
MODIFY: src/riskguard/policy.py
MODIFY: src/riskguard/risk_level.py
MODIFY: src/state/portfolio.py
MODIFY: src/execution/fill_tracker.py
MODIFY: src/state/db.py
DELETE: src/data/ecmwf_open_data.py (if dead)
MODIFY: src/execution/harvester.py
MODIFY: src/main.py
CREATE: tests/test_ledger.py
CREATE: tests/test_projection.py
CREATE: tests/test_lifecycle_manager.py
CREATE: tests/test_lifecycle_events.py
CREATE: tests/test_fill_tracker.py
CREATE: tests/test_polymarket_client.py
```

### Phase 1 (Data Layer)
```
MODIFY: src/state/db.py (facade)
CREATE: src/state/db_connections.py
CREATE: src/state/db_schema.py
CREATE: src/state/db_world.py
CREATE: src/state/db_trades.py
CREATE: src/state/db_derived.py
CREATE: src/state/db_risk.py
MODIFY: src/execution/harvester.py
MODIFY: src/data/wu_daily_collector.py
CREATE: src/contracts/observation_record.py
CREATE: src/contracts/ensemble_record.py
CREATE: src/contracts/calibration_pair_record.py
MODIFY: src/main.py
MODIFY: config/cities.json
MODIFY: src/config.py
CREATE: tests/test_db_decomposition.py
CREATE: tests/parity/test_parity_db_queries.py
```

### Phase 2 (Core Math)
```
CREATE: src/math/__init__.py
CREATE: src/math/probability_pipeline.py
CREATE: src/math/alpha_params.py
MODIFY: src/engine/evaluator.py
MODIFY: src/engine/monitor_refresh.py
MODIFY: src/state/portfolio.py
CREATE: tests/test_probability_pipeline.py
CREATE: tests/test_probability_properties.py
CREATE: tests/parity/test_parity_evaluator.py
CREATE: tests/parity/test_parity_probability.py
```

### Phase 3 (Risk & Sizing)
```
CREATE: src/riskguard/risk_fsm.py
MODIFY: src/riskguard/riskguard.py
MODIFY: src/riskguard/metrics.py
CREATE: src/strategy/position_sizer.py
MODIFY: src/strategy/kelly.py
MODIFY: src/strategy/risk_limits.py
CREATE: tests/test_risk_fsm.py
CREATE: tests/test_position_sizer.py
CREATE: tests/parity/test_parity_kelly.py
CREATE: tests/parity/test_parity_risk.py
```

### Phase 4 (Execution)
```
CREATE: src/execution/order_fsm.py
MODIFY: src/execution/exit_lifecycle.py
MODIFY: src/execution/executor.py
MODIFY: src/execution/fill_tracker.py
MODIFY: src/state/chain_reconciliation.py
CREATE: src/data/clob_protocol.py
MODIFY: src/data/polymarket_client.py
CREATE: tests/test_order_fsm.py
CREATE: tests/test_chain_reconciliation_phantom.py
CREATE: tests/parity/test_parity_reconciliation.py
```

### Phase 5 (Strategy)
```
CREATE: src/strategy/strategy_protocol.py
CREATE: src/strategy/registry.py
CREATE: src/strategy/strategies/__init__.py
CREATE: src/strategy/strategies/settlement_capture.py
CREATE: src/strategy/strategies/opening_inertia.py
CREATE: src/strategy/strategies/shoulder_sell.py
CREATE: src/strategy/strategies/center_buy.py
MODIFY: src/engine/cycle_runner.py
MODIFY: src/engine/cycle_runtime.py
CREATE: src/engine/cycle_deps.py
CREATE: tests/test_strategy_protocol.py
CREATE: tests/test_strategy_registry.py
CREATE: tests/parity/test_parity_strategy_dispatch.py
```

### Phase 6 (Scheduler)
```
CREATE: src/engine/job_graph.py
CREATE: src/engine/watchdog.py
CREATE: src/engine/cycle_phase.py
MODIFY: src/engine/cycle_runner.py
MODIFY: src/main.py
CREATE: tests/test_watchdog.py
CREATE: tests/test_cycle_phase.py
CREATE: tests/test_job_graph.py
```

### Phase 7 (Observability)
```
CREATE: src/observability/structured_logging.py
CREATE: src/observability/metrics.py
CREATE: src/observability/alert_contract.py
MODIFY: src/observability/status_summary.py
MODIFY: src/engine/monitor_refresh.py
MODIFY: src/main.py
MODIFY: src/riskguard/discord_alerts.py
CREATE: tests/test_structured_logging.py
CREATE: tests/test_metrics.py
CREATE: tests/test_alert_contract.py
```

---

## Appendix B: Glossary

| Term | Definition |
|------|-----------|
| **K-zone** | Governance zone (K0=frozen kernel, K1=governance, K2=runtime, K3=extension, K4=experimental) |
| **Parity test** | Test asserting old and new code produce identical outputs for same inputs |
| **Kill switch** | Procedure to halt refactor and revert to pre-refactor state |
| **God file** | Single file with too many responsibilities (e.g., db.py with 4,472 LOC) |
| **God object** | Single class with too many fields/responsibilities (e.g., Position with 79 fields) |
| **Dual-write** | Writing the same fact to two different storage locations (legacy + canonical) |
| **Shadow module** | Module marked SHADOW_ONLY — runs but never enters live decision path |
| **Phantom position** | Position that exists in portfolio but not on-chain (P14) |
| **Authority** | Data provenance status: VERIFIED (checked) or UNVERIFIED (unchecked) |
| **INV-XX** | Mathematical invariant that must never change |
| **BEH-XX** | Behavioral invariant (same conditions → same decisions) |
| **STR-XX** | Structural invariant (append-only tables, tz-aware timestamps, etc.) |

---

*End of refactor plan. This document is the single source of truth for Zeus refactoring scope, order, and constraints.*
