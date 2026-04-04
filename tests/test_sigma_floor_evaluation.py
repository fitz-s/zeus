"""MATH-004: Sigma Floor Evaluation Tests.

Evaluate whether the quantization noise floor in day0_post_peak_sigma is appropriate.
These tests document behavior after the MATH-010 floor implementation.

The floor ensures sigma never drops below QUANTIZATION_NOISE_FLOOR (0.35°F / 0.20°C)
to absorb integer settlement rounding and sensor noise (Gamma risk mitigation).
"""

import numpy as np
import pytest
from datetime import datetime, timezone, timedelta

from src.signal.day0_signal import Day0Signal
from src.signal.forecast_uncertainty import (
    day0_post_peak_sigma,
    QUANTIZATION_NOISE_FLOOR_F,
    QUANTIZATION_NOISE_FLOOR_C,
)
from src.signal.ensemble_signal import sigma_instrument


class TestSigmaFloorBehavior:
    """Test 1: Document current sigma floor behavior."""

    def test_sigma_floor_at_various_peak_confidence(self):
        """Document sigma values at different peak confidence levels."""
        print("\n=== MATH-004 Test 1: Sigma Floor Behavior (with quantization floor) ===")
        print(f"\nUnit: F, base_sigma = {sigma_instrument('F').value}")
        print(f"Quantization floor: {QUANTIZATION_NOISE_FLOOR_F}°F")
        print("\npeak_conf | sigma | % of base | floor effect")
        print("-" * 50)

        base = sigma_instrument("F").value
        results = []

        for peak in [0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0]:
            sigma = day0_post_peak_sigma("F", peak, freshness_factor=1.0)
            pct = sigma / base * 100
            # Raw sigma without floor
            raw_sigma = base * (1 - peak * 0.5)
            floor_active = sigma > raw_sigma * 1.01  # 1% tolerance

            results.append({
                "peak": peak,
                "sigma": sigma,
                "pct": pct,
                "floor_active": floor_active,
            })

            print(f"{peak:>9.1f} | {sigma:.3f} | {pct:>8.1f}% | {'YES' if floor_active else 'no'}")

        # At peak=1.0, sigma should be at floor (0.35) since raw would be 0.25
        assert abs(results[-1]["sigma"] - QUANTIZATION_NOISE_FLOOR_F) < 0.001
        # At peak=0.0, sigma should be 100% of base (above floor)
        assert abs(results[0]["sigma"] - base) < 0.001

        print("\nCurrent formula: sigma = base * (1 - peak * 0.5)")
        print("This means at peak=1.0, sigma = 50% of base (floor)")

    def test_sigma_floor_comparison_with_alternatives(self):
        """Compare current 50% floor with alternative floors."""
        print("\n=== MATH-004 Test 1b: Alternative Floor Comparison ===")

        base = sigma_instrument("F").value

        # Define alternative formulas
        def current_formula(peak):
            return base * (1.0 - peak * 0.5)

        def floor_40(peak):
            return base * (1.0 - peak * 0.6)

        def floor_30(peak):
            return base * (1.0 - peak * 0.7)

        def floor_20(peak):
            return base * (1.0 - peak * 0.8)

        def no_floor(peak):
            return base * (1.0 - peak * 0.95)  # 5% minimum

        print("\npeak | current(50%) | 40% floor | 30% floor | 20% floor | 5% floor")
        print("-" * 75)

        for peak in [0.0, 0.5, 0.8, 0.9, 1.0]:
            c = current_formula(peak)
            f40 = floor_40(peak)
            f30 = floor_30(peak)
            f20 = floor_20(peak)
            nf = no_floor(peak)
            print(f"{peak:>4.1f} | {c:>12.3f} | {f40:>9.3f} | {f30:>9.3f} | {f20:>9.3f} | {nf:>8.3f}")

        print("\nBase sigma (F):", base)


