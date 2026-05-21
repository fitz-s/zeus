# Phase 5 Plan — WeatherRegimeTag Production Consumers + Correlation Shrinkage

**Authority**: v4 §M line 1105 · math spec §15.4 · 06_PHASE_5_WEATHER_REGIME_CORRELATION.md
**Date**: 2026-05-21 · **Status**: DRAFT — pending executor dispatch

## Context

Phase 3 T1 (merged at phase3_track1_scaffold_landed) delivers:
- `WeatherRegimeTag` enum (HEAT_DOME / COLD_SNAP / NORMAL / SHOULDER_SEASON / SOURCE_ANOMALY / UNKNOWN) in `src/contracts/weather_regime_tag.py`
- `tail_correlation_cluster_for(city, regime, target_date)` in `src/strategy/correlation_cluster.py` (production body lands in Phase 3 T1 SCAFFOLD; Phase 5 depends on this).

Current call site: `src/engine/evaluator.py:4093` — `cluster_exposure_for_bankroll(portfolio, city.name, sizing_bankroll)` uses raw notional sum; cluster key is `city.name`, no regime conditioning. Function defined at `src/state/portfolio.py:2089`.

**Schema version baseline**: `src/state/db.py:852` `SCHEMA_VERSION = 15` on origin/main (2026-05-20). Phase 3 will land 15→16→17 before Phase 5 dispatches. **Phase 5 T2 bumps from N to N+1 where N = SCHEMA_VERSION at dispatch time** (NOT pinned to a number).

## Track 1 — Shrinkage Estimator (Ledoit-Wolf)

**File**: `src/strategy/correlation_shrinkage.py` (NEW) · **LOC**: 80–120 · **Schema**: none · **Deps**: numpy

Implement `ledoit_wolf_shrunk_correlation(residuals, target="diagonal") -> ShrinkageEstimate` per math spec §15.4 verbatim:

```
Σ_shrunk = (1 - δ*) · S + δ* · D
δ* = π / (γ × n)
```

- `D` = diagonal of `S` (retain variances, zero off-diagonal)
- `π` = sum of asymptotic variances of sample covariance entries
- `γ` = squared Frobenius distance `‖S - D‖²_F`
- Input shape `(n, p)`; ensemble residuals or daily settlement anomalies

`ShrinkageEstimate` frozen dataclass fields: `sample_correlation: np.ndarray`, `shrunk_correlation: np.ndarray`, `intensity: float`, `target_kind: Literal["diagonal","identity","constant_correlation","single_factor"]`, `n_observations: int`, `p_dimensions: int`.

**Tests** (`tests/test_correlation_shrinkage.py` NEW):
1. `test_intensity_converges_diagonal_target` — AR(1), p=20, n∈{50,100,250,500,1000}; δ* monotonically decreasing toward 0.
2. `test_intensity_bounded_sparse` — n<p → δ* bounded away from 0 (matrix invertible).
3. `test_known_diagonal_input` — diagonal input → γ=0 edge; estimator MUST clamp δ*=0 (no divide-by-zero); test docstring documents the clamp guard.
4. `test_n_equals_1_edge_case` — n=1 → δ*=1.0 (full shrinkage).

**Acceptance**: exports `ledoit_wolf_shrunk_correlation` + `ShrinkageEstimate`; file header (Created/Audited/Authority); δ* formula cited verbatim in docstring with Ledoit & Wolf 2003 reference; γ=0 clamp implemented; all 4 tests pass.

## Track 2 — Regime-Conditional Matrix Store

**File**: `src/strategy/regime_correlation_store.py` (NEW) · **LOC**: 100–150 · **Schema**: bump N→N+1 in zeus-world.db (N = SCHEMA_VERSION at dispatch) · **Deps**: T1, WeatherRegimeTag, sqlite3

**DB location**: zeus-world.db (NOT zeus-forecasts.db). Rationale: per-regime fit data is canonical truth (operator-tunable, persisted across boots, audited), not derived from upstream forecast/observation streams.

