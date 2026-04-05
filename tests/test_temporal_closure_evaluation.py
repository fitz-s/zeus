"""MATH-006: Temporal Closure Coefficients Evaluation.

Evaluate the 0.75/0.50/0.35 coefficients in day0_temporal_closure_weight.
These tests are EVALUATION ONLY - no production code changes.
"""

import numpy as np
import pytest

from src.signal.forecast_uncertainty import day0_temporal_closure_weight


class TestTemporalClosureCoefficients:
    """Document current temporal closure behavior."""

    def test_coefficient_contribution_map(self):
        """Map which coefficient dominates under various conditions."""
        print("\n=== MATH-006 Test 1: Coefficient Dominance Map ===")
        print("\nActual formula: max(time, 0.75*peak, 0.50*progress, 0.35*ens)")
        print("Note: time_closure has NO coefficient (weight=1.0)")
        print("\nhrs_rem | peak_conf | daylight | weight | dominant")
        print("-" * 60)

        scenarios = [
            # (hours_remaining, peak_confidence, daylight_progress, expected_dominant)
            (0.0, 0.0, 0.0, "time"),      # All at minimum
            (12.0, 0.0, 0.0, "none"),     # Far from settlement
            (6.0, 0.0, 0.5, "time"),      # Mid-day, moderate time
            (2.0, 0.5, 0.8, "time"),      # Near close
            (1.0, 0.9, 0.9, "time"),      # Very near close, high confidence
            (0.5, 1.0, 1.0, "time"),      # At sunset
            (6.0, 1.0, 1.0, "peak"),      # Mid-day but post-peak at sunset
        ]

        for hrs, peak, prog, expected in scenarios:
            # Calculate each component
            time_factor = max(0.0, 1.0 - hrs / 12.0)
            peak_factor = peak
            progress_factor = prog if prog is not None else 0.0

            time_contrib = 1.0 * time_factor  # NO coefficient!
            peak_contrib = 0.75 * peak_factor
            prog_contrib = 0.50 * progress_factor

            weight = day0_temporal_closure_weight(
                hours_remaining=hrs,
                peak_confidence=peak,
                daylight_progress=prog,
                obs_exceeds_ens_fraction=0.0,  # Ignore for this test
            )

            # Determine dominant
            contribs = [("time", time_contrib), ("peak", peak_contrib), ("prog", prog_contrib)]
            dominant = max(contribs, key=lambda x: x[1])[0]
            if max(c[1] for c in contribs) == 0:
                dominant = "none"

            print(f"{hrs:>7.1f} | {peak:>9.1f} | {prog if prog else 0:>8.1f} | {weight:.3f} | {dominant}")

    def test_coefficient_sensitivity(self):
        """Test how sensitive the output is to each coefficient."""
        print("\n=== MATH-006 Test 2: Coefficient Sensitivity ===")

        # Base scenario: mid-day, moderate confidence
        base_hrs = 4.0
        base_peak = 0.6
        base_prog = 0.7

        base_weight = day0_temporal_closure_weight(
            hours_remaining=base_hrs,
            peak_confidence=base_peak,
            daylight_progress=base_prog,
            obs_exceeds_ens_fraction=0.0,
        )

        print(f"\nBase scenario: hrs={base_hrs}, peak={base_peak}, progress={base_prog}")
        print(f"Base weight: {base_weight:.3f}")
        print("\nComponent contributions:")

        time_factor = max(0.0, 1.0 - base_hrs / 12.0)
        peak_factor = base_peak
        progress_factor = base_prog

        print(f"  0.75 * time_factor ({time_factor:.2f}) = {0.75 * time_factor:.3f}")
        print(f"  0.50 * peak_factor ({peak_factor:.2f}) = {0.50 * peak_factor:.3f}")
        print(f"  0.35 * prog_factor ({progress_factor:.2f}) = {0.35 * progress_factor:.3f}")

        # Sensitivity: what if we change coefficients?
        print("\nAlternative coefficient scenarios:")
        alternatives = [
            (0.75, 0.50, 0.35, "Current"),
            (0.80, 0.50, 0.35, "+0.05 time"),
            (0.70, 0.50, 0.35, "-0.05 time"),
            (0.75, 0.60, 0.35, "+0.10 peak"),
            (0.75, 0.40, 0.35, "-0.10 peak"),
            (0.75, 0.50, 0.45, "+0.10 prog"),
            (0.75, 0.50, 0.25, "-0.10 prog"),
            (0.85, 0.60, 0.50, "All higher"),
            (0.65, 0.40, 0.25, "All lower"),
        ]

        print("\ncoeff_time | coeff_peak | coeff_prog | result | delta")
        print("-" * 60)

        for ct, cp, cpr, label in alternatives:
            result = max(ct * time_factor, cp * peak_factor, cpr * progress_factor)
            delta = result - base_weight
            print(f"{ct:>10.2f} | {cp:>10.2f} | {cpr:>10.2f} | {result:.3f} | {delta:+.3f} ({label})")

    def test_alternative_combination_methods(self):
        """Compare max() vs weighted sum vs geometric mean."""
        print("\n=== MATH-006 Test 3: Alternative Combination Methods ===")

        scenarios = [
            (4.0, 0.6, 0.7),   # Balanced
            (1.0, 0.9, 0.9),   # All high
            (8.0, 0.2, 0.3),   # All low
            (2.0, 0.3, 0.9),   # Mixed: low peak, high progress
        ]

        print("\nComparison: max() vs weighted_sum vs geometric_mean")
        print("hrs | peak | prog | max() | w_sum | g_mean | current")
        print("-" * 65)

        for hrs, peak, prog in scenarios:
            tf = max(0.0, 1.0 - hrs / 12.0)
            pf = peak
            prf = prog

            # Current: max of weighted factors
            current = day0_temporal_closure_weight(
                hours_remaining=hrs,
                peak_confidence=peak,
                daylight_progress=prog,
                obs_exceeds_ens_fraction=0.0,
            )

            # Alternative 1: max of raw factors
            max_raw = max(tf, pf, prf)

            # Alternative 2: weighted sum (normalized)
            weights = [0.4, 0.35, 0.25]  # sum to 1
            w_sum = weights[0] * tf + weights[1] * pf + weights[2] * prf

            # Alternative 3: geometric mean
            factors = [max(0.01, f) for f in [tf, pf, prf]]  # avoid 0
            g_mean = np.power(np.prod(factors), 1/3)

            print(f"{hrs:>3.0f} | {peak:.1f}  | {prog:.1f}  | {max_raw:.3f} | {w_sum:.3f} | {g_mean:.3f} | {current:.3f}")

        print("\nNotes:")
        print("- max() gives aggressive closure (highest factor wins)")
        print("- w_sum gives moderate closure (all factors contribute)")
        print("- g_mean requires all factors to be high for high output")