class TestSigmaFloorCalibration:
    """Test 2: Check calibration implications of sigma floor."""

    def test_high_confidence_distribution_width(self):
        """Measure actual distribution width at high peak confidence."""
        print("\n=== MATH-004 Test 2: Distribution Width at High Confidence ===")

        now = datetime.now(timezone.utc)
        # Members spread across multiple bins so we can see distribution width
        members = np.array([68.0 + i * 0.3 for i in range(51)])  # 68-83°F range

        print("\nScenario: obs_high=70, members spread 68-83°F, hours_remaining=4.0")
        print("\npeak_conf | sigma | dist_std | p_max | dominant_bin")
        print("-" * 60)

        from src.types import Bin
        bins = [Bin(lo, lo + 1, "F") for lo in range(60, 86, 2)]

        results = []
        for peak in [0.0, 0.5, 0.8, 0.9, 1.0]:
            sig = Day0Signal(
                observed_high_so_far=70.0,  # Lower than many members
                current_temp=68.0,
                hours_remaining=4.0,  # More time for ENS to matter
                member_maxes_remaining=members,
                unit="F",
                diurnal_peak_confidence=peak,
                observation_source="wu",
                observation_time=now.isoformat(),
                current_utc_timestamp=now.isoformat(),
                daylight_progress=0.5,  # Mid-day
            )

            p_vec = sig.p_vector(bins, n_mc=10000)

            # Calculate effective std from distribution
            centers = np.array([(b.low + b.high) / 2 for b in bins])
            mean = np.sum(p_vec * centers)
            var = np.sum(p_vec * (centers - mean) ** 2)
            dist_std = np.sqrt(var)

            p_max = np.max(p_vec)
            dom_bin_idx = np.argmax(p_vec)
            dom_bin = f"{bins[dom_bin_idx].low}-{bins[dom_bin_idx].high}"

            results.append({
                "peak": peak,
                "sigma": sig._sigma,
                "dist_std": dist_std,
                "p_max": p_max,
            })

            print(f"{peak:>9.1f} | {sig._sigma:.3f} | {dist_std:>8.2f} | {p_max:.3f} | {dom_bin}")

        # Distribution should narrow with increasing confidence
        # (or stay similar if ENS variance dominates)
        print("\nNote: If dist_std doesn't change much, ENS variance dominates over sigma")
        print("This is expected when hours_remaining > 0 and obs_high < ENS forecasts")

    def test_sunset_collapse_with_floor(self):
        """Test if floor prevents proper sunset collapse."""
        print("\n=== MATH-004 Test 2b: Sunset Collapse Behavior ===")

        now = datetime.now(timezone.utc)
        # All members very close to observed high
        members = np.full(51, 72.0)

        print("\nAt sunset (daylight_progress=1.0, hours_remaining=0.5):")
        print("peak_conf | sigma | obs_weight | p_at_obs_bin")
        print("-" * 55)

        from src.types import Bin
        # F bins are 2 degrees wide (lo to lo+1 inclusive)
        bins = [Bin(lo, lo + 1, "F") for lo in range(60, 82, 2)]
        obs_bin_idx = 6  # 72-73 bin

        for peak in [0.5, 0.8, 0.9, 1.0]:
            sig = Day0Signal(
                observed_high_so_far=72.0,
                current_temp=72.0,
                hours_remaining=0.5,
                member_maxes_remaining=members,
                unit="F",
                diurnal_peak_confidence=peak,
                daylight_progress=1.0,  # sunset
                observation_source="wu",
                observation_time=now.isoformat(),
                current_utc_timestamp=now.isoformat(),
            )

            p_vec = sig.p_vector(bins, n_mc=10000)
            obs_weight = sig.observation_weight()

            print(f"{peak:>9.1f} | {sig._sigma:.3f} | {obs_weight:>10.2f} | {p_vec[obs_bin_idx]:>12.3f}")

        print("\nNote: At sunset with trusted fresh observation, obs_weight should be 1.0")
        print("and probability should collapse to observed bin")