`RegimeCorrelationStore`:
- `fit(regime: WeatherRegimeTag, residuals: np.ndarray) -> ShrinkageEstimate` — fits and persists. **`fit(UNKNOWN, …)` raises `ValueError` at the fit() call** (NOT deferred to get()); UNKNOWN never enters the cache.
- `get(regime, cities) -> np.ndarray` — returns slice for city subset; `KeyError` if regime not yet fit.
- `invalidate(regime)` — clears cache row.
- Persistence: `regime_correlation_cache` table (`regime TEXT PK, cities_json TEXT, matrix_json TEXT, fitted_at TEXT, n_observations INT, intensity REAL`).
- Migration step (Zeus standard pattern, see `src/state/db.py:2477`): bump `SCHEMA_VERSION` constant N→N+1, add `ensure_table` import to `init_schema`, set `PRAGMA user_version = SCHEMA_VERSION` as the final step; CI hook `scripts/check_schema_version.py` enforces lockstep via pinned hash.

**Tests** (`tests/test_regime_correlation_store.py` NEW):
1. `test_heat_dome_tighter_than_normal` — synthetic residuals; stored matrix `HEAT_DOME[i,j] > NORMAL[i,j]` off-diagonal.
2. `test_fit_unknown_raises_valueerror` — `fit(UNKNOWN, …)` → `ValueError` at fit() (Phase 3 R-1 antibody enforced at write boundary).
3. `test_cache_round_trip` — fit → persist → reload → matrix equality within 1e-9.
4. `test_schema_migration_N_to_N_plus_1` — pre-migration world DB at version N gains `regime_correlation_cache` table; `PRAGMA user_version == N+1` post-migration.

**Acceptance**: exports `RegimeCorrelationStore`; file header; `fit(UNKNOWN)` raises ValueError; SCHEMA_VERSION bumped from dispatch-time N to N+1 with pinned-hash CI update; all 4 tests pass.

## Track 3 — `cluster_exposure_for_bankroll` Consumes Shrunk Matrix

**Files**: `src/state/portfolio.py`, `src/engine/evaluator.py` (mod) · **LOC**: 60–90 · **Schema**: none · **Deps**: T1, T2, Phase 3 T1 production (`regime_tag_for`, `tail_correlation_cluster_for`)

1. Add optional `regime_correlation_store: RegimeCorrelationStore | None = None` to `cluster_exposure_for_bankroll` at `src/state/portfolio.py:2089`.
2. When store provided AND regime ≠ UNKNOWN: portfolio-variance exposure `σ²_p = wᵀ Σ_shrunk w`, `w_i = notional_i / bankroll`; replaces raw notional sum.
3. `src/engine/evaluator.py:4093` caller passes regime via `regime_tag_for(city, target_date, decision_time, conn)` + store; falls back to current notional-sum path when regime=UNKNOWN or store=None.
4. Regression invariant: positive inter-city correlation → variance-based cap ≤ notional-sum cap; heat-dome cap strictly tighter than normal.

**Tests** (`tests/test_cluster_exposure_shrunk.py` NEW):
1. `test_heat_dome_under_allocates_vs_normal` — same portfolio, HEAT_DOME vs NORMAL store → HEAT_DOME cap triggers at lower notional.
2. `test_fallback_to_notional_sum_when_store_none` — `store=None` reproduces current behavior byte-for-byte.
3. `test_variance_cap_equals_notional_uncorrelated` — D=I (uncorrelated) → variance-cap equals notional-cap within float tolerance.
4. `test_unknown_regime_uses_notional_fallback` — UNKNOWN at call site → fallback path; no exception propagates.

**Acceptance**: signature backward-compatible (store optional); evaluator passes regime+store; existing `tests/test_runtime_guards.py:12636` still passes; all 4 new tests pass.

## Cross-Track Invariants

- No track modifies `decision_events` schema (Phase 5 scope boundary).
- No track promotes candidates to live (Phase 6 scope).
- All new files carry file header (Created / Last audited / Authority basis).
- Math spec §15.4 intensity formula cited verbatim in `correlation_shrinkage.py` docstring.
- Schema bump language is dispatch-time-dynamic (N→N+1), NOT pinned to a number.

## Dependency Order

T1 → T2 → T3 (T2 imports T1; T3 imports T1+T2). Phase 3 T1 production body (`regime_tag_for`, `tail_correlation_cluster_for`) must land before T3 dispatch.
