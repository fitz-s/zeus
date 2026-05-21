# Phase 5 — WeatherRegimeTag (production) + Correlation Matrix Shrinkage

## v4 §M scope

Line 1105: "Math spec §15.4 correlation-matrix-via-shrinkage — Phase 5 `WeatherRegimeTag` dependency"

## Phase 3 dependency

Phase 3 T1 lands `WeatherRegimeTag` enum + `correlation_cluster_for(city, regime)` as a foundation. Phase 5 extends:
- Multi-regime correlation matrices (one per `WeatherRegimeTag` value)
- Shrinkage estimator per math spec §15.4
- Integration with `cluster_exposure_for_bankroll` (existing on main, verify line)

## Why shrinkage is structurally required

Sample covariance matrix `S` of `n` historical realizations across `p` city-temperature series:
- When `n < p`, `S` is singular — cannot invert for Markowitz / risk-parity / cluster-cap operations.
- Even `n > p`, if `n` is small (Zeus has limited live-trading history per dossier §8), eigenvalue dispersion is overstated: smallest eigenvalues collapse toward 0, largest inflate. Direct use produces unstable position allocations.
- Shrinkage replaces `S` with convex combination `Σ_shrunk = (1 - δ) · S + δ · T` where `T` is a structured target (identity, constant-correlation, single-factor). `δ ∈ [0, 1]` is selected to minimize expected MSE.

**Ledoit-Wolf optimal intensity** (math spec §15.4; Ledoit & Wolf 2003; Ledoit & Wolf 2004 "Honey, I shrunk the sample covariance matrix"):
```
δ* = π / (γ × n)
```
where
- `π` = sum of asymptotic variances of sample covariance entries (estimable from data)
- `γ` = squared Frobenius distance between sample covariance `S` and diagonal target `D`
- `n` = number of observations

Target is the **diagonal** matrix `D` formed from the diagonal entries of `S` (retain variances, zero all off-diagonal covariances). NOT constant-correlation. Per math spec §15.4 verbatim.

Result: `Σ_shrunk` is invertible, well-conditioned, and provably MSE-optimal over the convex family `(1-δ)·S + δ·D`.

## Implementation surfaces

### T1 Shrinkage estimator

`src/strategy/correlation_shrinkage.py` (NEW):
```python
@dataclass(frozen=True)
class ShrinkageEstimate:
    sample_correlation: np.ndarray         # raw S
    shrunk_correlation: np.ndarray         # Σ_shrunk
    intensity: float                       # δ*
    target_kind: Literal["identity", "constant_correlation", "single_factor"]
    n_observations: int
    p_dimensions: int

def ledoit_wolf_shrunk_correlation(
    residuals: np.ndarray,                 # shape (n, p) — ensemble residuals or daily returns
    target: Literal["diagonal", "identity", "constant_correlation", "single_factor"] = "diagonal",
) -> ShrinkageEstimate: ...
```

Tests: known-answer cases (synthetic data with known underlying correlation; shrinkage estimate must converge as `n → ∞`); edge cases (n=1, n<p, perfect correlation, diagonal target).

### T2 Regime-conditional matrices

`src/strategy/regime_correlation_store.py` (NEW): caches a shrunk correlation matrix per `WeatherRegimeTag` value. Heat dome regime produces tighter inter-city correlation than normal regime; cold snap may produce asymmetric tail correlation; shoulder-season produces near-decorrelation.

Cache invalidation: re-fit when new residuals arrive (e.g., post-settlement); persist in `config/city_correlation_matrix.json` extended schema or new yaml file.

### T3 Cluster cap integration

`src/engine/evaluator.py:~4068` `cluster_exposure_for_bankroll` (current) uses static cluster definition (likely K3 from project memory `keyFiles: city_correlation_matrix.json`). Phase 5 changes:
- Cluster ID comes from `correlation_cluster_for(city, regime)` (Phase 3 T1).
- Cap formula uses `Σ_shrunk` to compute portfolio variance instead of summing notional.
- Result: heat-dome regime tightens cap (more correlated → less notional capacity); normal regime loosens cap.

### Math spec §15.4 verification

Before Phase 5 plan locks, READ `docs/reference/zeus_math_spec.md` §15.4 verbatim. Verify:
- Shrinkage target prescription matches Ledoit-Wolf (or specifies alternative).
- Intensity formula present.
- Sample-vs-shrunk MSE bound stated.
- Verification cohort specified (back-test design).

If spec is incomplete → escalate to operator before dispatch. Do not invent formula from prose.

## Schema impact

- `regime_correlation_cache` table (world or forecasts; small footprint).
- Optional: `correlation_fit_runs` audit table.
- Schema bump: world or forecasts 17→18.

## Verifier probes

1. `src/strategy/correlation_shrinkage.py::ledoit_wolf_shrunk_correlation` exists with documented intensity formula matching math spec §15.4.
2. Synthetic test: AR(1) process residuals → fitted intensity converges to known closed-form value as `n → ∞`.
3. `regime_correlation_store` cache produces strictly different matrices for `WeatherRegimeTag.HEAT_DOME` vs `WeatherRegimeTag.NORMAL` on the same city set.
4. `cluster_exposure_for_bankroll` consumes shrunk matrix; portfolio-variance-based cap is provably ≤ notional-sum-based cap under positive inter-city correlation.
5. Heat-dome regime under-allocates vs normal (regression test on synthetic regime).

## What Phase 5 does NOT do

- Promote candidates to live (Phase 6).
- Change Kelly multiplier composition (Phase 0 PR 2+7 already shipped `EffectiveKellyContext`; Phase 3 added shoulder haircut).
- Touch `decision_events` schema.