class TestSigmaFloorRecommendation:
    """Test 3: Generate recommendation based on evidence."""

    def test_floor_impact_summary(self):
        """Summarize floor impact for recommendation."""
        print("\n=== MATH-004 Test 3: Floor Impact Summary ===")

        base_f = sigma_instrument("F").value
        base_c = sigma_instrument("C").value

        print(f"\nBase sigma: F={base_f}°F, C={base_c}°C")
        print("\nCurrent floor effect (at peak=1.0):")
        print(f"  F: sigma = {base_f * 0.5:.3f}°F (50% of base)")
        print(f"  C: sigma = {base_c * 0.5:.3f}°C (50% of base)")

        print("\nKey observations from MATH-001/002/003:")
        print("  1. MATH-001: Distribution collapses properly at sunset (obs_weight=1.0)")
        print("  2. MATH-002: High-confidence predictions have 97.8% hit rate")
        print("  3. MATH-003: Staleness now expands sigma (MATH-005 fix)")

        print("\n" + "=" * 60)
        print("PRELIMINARY RECOMMENDATION:")
        print("=" * 60)
        print("""
The 50% floor appears REASONABLE given:
- Calibration is good in trading-relevant high-confidence region
- The floor provides safety margin for model uncertainty
- At sunset, obs_weight=1.0 dominates anyway (distribution collapses)

However, consider:
- The floor prevents sigma from shrinking below 0.25°F (F) / 0.14°C (C)
- This may be overly conservative when all signals agree
- The proposed Bayesian model (MATH-009) could replace the floor
  with a principled approach

VERDICT: No urgent change needed. Defer to MATH-009 for principled solution.
""")


class TestSigmaFloorEdgeCases:
    """Test 4: Edge cases and boundary conditions."""

    def test_floor_with_staleness_expansion(self):
        """Test interaction between floor and MATH-005 staleness expansion."""
        print("\n=== MATH-004 Test 4: Floor + Staleness Interaction ===")

        base = sigma_instrument("F").value

        print("\nInteraction matrix (sigma values):")
        print("peak_conf \\ freshness | 1.0 (fresh) | 0.5 (1.5h) | 0.0 (3h+)")
        print("-" * 65)

        for peak in [0.0, 0.5, 0.8, 1.0]:
            row = f"{peak:>20.1f} |"
            for fresh in [1.0, 0.5, 0.0]:
                sigma = day0_post_peak_sigma("F", peak, freshness_factor=fresh)
                row += f" {sigma:>11.3f} |"
            print(row)

        print(f"\nBase sigma: {base}°F")
        print(f"Quantization floor: {QUANTIZATION_NOISE_FLOOR_F}°F")
        print("\nFormula: sigma = max(floor, base * (1 - peak * 0.5) * (1 + (1 - fresh) * 0.5))")

        # Verify the interaction
        max_sigma = day0_post_peak_sigma("F", 0.0, freshness_factor=0.0)
        min_sigma = day0_post_peak_sigma("F", 1.0, freshness_factor=1.0)
        assert max_sigma == base * 1.5  # 0 peak, 3h stale (above floor)
        assert min_sigma == QUANTIZATION_NOISE_FLOOR_F  # 1.0 peak, fresh → floor

    def test_floor_prevents_zero_sigma(self):
        """Verify floor prevents sigma from reaching zero."""
        print("\n=== MATH-004 Test 4b: Zero Sigma Prevention ===")

        # Even at extreme confidence, sigma should never be below floor
        for unit, floor in [("F", QUANTIZATION_NOISE_FLOOR_F), ("C", QUANTIZATION_NOISE_FLOOR_C)]:
            base = sigma_instrument(unit).value
            min_sigma = day0_post_peak_sigma(unit, 1.0, freshness_factor=1.0)

            print(f"{unit}: min_sigma = {min_sigma:.4f} (floor = {floor})")
            assert min_sigma > 0, f"Sigma should never be zero for {unit}"
            assert min_sigma >= floor, f"Sigma should be at least floor for {unit}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
