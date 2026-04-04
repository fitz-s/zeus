# MATH-006-TEMPORAL-CLOSURE-COEFFICIENTS

## Metadata

- Packet ID: `MATH-006`
- Priority: `P1`
- Created: `2026-04-04 America/Chicago`
- Status: `FROZEN / EVALUATION ONLY`
- Depends on: `MATH-002`
- Owner: `Math lane lead`
- Authority: `math_task.md`

## Problem Statement

The temporal_closure_weight formula uses three hardcoded coefficients:
```python
# forecast_uncertainty.py:258-263
return max(
    0.75 * time_factor,
    0.50 * peak_factor,
    0.35 * progress_factor,
)
```

**Questions to evaluate:**
1. Are 0.75, 0.50, 0.35 empirically justified?
2. Should these be data-driven per city/season?
3. Is the max() combination optimal vs weighted average?

## Evaluation Approach

### Test 1: Coefficient Sensitivity Analysis
Measure how changes to each coefficient affect calibration metrics.

### Test 2: Historical Performance by Factor Regime
Use calibration data to check accuracy when each factor dominates.

### Test 3: Alternative Combination Methods
Compare max() vs weighted sum vs geometric mean.

## Allowed Files

- `tests/test_temporal_closure_evaluation.py` (new)
- `math_task.md`
- `math_progress.md`

## Forbidden

- No changes to `src/signal/forecast_uncertainty.py`
- Evaluation only
