# MATH-001-SUNSET-SANITY-VALIDATION

```yaml
work_packet_id: MATH-001-SUNSET-SANITY-VALIDATION
packet_type: validation_packet
objective: Implement Gemini's required sunset sanity check — verify Day0 probability distribution narrows appropriately at T-1min before sunset with fresh trusted observation.
why_this_now: Gemini external review explicitly requires this validation before Day0 math can be considered production-ready. No existing test validates near-sunset distribution width.
why_not_other_approach:
  - Skip validation and tune coefficients directly | would perpetuate unvalidated magic constants
  - Start with bin hit-rate calibration instead | sunset sanity is simpler and provides immediate feedback on temporal_closure effectiveness
truth_layer: Day0 temporal_closure_weight and observation_weight should converge near 1.0 at sunset with fresh trusted observation. The resulting probability distribution should be tightly concentrated around the observed high.
control_layer: This packet only adds validation tests. It does not modify any production math code.
evidence_layer: Test assertions documenting observed distribution width at T-1min sunset under multiple configurations.
zones_touched:
  - K3_math_model
invariants_touched:
  - none (validation only)
required_reads:
  - AGENTS.md
  - math_task.md
  - math_progress.md
  - .omx/artifacts/gemini-day0-full-math-review-2026-04-03T06-25-56Z.md
  - src/signal/forecast_uncertainty.py
  - src/signal/day0_signal.py
  - tests/test_day0_signal.py
  - tests/test_forecast_uncertainty.py
files_may_change:
  - work_packets/MATH-001-SUNSET-SANITY-VALIDATION.md
  - tests/test_day0_signal.py
  - tests/test_forecast_uncertainty.py
  - math_progress.md
  - math_task.md
files_may_not_change:
  - src/signal/forecast_uncertainty.py
  - src/signal/day0_signal.py
  - src/signal/ensemble_signal.py
  - architecture/**
  - docs/governance/**
  - AGENTS.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - targeted sunset sanity tests
parity_required: false
replay_required: false
rollback: Remove the added validation tests. No production code is touched.
acceptance:
  - At least one test proves Day0 distribution at T-1min sunset with fresh trusted ASOS observation concentrates > 80% probability within ± 2°F of observed_high
  - At least one test documents what happens when observation is stale (> 1h) at sunset
  - Distribution width metrics are recorded in math_progress.md for future coefficient tuning
  - If distribution does NOT narrow appropriately, this is documented as a blocker for MATH-004 (sigma floor evaluation)
evidence_required:
  - pytest output for sunset sanity tests
  - documented distribution width values
  - pass/fail verdict on current temporal_closure effectiveness
```

## Background

Gemini's external review (2026-04-03) stated:

> **The "Sunset Sanity" Check:** Run the math at 1 minute before sunset. The probability distribution should be a tight spike around the `observed_high`. If it's still wide, your `temporal_closure_weight` is too weak.

This is one of three required validations before Day0 math can be trusted for production.

## Test Design

### Test 1: Fresh trusted observation at sunset

**Setup**:
- City: NYC (KLGA)
- Target date: 2026-04-03
- Observation source: "wu/asos" (trusted)
- Observation time: 1 minute before sunset (e.g., sunset at 19:30 → observation at 19:29)
- Observation age: < 5 minutes (fresh)
- Observed high: 72°F
- Current temp: 68°F
- ENS members: realistic spread around 70-74°F

**Expected behavior**:
- `observation_weight()` should return > 0.95
- `temporal_closure_weight()` should return > 0.90
- `p_vector()` should concentrate > 80% probability in the bin containing 72°F

**Assertions**:
```python
assert day0.observation_weight() > 0.95, "Near-sunset with fresh trusted obs should dominate"
p = day0.p_vector(bins)
bin_idx = find_bin_containing(72, bins)
assert p[bin_idx] > 0.80, f"Sunset spike too weak: {p[bin_idx]:.2%}"
```

### Test 2: Stale observation at sunset

**Setup**:
- Same as Test 1, but observation_time is 2 hours before sunset
- Observation age: ~2 hours (stale)

**Expected behavior**:
- `observation_weight()` should be lower (< 0.8 due to freshness decay)
- Distribution should be wider
- This documents the current stale-handling behavior for MATH-005

### Test 3: Untrusted observation at sunset

**Setup**:
- Same as Test 1, but observation_source is "pws" (untrusted)

**Expected behavior**:
- `source_factor` = 0.0 (per FEAT-P2H-007 fix)
- Distribution should be wider than fresh trusted case
- Documents the current untrusted-handling behavior

### Test 4: Multi-city comparison

**Setup**:
- Run Test 1 configuration for: NYC, London, Tokyo
- Document distribution widths for each

**Purpose**:
- Verify sunset sanity holds across timezone/unit configurations
- Identify any city-specific issues

## Metrics to Record

For each test configuration:
- `observation_weight`: float
- `temporal_closure_weight`: float
- `p_max`: max probability in any single bin
- `p_within_2F`: sum of probabilities within ±2°F of observed_high
- `p_within_4F`: sum of probabilities within ±4°F of observed_high
- `effective_std`: derived standard deviation of the distribution

These metrics will inform MATH-004 (sigma floor evaluation) and MATH-006 (coefficient tuning).

## Pass Criteria

**PASS** if:
- Fresh trusted sunset case achieves `p_within_2F > 0.80`
- All tests run without error
- Metrics are documented in math_progress.md

**CONDITIONAL PASS** if:
- Fresh trusted sunset case achieves `p_within_2F > 0.60` but < 0.80
- This indicates temporal_closure is partially effective but may need tuning
- Documented as input for MATH-004/MATH-006

**FAIL** if:
- Fresh trusted sunset case achieves `p_within_2F < 0.60`
- This indicates temporal_closure is fundamentally too weak
- Blocks further math work until root cause is identified

## Notes

- This packet is **validation-only**. Even if tests reveal issues, we do not modify production code in this packet.
- Results feed into MATH-004 (sigma floor) and MATH-006 (coefficient tuning) decisions.
- The existing temporal_closure has already been fixed from multiplication to max() in FEAT-P2H-008. This validation tests whether that fix is sufficient.
