# MATH-004-SIGMA-FLOOR-EVALUATION

```yaml
work_packet_id: MATH-004-SIGMA-FLOOR-EVALUATION
packet_type: evaluation_packet
priority: P1
status: COMPLETE
depends_on: [MATH-001, MATH-002, MATH-003, MATH-005]
owner: Math lane lead
authority: math_task.md
objective: Evaluate the 50% sigma floor (peak * 0.5) to determine if it is appropriate or needs adjustment.
why_this_now: MATH-003/005 revealed staleness issues; now need to evaluate whether the base floor itself is correct.
why_not_other_approach:
  - Change floor immediately | need evidence first, validation-before-change discipline
  - Skip evaluation | floor is a critical regularization parameter
truth_layer: The floor ensures sigma never collapses below a minimum, providing regularization against discrete settlement quantization noise.
control_layer: Evaluation only - no code changes until evidence gathered.
evidence_layer: Test results documenting floor behavior, alternatives, and interaction with staleness.
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
  - tests/test_sigma_floor_evaluation.py
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
  - Document current floor behavior at various peak_confidence levels
  - Evaluate alternative floor coefficients (0.25, 0.40, 0.60)
  - Document interaction with MATH-005 staleness expansion
  - Produce recommendation for whether floor should change
evidence_required:
  - pytest output for evaluation tests
  - documented floor behavior values
  - recommendation with rationale
```

## Metadata

- Packet ID: `MATH-004`
- Priority: `P1`
- Created: `2026-04-04 America/Chicago`
- Status: `COMPLETE`
- Depends on: `MATH-001, MATH-002, MATH-003, MATH-005`
- Owner: `Math lane lead`
- Authority: `math_task.md`

## Problem Statement

The current sigma calculation uses a 50% floor:
```python
# forecast_uncertainty.py:524
return base_sigma * (1.0 - peak * 0.5)
```

This means sigma can never shrink below 50% of base, even at 100% peak confidence.

**Questions to evaluate:**
1. Is the 50% floor too conservative (keeping sigma too wide)?
2. Is it too aggressive (allowing sigma to shrink too much)?
3. Should it be replaced with a data-driven floor?

## Evaluation Approach

### Test 1: Distribution Width at High Confidence

Measure effective distribution width at various peak_confidence levels.
If distributions are too wide at high confidence, the floor is too conservative.

### Test 2: Historical Hit-Rate by Peak Confidence

Using MATH-002 calibration data, check if high peak_confidence predictions
are appropriately calibrated. Overconfidence suggests floor is too low.

### Test 3: Sunset Boundary Behavior

At sunset (daylight_progress=1.0), the distribution should collapse to
near-zero width. Check if the 50% floor prevents proper collapse.

### Test 4: Compare Current vs Alternative Floors

Simulate what calibration would look like with 40%, 30%, 20% floors.

## Allowed Files

- `tests/test_sigma_floor_evaluation.py` (new)
- `math_task.md`
- `math_progress.md`
- `work_packets/MATH-004-SIGMA-FLOOR-EVALUATION.md`

## Forbidden

- No changes to `src/signal/forecast_uncertainty.py`
- No changes to any production code
- Evaluation only, recommendations recorded in math_progress.md

## Acceptance Criteria

1. Tests document current floor behavior
2. Tests compare calibration under different floor scenarios
3. Clear recommendation with evidence recorded