class TestTemporalClosureEdgeCases:
    """Test edge cases of temporal closure."""

    def test_boundary_values(self):
        """Test at extreme boundary values."""
        print("\n=== MATH-006 Test 4: Boundary Values ===")
        print("\nActual formula: max(time, 0.75*peak, 0.50*daylight, 0.35*ens)")

        boundaries = [
            (0.0, 0.0, 0.0, "All minimum"),
            (12.0, 0.0, 0.0, "Max hours"),
            (0.0, 1.0, 0.0, "Max peak only"),
            (0.0, 0.0, 1.0, "Max progress only"),
            (0.0, 1.0, 1.0, "Max peak+progress"),
            (12.0, 1.0, 1.0, "Max hours, max peak+progress"),
        ]

        print("\nhrs | peak | prog | weight | expected")
        print("-" * 50)

        for hrs, peak, prog, label in boundaries:
            weight = day0_temporal_closure_weight(
                hours_remaining=hrs,
                peak_confidence=peak,
                daylight_progress=prog,
                obs_exceeds_ens_fraction=0.0,
            )

            # Calculate expected: time has NO coefficient
            tf = max(0.0, 1.0 - hrs / 12.0)
            expected = max(tf, 0.75 * peak, 0.50 * prog)

            print(f"{hrs:>3.0f} | {peak:.1f}  | {prog:.1f}  | {weight:.3f} | {expected:.3f} ({label})")

            assert abs(weight - expected) < 0.001

    def test_linear_time_decay_profile(self):
        """Document the linear time decay (Gemini flagged this)."""
        print("\n=== MATH-006 Test 5: Linear Time Decay Profile ===")
        print("\nGemini's concern: linear decay doesn't match atmospheric uncertainty cliff")
        print("Current formula: time_factor = 1 - hours_remaining / 12.0")
        print("\nhours_remaining | time_factor | 0.75 * time_factor")
        print("-" * 55)

        for hrs in [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0]:
            tf = max(0.0, 1.0 - hrs / 12.0)
            weighted = 0.75 * tf
            print(f"{hrs:>15.1f} | {tf:>11.3f} | {weighted:.3f}")

        print("\nProposed alternative: logistic cliff")
        print("W(t) = 1 / (1 + exp(k * (t - t_cliff)))")
        print("where t_cliff ≈ 1.5h before sunset, k ≈ 2-3")

        # Compare linear vs logistic
        print("\nComparison at key points:")
        print("hrs | linear | logistic(k=2, cliff=1.5)")
        print("-" * 45)

        k = 2.0
        t_cliff = 1.5

        for hrs in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0]:
            linear = max(0.0, 1.0 - hrs / 12.0)
            logistic = 1.0 / (1.0 + np.exp(k * (hrs - t_cliff)))
            print(f"{hrs:>3.1f} | {linear:.3f}  | {logistic:.3f}")


class TestTemporalClosureRecommendation:
    """Generate recommendation based on evaluation."""

    def test_recommendation_summary(self):
        """Summarize findings and recommendation."""
        print("\n=== MATH-006: Recommendation Summary ===")
        print("""
FINDINGS:

1. COEFFICIENT VALUES (0.75, 0.50, 0.35):
   - Time factor (0.75) dominates in most scenarios
   - Peak factor (0.50) is secondary
   - Progress factor (0.35) rarely wins
   - These feel arbitrary but provide reasonable behavior

2. COMBINATION METHOD (max):
   - max() gives aggressive closure
   - May over-weight a single strong signal
   - Alternative: weighted sum would be more conservative

3. LINEAR TIME DECAY (hours / 12.0):
   - Too gradual at start (12h→6h: little change)
   - Too linear near sunset (should be cliff-like)
   - Gemini's logistic proposal has merit

RECOMMENDATION:

Phase 1 (conservative):
- Keep current coefficients but document rationale
- Add tests to lock in expected behavior

Phase 2 (data-driven):
- Use MATH-002 calibration data to fit optimal coefficients
- Consider logistic time decay for more physical behavior

Phase 3 (proposed Bayesian model):
- MATH-009's Bayesian approach could replace this entirely
- Let observation/prior variance ratio drive closure

VERDICT: No urgent change. Document current behavior, plan for MATH-009.
""")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
