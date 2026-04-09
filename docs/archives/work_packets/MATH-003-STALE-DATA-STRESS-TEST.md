# MATH-003-STALE-DATA-STRESS-TEST

```yaml
work_packet_id: MATH-003-STALE-DATA-STRESS-TEST
packet_type: validation_packet
objective: Test that stale (2h+) trusted observations appropriately expand distribution width, especially during peak heating. Third of Gemini's required validations.
why_this_now: MATH-001 and MATH-002 passed. This completes Gemini's required validation triad before any coefficient changes.
why_not_other_approach:
  - Skip validation and assume current staleness handling is correct | current 3h linear decay may be too permissive
  - Test only at sunset | peak heating mid-day is the critical stress case
truth_layer: Stale observations should expand sigma because temperature may have changed significantly since the observation was taken.
control_layer: This packet only adds validation tests. It does not modify any production math code.
evidence_layer: Documented sigma expansion ratios for fresh vs stale observations across daylight phases.
zones_touched:
  - K3_math_model
invariants_touched:
  - none (validation only)
required_reads:
  - AGENTS.md
  - math_task.md
  - math_progress.md
  - src/signal/forecast_uncertainty.py (day0_nowcast_context, freshness_factor)
  - src/signal/day0_signal.py
files_may_change:
  - work_packets/MATH-003-STALE-DATA-STRESS-TEST.md
  - tests/test_day0_signal.py
  - math_progress.md
  - math_task.md
files_may_not_change:
  - src/signal/forecast_uncertainty.py
  - src/signal/day0_signal.py
  - architecture/**
  - docs/governance/**
  - AGENTS.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - targeted stale-data stress tests
parity_required: false
replay_required: false
rollback: Remove the added validation tests. No production code is touched.
acceptance:
  - Test documents sigma expansion when observation is 2h stale vs fresh
  - Test covers both peak-heating (mid-day) and post-sunset scenarios
  - Test identifies whether current 3h decay is appropriate or too permissive
  - Results recorded in math_progress.md
evidence_required:
  - pytest output for stale-data stress tests
  - documented sigma/distribution width for fresh vs 1h vs 2h vs 3h stale
  - pass/fail verdict on current freshness handling
```

## Background

Gemini's external review (2026-04-03) stated:

> **Stale Trusted Observation Stress Test:** Force a scenario where the last trusted observation is 2 hours old during peak heating. The sigma should expand significantly.

Current code in `forecast_uncertainty.py:479`:
```python
freshness_factor = max(0.0, 1.0 - min(1.0, age_hours / 3.0))
```

This is a **linear decay over 3 hours**. Gemini's concern: during peak heating (mid-day), 2 hours is long enough for temperature to change dramatically. A 2h-stale observation should not be trusted as much as this linear decay suggests.

## Critical Insight from Earlier Analysis

The proposed mathematical framework suggests **Brownian motion** sigma expansion:

$$\sigma_{stale\_obs}(\Delta t) = \sqrt{\sigma^2_{sensor} + D_{peak} \cdot \Delta t}$$

Where $D_{peak}$ is the diffusion coefficient during peak heating (higher than night).

Current behavior: linear weight decay
Proposed behavior: square-root sigma growth

This test will measure current behavior and document whether it's adequate.

## Test Design

### Test 1: Fresh vs Stale at Peak Heating (Mid-Day)

**Setup**:
- `daylight_progress = 0.5` (mid-day, peak heating period)
- `hours_remaining = 6.0`
- Same observed_high, current_temp, ENS spread
- Vary `observation_time` to create 0h, 1h, 2h, 3h staleness

**Expected behavior**:
- Fresh (0h): highest `observation_weight`, narrowest distribution
- 2h stale: significantly lower `observation_weight`, wider distribution
- 3h stale: `freshness_factor = 0`, observation weight minimal

### Test 2: Fresh vs Stale at Post-Sunset

**Setup**:
- `daylight_progress = 1.0` (post-sunset)
- `hours_remaining = 0.0`
- Vary staleness as above

**Expected behavior**:
- At sunset, staleness should matter less because temperature is "locked"
- But very stale observation (3h) should still show some expansion

### Test 3: Sigma Expansion Ratio

Measure the ratio: `std(stale) / std(fresh)` for each staleness level.

**Gemini's implicit expectation**: 
- 2h stale during peak heating should show at least 2x sigma expansion
- If expansion is < 1.5x, current decay is too permissive

### Test 4: Brownian vs Linear Comparison

Document whether observed expansion follows:
- Linear: $\sigma \propto \Delta t$
- Square-root: $\sigma \propto \sqrt{\Delta t}$

This informs whether the proposed Brownian model is justified.

## Metrics to Record

For each (staleness, daylight_phase) combination:
- `freshness_factor`: from `day0_nowcast_context`
- `observation_weight`: from `Day0Signal.observation_weight()`
- `effective_std`: derived from p_vector distribution
- `sigma_expansion_ratio`: `std(stale) / std(fresh)`

## Pass Criteria

**PASS** if:
- Tests complete and document current behavior
- Staleness does cause measurable sigma expansion
- Results are recorded for MATH-005 (freshness threshold tuning)

**CONDITIONAL PASS** if:
- 2h stale at peak heating shows < 1.5x sigma expansion
- This indicates current 3h decay is too permissive
- Documented as input for MATH-005

**FAIL** if:
- Tests cannot be constructed due to code issues
- Staleness has no effect on distribution (would indicate a bug)
