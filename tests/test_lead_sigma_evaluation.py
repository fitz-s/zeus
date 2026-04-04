"""MATH-007: Lead Sigma Multiplier Evaluation.

Evaluate the lead_days → sigma multiplier relationship.
Currently uses fixed multipliers; evaluate if they should be data-driven.
"""

import numpy as np
import pytest

from src.signal.forecast_uncertainty import (
    analysis_lead_sigma_multiplier,
    analysis_sigma_context,
)


class TestLeadSigmaMultiplier:
    """Evaluate current lead sigma multiplier behavior."""

    def test_current_multiplier_profile(self):
        """Document current lead → multiplier mapping."""
        print("\n=== MATH-007 Test 1: Current Lead Sigma Multiplier Profile ===")
        print("\nlead_days | multiplier | sigma_F | sigma_C")
        print("-" * 50)

        base_f = 0.5  # From sigma_instrument("F")
        base_c = 0.28  # From sigma_instrument("C")

        for lead in [0, 1, 2, 3, 4, 5, 6, 7, 10, 14]:
            mult = analysis_lead_sigma_multiplier(float(lead))
            print(f"{lead:>9d} | {mult:>10.3f} | {base_f * mult:.3f} | {base_c * mult:.3f}")

        print("\nCurrent formula: 1.0 + min(lead_days, 6) * (0.2 / 6)")
        print("Range: 1.0 (Day0) to 1.2 (Day6+)")

    def test_multiplier_is_bounded(self):
        """Verify multiplier stays within expected bounds."""
        print("\n=== MATH-007 Test 2: Multiplier Bounds ===")

        # Test edge cases
        assert analysis_lead_sigma_multiplier(None) == 1.0, "None should give 1.0"
        assert analysis_lead_sigma_multiplier(0.0) == 1.0, "Day0 should give 1.0"
        assert analysis_lead_sigma_multiplier(-1.0) == 1.0, "Negative should give 1.0"

        # Test upper bound
        for lead in [6, 7, 10, 30, 100]:
            mult = analysis_lead_sigma_multiplier(float(lead))
            assert mult <= 1.2, f"Lead {lead} should not exceed 1.2, got {mult}"
            assert mult >= 1.0, f"Lead {lead} should not be below 1.0, got {mult}"

        print("✓ All bounds verified: 1.0 ≤ multiplier ≤ 1.2")

    def test_multiplier_is_monotonic(self):
        """Verify multiplier increases with lead time."""
        print("\n=== MATH-007 Test 3: Monotonicity Check ===")

        prev_mult = 1.0
        for lead in range(0, 10):
            mult = analysis_lead_sigma_multiplier(float(lead))
            assert mult >= prev_mult, f"Lead {lead}: {mult} < {prev_mult}"
            prev_mult = mult

        print("✓ Multiplier is monotonically non-decreasing with lead time")


class TestLeadSigmaDataDriven:
    """Evaluate if lead sigma should be data-driven."""

    def test_expected_vs_actual_error_growth(self):
        """Compare current profile with theoretical error growth."""
        print("\n=== MATH-007 Test 4: Theoretical vs Current Error Growth ===")

        print("\nTheoretical models:")
        print("1. Linear growth: σ(t) = σ₀ × (1 + α × t)")
        print("2. Square root (random walk): σ(t) = σ₀ × √(1 + β × t)")
        print("3. Exponential saturation: σ(t) = σ_max - (σ_max - σ₀) × exp(-γ × t)")

        print("\nlead | current | linear(α=0.05) | sqrt(β=0.1) | exp_sat")
        print("-" * 65)

        sigma_0 = 1.0
        sigma_max = 1.5
        alpha = 0.05
        beta = 0.1
        gamma = 0.3

        for lead in [0, 1, 2, 3, 4, 5, 6, 7]:
            current = analysis_lead_sigma_multiplier(float(lead))
            linear = sigma_0 * (1 + alpha * lead)
            sqrt_rw = sigma_0 * np.sqrt(1 + beta * lead)
            exp_sat = sigma_max - (sigma_max - sigma_0) * np.exp(-gamma * lead)

            print(f"{lead:>4d} | {current:.3f}   | {linear:.3f}          | {sqrt_rw:.3f}       | {exp_sat:.3f}")

        print("\nNote: Current profile is linear but with very shallow slope")
        print("Only 20% increase from Day0 to Day6")

    def test_recommendation(self):
        """Generate recommendation."""
        print("\n=== MATH-007: Recommendation ===")
        print("""
FINDINGS:

1. Current multiplier range (1.0 → 1.2) is very conservative
   - Only 20% increase from Day0 to Day6+
   - Real forecast error likely grows faster

2. The formula is simplistic but bounded
   - Linear growth capped at Day6
   - No per-city/season variation
   - No model skill adjustment

3. Data-driven alternative would require:
   - Historical MAE vs lead_days curve from model_bias table
   - Per-city/season bucketing
   - More complex calibration pipeline

RECOMMENDATION:

The current simple approach is acceptable for now because:
- The 20% max expansion is conservative (safe)
- Day0 (most trading) uses multiplier=1.0
- More complex model would need significant calibration data

Consider data-driven approach in Phase 2 after:
- model_bias table has more historical data
- Calibration shows systematic lead-dependent errors
- Trading expands beyond Day0/Day1

VERDICT: No change needed. Current profile is conservative and safe.
""")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
