# MATH-005-FRESHNESS-SIGMA-CONNECTION

## Metadata

- Packet ID: `MATH-005`
- Priority: `P0 (blocking)`
- Created: `2026-04-04 America/Chicago`
- Status: `FROZEN / READY TO IMPLEMENT`
- Depends on: `MATH-003`
- Triggered by: `MATH-003 defect finding`
- Owner: `Math lane lead`
- Authority: `math_task.md`

## Problem Statement

MATH-003 revealed that `freshness_factor` is computed but **not propagated** to distribution width calculations.

### Evidence from MATH-003:

```
| Stale (h) | freshness_factor | obs_weight | effective_std |
|-----------|------------------|------------|---------------|
| 0.0       | 1.000            | 0.5000     | 1.59°F        |
| 2.0       | 0.333            | 0.5000     | 1.59°F        |
| 3.0       | 0.000            | 0.5000     | 1.59°F        |

Sigma expansion ratio: 1.00x (NO EXPANSION)
```

**Root cause**: `day0_post_peak_sigma(unit, peak_confidence)` does not receive or use `freshness_factor`.

## Solution Design

### Minimal Change Approach

Add optional `freshness_factor` parameter to `day0_post_peak_sigma`:

```python
def day0_post_peak_sigma(
    unit: str, 
    peak_confidence: float,
    freshness_factor: float = 1.0,  # NEW: default 1.0 preserves existing behavior
) -> float:
    peak = min(1.0, max(0.0, float(peak_confidence)))
    fresh = min(1.0, max(0.01, float(freshness_factor)))  # floor at 0.01 to avoid div/0
    base_sigma = sigma_instrument(unit).value
    
    # Original formula: base_sigma * (1.0 - peak * 0.5)
    # New: expand sigma when data is stale (low freshness)
    peak_shrinkage = 1.0 - peak * 0.5
    staleness_expansion = 1.0 + (1.0 - fresh) * 0.5  # up to 1.5x at freshness=0
    
    return base_sigma * peak_shrinkage * staleness_expansion
```

### Rationale

1. **Backward compatible**: Default `freshness_factor=1.0` returns the same value as before
2. **Bounded expansion**: At `freshness=0`, sigma expands by 1.5x (not unbounded)
3. **Conservative**: First fix is modest; can tune after calibration data
4. **Testable**: Clear expected behavior for tests

### Alternative (Brownian model, deferred)

The proposed Brownian motion model:
```python
sigma_stale = sqrt(sigma_sensor² + D_peak * Δt)
```

This is more physically correct but requires:
- Introducing new physics parameters (D_peak)
- More extensive testing
- Potentially larger changes to the architecture

**Decision**: Implement minimal fix first, validate with MATH-003 tests, then consider Brownian model as MATH-006.

## Allowed Files

- `src/signal/forecast_uncertainty.py` — add freshness_factor parameter
- `src/signal/day0_signal.py` — pass freshness_factor to day0_post_peak_sigma
- `tests/test_day0_signal.py` — update MATH-003 tests to expect expansion
- `tests/test_forecast_uncertainty.py` — add tests for new parameter
- `math_task.md` — update status
- `math_progress.md` — record results

## Forbidden

- No changes to sigma_instrument() baseline values
- No changes to 50% floor (MATH-004 scope)
- No changes to 3h freshness threshold (separate from sigma connection)
- No introduction of D_peak or Brownian model yet

## Acceptance Criteria

1. MATH-003 Test 3 (sigma expansion profile) shows expansion > 1.0x for stale data
2. MATH-003 Test 4 (2h stale at peak heating) shows expansion >= 1.25x
3. All existing tests pass (backward compatibility via default parameter)
4. New tests document expected freshness-sigma relationship

## Rollback

Revert parameter to fixed `freshness_factor=1.0` internally if issues found.
