# MATH-002-BIN-HIT-RATE-CALIBRATION

```yaml
work_packet_id: MATH-002-BIN-HIT-RATE-CALIBRATION
packet_type: validation_packet
objective: Build bin hit-rate calibration framework and measure current system calibration quality. Second of Gemini's three required validations.
why_this_now: MATH-001 (sunset sanity) passed. Bin hit-rate calibration is the core diagnostic needed before any coefficient changes.
why_not_other_approach:
  - Skip calibration and tune coefficients directly | would perpetuate unvalidated constants
  - Use only raw p_vector | ignores Platt calibration layer which may be critical
truth_layer: For a well-calibrated system, bins predicted with probability p should hit approximately p fraction of the time.
control_layer: This packet only adds validation analysis. It does not modify any production math code.
evidence_layer: Calibration curve + reliability diagram + documented gaps.
zones_touched:
  - K3_math_model
invariants_touched:
  - none (validation only)
required_reads:
  - AGENTS.md
  - math_task.md
  - math_progress.md
  - .omx/artifacts/gemini-day0-full-math-review-2026-04-03T06-25-56Z.md
  - src/signal/ensemble_signal.py
  - src/calibration/calibration_manager.py
  - src/calibration/platt.py
files_may_change:
  - work_packets/MATH-002-BIN-HIT-RATE-CALIBRATION.md
  - tests/test_calibration_quality.py (new)
  - math_progress.md
  - math_task.md
files_may_not_change:
  - src/signal/forecast_uncertainty.py
  - src/signal/day0_signal.py
  - src/signal/ensemble_signal.py
  - src/calibration/*.py (read only)
  - architecture/**
  - docs/governance/**
  - AGENTS.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - targeted calibration quality tests
parity_required: false
replay_required: false
rollback: Remove the added validation tests. No production code is touched.
acceptance:
  - Calibration analysis covers at least 100 settled markets with shadow_signals
  - Reliability diagram shows predicted vs actual hit rates by probability bin
  - Calibration gaps documented: over-confident bins, under-confident bins
  - ECE (Expected Calibration Error) or similar metric computed
  - Results recorded in math_progress.md
evidence_required:
  - Calibration curve visualization data (can be text-based)
  - ECE or Brier score decomposition
  - Identified calibration gaps with severity
```

## Background

Gemini's external review (2026-04-03) stated:

> **Bin Hit-Rate Calibration:** Compare your predicted probabilities against actual historical hit rates. If you say 70%, it should hit ~70% of the time.

This is one of three required validations before Day0 math can be trusted for production.

## Data Sources

### Database: `state/zeus.db`

| Table | Records | Fields Used |
|-------|---------|-------------|
| `shadow_signals` | 120+ with p_cal_json | city, target_date, p_raw_json, p_cal_json, lead_hours |
| `settlements` | 1,399 | city, target_date, winning_bin, settlement_value |

### Join Logic

```sql
SELECT 
    s.city,
    s.target_date,
    s.p_raw_json,
    s.p_cal_json,
    s.lead_hours,
    t.winning_bin,
    t.settlement_value
FROM shadow_signals s
JOIN settlements t ON s.city = t.city AND s.target_date = t.target_date
WHERE s.p_raw_json IS NOT NULL
  AND t.winning_bin IS NOT NULL;
```

## Calibration Analysis Design

### 1. Bin-Level Calibration

For each probability bin [0.0-0.1), [0.1-0.2), ..., [0.9-1.0]:
- Count predictions falling in this probability range
- Count actual hits
- Compute hit rate = hits / predictions

**Perfect calibration**: hit_rate ≈ bin_midpoint

### 2. Expected Calibration Error (ECE)

$$ECE = \sum_{b=1}^{B} \frac{n_b}{N} |acc_b - conf_b|$$

Where:
- $n_b$ = number of predictions in bin b
- $acc_b$ = actual accuracy in bin b (hit rate)
- $conf_b$ = average confidence in bin b

### 3. Reliability Diagram

```
Actual Hit Rate
    1.0 |                              *
        |                           *
    0.8 |                        * 
        |                     *     (over-confident here)
    0.6 |                  *
        |               *
    0.4 |            *
        |         *  (under-confident here)
    0.2 |      *
        |   *
    0.0 +---+---+---+---+---+---+---+---+---+---+
        0.0 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1.0
                    Predicted Probability
```

### 4. Lead-Time Stratification

Calibration quality may vary by lead time:
- Lead < 6h: Day0 signals dominate
- Lead 6-24h: Ensemble + Day0 mix
- Lead > 24h: Pure ensemble

## Test Design

### Test 1: Overall Calibration Quality

```python
def test_overall_calibration_quality():
    """Compute ECE and assert it's within acceptable range."""
    data = load_shadow_signals_with_settlements()
    ece = compute_expected_calibration_error(data)
    
    print(f"ECE = {ece:.4f}")
    print(f"Sample size = {len(data)}")
    
    # Document current state (no pass/fail threshold yet)
    # This establishes baseline for future coefficient tuning
```

### Test 2: Bin-Level Hit Rates

```python
def test_bin_level_hit_rates():
    """Document hit rate for each probability decile."""
    data = load_shadow_signals_with_settlements()
    
    for prob_bin in [(0.0, 0.1), (0.1, 0.2), ..., (0.9, 1.0)]:
        predictions = filter_by_prob_range(data, prob_bin)
        hit_rate = compute_hit_rate(predictions)
        expected = (prob_bin[0] + prob_bin[1]) / 2
        gap = hit_rate - expected
        
        print(f"Prob {prob_bin}: n={len(predictions)}, "
              f"hit_rate={hit_rate:.3f}, expected={expected:.2f}, gap={gap:+.3f}")
```

### Test 3: Calibration by Lead Time

```python
def test_calibration_by_lead_time():
    """Stratify calibration by lead time buckets."""
    data = load_shadow_signals_with_settlements()
    
    for lead_bucket in ["<6h", "6-24h", ">24h"]:
        subset = filter_by_lead(data, lead_bucket)
        ece = compute_expected_calibration_error(subset)
        print(f"Lead {lead_bucket}: n={len(subset)}, ECE={ece:.4f}")
```

### Test 4: Raw vs Calibrated Comparison

```python
def test_raw_vs_calibrated_comparison():
    """Compare ECE of p_raw vs p_cal."""
    data = load_shadow_signals_with_settlements()
    
    ece_raw = compute_ece(data, use_calibrated=False)
    ece_cal = compute_ece(data, use_calibrated=True)
    
    print(f"ECE (raw): {ece_raw:.4f}")
    print(f"ECE (calibrated): {ece_cal:.4f}")
    print(f"Improvement: {(ece_raw - ece_cal) / ece_raw * 100:.1f}%")
```

## Pass Criteria

**PASS** if:
- Analysis completes without error on 100+ matched records
- Reliability diagram data is documented
- ECE is computed and recorded

**CONDITIONAL PASS** if:
- ECE > 0.10 (significant miscalibration detected)
- Specific bins show > 20% gap from expected
- These are documented as inputs for MATH-006 (coefficient tuning)

**FAIL** if:
- Data join fails or produces < 50 matched records
- Analysis cannot complete due to data quality issues

## Notes

- This packet is **validation-only**. Even if calibration gaps are found, we do not modify production code.
- Results feed into MATH-006 (coefficient tuning) decisions.
- If Platt calibration (p_cal) shows significant improvement over p_raw, that validates the calibration layer design.
- If p_cal shows no improvement, that may indicate calibration drift or insufficient training data.
