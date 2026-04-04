# MATH-006-TEMPORAL-CLOSURE-COEFFICIENTS

```yaml
work_packet_id: MATH-006-TEMPORAL-CLOSURE-COEFFICIENTS
packet_type: evaluation_packet
priority: P1
status: COMPLETE
depends_on: [MATH-002]
owner: Math lane lead
authority: math_task.md
objective: Evaluate the temporal_closure coefficients (0.75, 0.50, 0.35) to determine if they are appropriate.
why_this_now: After MATH-002 calibration framework is complete, can evaluate whether coefficients are empirically justified.
why_not_other_approach:
  - Change coefficients immediately | need evidence first, validation-before-change discipline
  - Skip evaluation | coefficients directly affect trading accuracy
truth_layer: These coefficients control how time, peak confidence, and daylight progress blend to form observation weight.
control_layer: Evaluation only - no code changes until evidence gathered.
evidence_layer: Test results documenting coefficient behavior, sensitivity, and alternatives.
zones_touched:
  - K3_math_model
invariants_touched:
  - none (evaluation only)
required_reads:
  - AGENTS.md
  - math_task.md
  - math_progress.md
  - src/signal/forecast_uncertainty.py
files_may_change:
  - tests/test_temporal_closure_evaluation.py
  - math_progress.md
  - math_task.md
files_may_not_change:
  - src/signal/forecast_uncertainty.py
  - src/signal/day0_signal.py
  - architecture/**
  - docs/governance/**
schema_changes: false
rollback: Remove evaluation tests. No production code is touched.
acceptance:
  - Document current coefficient dominance patterns
  - Evaluate sensitivity to coefficient changes
  - Document alternative coefficient combinations
  - Produce recommendation for whether coefficients should change
evidence_required:
  - pytest output for evaluation tests
  - documented coefficient behavior
  - recommendation with rationale
```

## Metadata

- Packet ID: `MATH-006`
- Priority: `P1`
- Created: `2026-04-04 America/Chicago`
- Status: `COMPLETE`
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
