"""Tests for Day0Signal."""

import numpy as np
import pytest

from src.signal.day0_signal import Day0Signal
from src.types import Bin, SolarDay, DaylightPhase
from datetime import date, datetime, timezone, timedelta


BINS = [
    Bin(low=None, high=32, label="32 or below", unit="F"),
    Bin(low=33, high=34, label="33-34", unit="F"),
    Bin(low=35, high=36, label="35-36", unit="F"),
    Bin(low=37, high=38, label="37-38", unit="F"),
    Bin(low=39, high=40, label="39-40", unit="F"),
    Bin(low=41, high=42, label="41-42", unit="F"),
    Bin(low=43, high=44, label="43-44", unit="F"),
    Bin(low=45, high=46, label="45-46", unit="F"),
    Bin(low=47, high=48, label="47-48", unit="F"),
    Bin(low=49, high=50, label="49-50", unit="F"),
    Bin(low=51, high=None, label="51 or higher", unit="F"),
]


class TestDay0Signal:
    def test_obs_floor_shifts_distribution(self):
        """If observed high is 45, bins below 45 should have ~0 probability."""
        np.random.seed(42)
        remaining = np.random.default_rng(42).normal(40, 3, 51)
        sig = Day0Signal(observed_high_so_far=45.0, current_temp=42.0,
                         hours_remaining=3.0, member_maxes_remaining=remaining)
        p = sig.p_vector(BINS, n_mc=1000)

        assert p.shape == (11,)
        assert pytest.approx(p.sum(), abs=0.01) == 1.0
        # Bins below 45 should be near zero (obs floor at 45)
        assert p[0] + p[1] + p[2] + p[3] + p[4] < 0.05

    def test_obs_dominates_when_high(self):
        """If observed high exceeds most remaining forecasts → obs_dominates=True."""
        remaining = np.full(51, 35.0)  # All remaining forecast below obs
        sig = Day0Signal(observed_high_so_far=50.0, current_temp=48.0,
                         hours_remaining=2.0, member_maxes_remaining=remaining)
        assert sig.obs_dominates() is True

    def test_obs_not_dominant_when_low(self):
        """If remaining forecast mostly exceeds obs → obs_dominates=False."""
        remaining = np.full(51, 60.0)  # All remaining forecast above obs
        sig = Day0Signal(observed_high_so_far=40.0, current_temp=38.0,
                         hours_remaining=6.0, member_maxes_remaining=remaining)
        assert sig.obs_dominates() is False

    def test_expected_high(self):
        """Expected high should be >= observed high."""
        remaining = np.random.default_rng(42).normal(40, 3, 51)
        sig = Day0Signal(observed_high_so_far=45.0, current_temp=42.0,
                         hours_remaining=3.0, member_maxes_remaining=remaining)
        assert sig.expected_high() >= 45.0

    def test_sums_to_one(self):
        np.random.seed(42)
        remaining = np.random.default_rng(42).normal(42, 5, 51)
        sig = Day0Signal(observed_high_so_far=38.0, current_temp=36.0,
                         hours_remaining=5.0, member_maxes_remaining=remaining)
        p = sig.p_vector(BINS, n_mc=500)
        assert pytest.approx(p.sum(), abs=0.01) == 1.0

    def test_daylight_progress_caps_pre_sunrise_weight(self):
        sig = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=1.0,
            member_maxes_remaining=np.full(51, 40.0),
            daylight_progress=0.0,
        )
        assert sig.observation_weight() <= 0.05

    def test_daylight_progress_forces_post_sunset_finality(self):
        sig = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=8.0,
            member_maxes_remaining=np.full(51, 40.0),
            daylight_progress=1.0,
            observation_source="wu_api",
            observation_time="2026-04-02T00:00:00+00:00",
            current_utc_timestamp="2026-04-02T00:30:00+00:00",
        )
        assert sig.observation_weight() == pytest.approx(1.0)

    def test_solar_day_can_drive_daylight_progress(self):
        solar_day = SolarDay(
            city="NYC",
            target_date=date(2026, 4, 1),
            timezone="America/New_York",
            sunrise_local=datetime.fromisoformat("2026-04-01T06:30-04:00"),
            sunset_local=datetime.fromisoformat("2026-04-01T19:30-04:00"),
            sunrise_utc=datetime.fromisoformat("2026-04-01T10:30+00:00"),
            sunset_utc=datetime.fromisoformat("2026-04-01T23:30+00:00"),
            utc_offset_minutes=-240,
            dst_active=True,
        )
        sig = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=8.0,
            member_maxes_remaining=np.full(51, 40.0),
            solar_day=solar_day,
            current_local_hour=20.0,
            observation_source="wu_api",
            observation_time="2026-04-02T00:00:00+00:00",
            current_utc_timestamp="2026-04-02T00:30:00+00:00",
        )
        assert sig.observation_weight() == pytest.approx(1.0)

    def test_solar_day_phase_semantics(self):
        solar_day = SolarDay(
            city="NYC",
            target_date=date(2026, 4, 1),
            timezone="America/New_York",
            sunrise_local=datetime.fromisoformat("2026-04-01T06:30-04:00"),
            sunset_local=datetime.fromisoformat("2026-04-01T19:30-04:00"),
            sunrise_utc=datetime.fromisoformat("2026-04-01T10:30+00:00"),
            sunset_utc=datetime.fromisoformat("2026-04-01T23:30+00:00"),
            utc_offset_minutes=-240,
            dst_active=True,
        )
        assert solar_day.phase(5.0) == DaylightPhase.PRE_SUNRISE
        assert solar_day.phase(12.0) == DaylightPhase.DAYLIGHT
        assert solar_day.phase(20.0) == DaylightPhase.POST_SUNSET

    def test_post_sunset_day0_distribution_locks_to_observed_high(self):
        remaining = np.full(51, 50.0)
        sig = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=8.0,
            member_maxes_remaining=remaining,
            daylight_progress=1.0,
            observation_source="wu_api",
            observation_time="2026-04-02T00:00:00+00:00",
            current_utc_timestamp="2026-04-02T00:30:00+00:00",
        )
        p = sig.p_vector(BINS, n_mc=400)
        # Fully sunset-locked: all probability should collapse into the observed bin.
        assert p[3] + p[4] > 0.95  # 37-38 / 39-40 due to settlement/noise boundary

    def test_pre_sunrise_keeps_more_upside_than_post_sunset(self):
        remaining = np.full(51, 50.0)
        dawn = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=8.0,
            member_maxes_remaining=remaining,
            daylight_progress=0.0,
        )
        dusk = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=8.0,
            member_maxes_remaining=remaining,
            daylight_progress=1.0,
            observation_source="wu_api",
            observation_time="2026-04-02T00:00:00+00:00",
            current_utc_timestamp="2026-04-02T00:30:00+00:00",
        )
        p_dawn = dawn.p_vector(BINS, n_mc=400)
        p_dusk = dusk.p_vector(BINS, n_mc=400)
        # Late-day logic should suppress the hot upside tail much more aggressively.
        assert p_dawn[-1] > p_dusk[-1]

    def test_stale_post_sunset_observation_keeps_more_upside_than_fresh(self):
        remaining = np.full(51, 50.0)
        fresh = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=8.0,
            member_maxes_remaining=remaining,
            daylight_progress=1.0,
            observation_source="wu_api",
            observation_time="2026-04-02T00:00:00+00:00",
            current_utc_timestamp="2026-04-02T00:30:00+00:00",
        )
        stale = Day0Signal(
            observed_high_so_far=38.0,
            current_temp=36.0,
            hours_remaining=8.0,
            member_maxes_remaining=remaining,
            daylight_progress=1.0,
            observation_source="wu_api",
            observation_time="2026-04-02T00:00:00+00:00",
            current_utc_timestamp="2026-04-02T04:00:00+00:00",
        )
        p_fresh = fresh.p_vector(BINS, n_mc=400)
        p_stale = stale.p_vector(BINS, n_mc=400)
        assert p_stale[5] + p_stale[6] > p_fresh[5] + p_fresh[6]
        assert p_stale[3] < p_fresh[3]

    def test_untrusted_observation_source_does_not_activate_nowcast_blend(self):
        sig = Day0Signal(
            observed_high_so_far=45.0,
            current_temp=44.8,
            hours_remaining=1.0,
            member_maxes_remaining=np.full(51, 46.0),
            daylight_progress=0.5,
            observation_source="personal_weather_station",
            observation_time="2026-04-02T00:00:00+00:00",
            current_utc_timestamp="2026-04-02T00:30:00+00:00",
        )
        backbone = sig.forecast_context()["backbone"]
        assert backbone["nowcast"]["trusted_source"] is False
        assert backbone["nowcast"]["blend_weight"] == 0.0

    def test_ens_dominance_strengthens_observation_weight(self):
        dominated = Day0Signal(
            observed_high_so_far=50.0,
            current_temp=48.0,
            hours_remaining=10.0,
            member_maxes_remaining=np.full(51, 45.0),
            daylight_progress=0.1,
        )
        not_dominated = Day0Signal(
            observed_high_so_far=40.0,
            current_temp=38.0,
            hours_remaining=10.0,
            member_maxes_remaining=np.full(51, 50.0),
            daylight_progress=0.1,
        )
        assert dominated.observation_weight() > not_dominated.observation_weight()


# =============================================================================
# MATH-001: SUNSET SANITY VALIDATION
# Gemini external review requirement: At T-1min before sunset with fresh trusted
# observation, the probability distribution should be tightly concentrated around
# the observed high. These tests validate the current temporal_closure behavior.
# =============================================================================


class TestSunsetSanityValidation:
    """MATH-001: Sunset sanity checks per Gemini external review.
    
    These tests document the current Day0 distribution behavior at near-sunset
    conditions with various observation freshness and trust levels.
    """

    # Extended bins for better resolution around typical highs
    EXTENDED_BINS = [
        Bin(low=None, high=64, label="64 or below", unit="F"),
        Bin(low=65, high=66, label="65-66", unit="F"),
        Bin(low=67, high=68, label="67-68", unit="F"),
        Bin(low=69, high=70, label="69-70", unit="F"),
        Bin(low=71, high=72, label="71-72", unit="F"),
        Bin(low=73, high=74, label="73-74", unit="F"),
        Bin(low=75, high=76, label="75-76", unit="F"),
        Bin(low=77, high=78, label="77-78", unit="F"),
        Bin(low=79, high=80, label="79-80", unit="F"),
        Bin(low=81, high=None, label="81 or higher", unit="F"),
    ]

    def _find_bin_containing(self, value: float, bins: list[Bin]) -> int:
        """Find index of bin containing the given value."""
        for i, b in enumerate(bins):
            if b.is_open_low and value <= b.high:
                return i
            if b.is_open_high and value >= b.low:
                return i
            if not b.is_open_low and not b.is_open_high:
                if b.low <= value <= b.high:
                    return i
        return -1

    def _p_within_range(self, p: np.ndarray, bins: list[Bin], center: float, half_width: float) -> float:
        """Sum probability mass within ± half_width of center."""
        total = 0.0
        for i, b in enumerate(bins):
            # Check if bin overlaps with [center - half_width, center + half_width]
            bin_low = b.low if b.low is not None else float('-inf')
            bin_high = b.high if b.high is not None else float('inf')
            range_low = center - half_width
            range_high = center + half_width
            if bin_low <= range_high and bin_high >= range_low:
                total += p[i]
        return total

    def _distribution_effective_std(self, p: np.ndarray, bins: list[Bin]) -> float:
        """Estimate effective std from probability distribution over bins."""
        # Use bin midpoints for moment calculation
        midpoints = []
        for b in bins:
            if b.is_open_low:
                midpoints.append(b.high - 1.0)  # Approximate for open bins
            elif b.is_open_high:
                midpoints.append(b.low + 1.0)
            else:
                midpoints.append((b.low + b.high) / 2.0)
        midpoints = np.array(midpoints)
        mean = np.sum(p * midpoints)
        var = np.sum(p * (midpoints - mean) ** 2)
        return float(np.sqrt(var))

    def test_fresh_trusted_observation_at_sunset(self):
        """MATH-001 Test 1: Fresh trusted ASOS observation at T-1min before sunset.
        
        This is the core sunset sanity check. With a fresh trusted observation
        and daylight_progress=1.0 (post-sunset), the distribution should collapse
        tightly around the observed high.
        
        Acceptance: p_within_2F > 0.80
        """
        # Setup: NYC, observed high 72°F, T-1min before sunset
        # ENS members spread around the observed value
        rng = np.random.default_rng(42)
        ens_remaining = rng.normal(71.0, 2.0, 51)  # ENS spread around 71°F
        
        sig = Day0Signal(
            observed_high_so_far=72.0,
            current_temp=68.0,
            hours_remaining=0.0,  # Settlement closes now
            member_maxes_remaining=ens_remaining,
            unit="F",
            daylight_progress=1.0,  # Post-sunset
            observation_source="wu/asos",  # Trusted source
            observation_time="2026-04-03T23:29:00+00:00",  # 1 minute ago
            current_utc_timestamp="2026-04-03T23:30:00+00:00",  # Now
        )
        
        # Collect metrics
        obs_weight = sig.observation_weight()
        p = sig.p_vector(self.EXTENDED_BINS, n_mc=2000)
        
        p_within_2F = self._p_within_range(p, self.EXTENDED_BINS, 72.0, 2.0)
        p_within_4F = self._p_within_range(p, self.EXTENDED_BINS, 72.0, 4.0)
        effective_std = self._distribution_effective_std(p, self.EXTENDED_BINS)
        obs_bin_idx = self._find_bin_containing(72.0, self.EXTENDED_BINS)
        p_max = float(p[obs_bin_idx]) if obs_bin_idx >= 0 else 0.0
        
        # Document results for math_progress.md
        print(f"\n=== MATH-001 Test 1: Fresh Trusted Observation at Sunset ===")
        print(f"observation_weight: {obs_weight:.4f}")
        print(f"p_max (bin containing 72°F): {p_max:.4f}")
        print(f"p_within_2F: {p_within_2F:.4f}")
        print(f"p_within_4F: {p_within_4F:.4f}")
        print(f"effective_std: {effective_std:.2f}°F")
        print(f"Distribution: {p}")
        
        # Assertions
        assert obs_weight > 0.95, f"Near-sunset fresh trusted obs should dominate: got {obs_weight:.4f}"
        assert p_within_2F > 0.80, f"Sunset spike too weak: p_within_2F = {p_within_2F:.4f}, expected > 0.80"

    def test_stale_observation_at_sunset(self):
        """MATH-001 Test 2: Stale (2-hour old) observation at sunset.
        
        Documents current behavior when observation is stale at sunset.
        Distribution should be wider than fresh case but still observation-biased.
        """
        rng = np.random.default_rng(42)
        ens_remaining = rng.normal(71.0, 2.0, 51)
        
        sig = Day0Signal(
            observed_high_so_far=72.0,
            current_temp=68.0,
            hours_remaining=0.0,
            member_maxes_remaining=ens_remaining,
            unit="F",
            daylight_progress=1.0,
            observation_source="wu/asos",
            observation_time="2026-04-03T21:30:00+00:00",  # 2 hours ago
            current_utc_timestamp="2026-04-03T23:30:00+00:00",
        )
        
        obs_weight = sig.observation_weight()
        p = sig.p_vector(self.EXTENDED_BINS, n_mc=2000)
        
        p_within_2F = self._p_within_range(p, self.EXTENDED_BINS, 72.0, 2.0)
        p_within_4F = self._p_within_range(p, self.EXTENDED_BINS, 72.0, 4.0)
        effective_std = self._distribution_effective_std(p, self.EXTENDED_BINS)
        
        print(f"\n=== MATH-001 Test 2: Stale Observation (2h) at Sunset ===")
        print(f"observation_weight: {obs_weight:.4f}")
        print(f"p_within_2F: {p_within_2F:.4f}")
        print(f"p_within_4F: {p_within_4F:.4f}")
        print(f"effective_std: {effective_std:.2f}°F")
        
        # Stale observation should still collapse reasonably at sunset
        # (daylight_progress=1.0 still forces finality)
        assert obs_weight > 0.80, f"Post-sunset should still have high obs_weight: got {obs_weight:.4f}"
        # Document: is distribution acceptably narrow?
        # This may indicate need for FEAT-P2H-009 (freshness threshold at sunset)

    def test_untrusted_observation_at_sunset(self):
        """MATH-001 Test 3: Untrusted (PWS) observation at sunset.
        
        Per FEAT-P2H-007, untrusted sources get source_factor=0.0.
        Distribution should be wider than trusted case.
        """
        rng = np.random.default_rng(42)
        ens_remaining = rng.normal(71.0, 2.0, 51)
        
        sig = Day0Signal(
            observed_high_so_far=72.0,
            current_temp=68.0,
            hours_remaining=0.0,
            member_maxes_remaining=ens_remaining,
            unit="F",
            daylight_progress=1.0,
            observation_source="pws",  # Untrusted
            observation_time="2026-04-03T23:29:00+00:00",
            current_utc_timestamp="2026-04-03T23:30:00+00:00",
        )
        
        obs_weight = sig.observation_weight()
        p = sig.p_vector(self.EXTENDED_BINS, n_mc=2000)
        
        p_within_2F = self._p_within_range(p, self.EXTENDED_BINS, 72.0, 2.0)
        effective_std = self._distribution_effective_std(p, self.EXTENDED_BINS)
        
        print(f"\n=== MATH-001 Test 3: Untrusted Observation at Sunset ===")
        print(f"observation_weight: {obs_weight:.4f}")
        print(f"p_within_2F: {p_within_2F:.4f}")
        print(f"effective_std: {effective_std:.2f}°F")
        
        # Untrusted source still gets post-sunset finality via daylight_progress
        # but nowcast blend_weight should be 0.0
        backbone = sig.forecast_context()["backbone"]
        assert backbone["nowcast"]["trusted_source"] is False
        assert backbone["nowcast"]["blend_weight"] == 0.0

    def test_fresh_vs_stale_comparison(self):
        """Compare fresh vs stale observation distribution widths at sunset."""
        rng = np.random.default_rng(42)
        ens_remaining = rng.normal(71.0, 2.0, 51)
        
        fresh = Day0Signal(
            observed_high_so_far=72.0,
            current_temp=68.0,
            hours_remaining=0.0,
            member_maxes_remaining=ens_remaining,
            unit="F",
            daylight_progress=1.0,
            observation_source="wu/asos",
            observation_time="2026-04-03T23:29:00+00:00",
            current_utc_timestamp="2026-04-03T23:30:00+00:00",
        )
        
        stale = Day0Signal(
            observed_high_so_far=72.0,
            current_temp=68.0,
            hours_remaining=0.0,
            member_maxes_remaining=ens_remaining,
            unit="F",
            daylight_progress=1.0,
            observation_source="wu/asos",
            observation_time="2026-04-03T21:30:00+00:00",
            current_utc_timestamp="2026-04-03T23:30:00+00:00",
        )
        
        p_fresh = fresh.p_vector(self.EXTENDED_BINS, n_mc=2000)
        p_stale = stale.p_vector(self.EXTENDED_BINS, n_mc=2000)
        
        std_fresh = self._distribution_effective_std(p_fresh, self.EXTENDED_BINS)
        std_stale = self._distribution_effective_std(p_stale, self.EXTENDED_BINS)
        
        print(f"\n=== MATH-001 Test 4: Fresh vs Stale Comparison ===")
        print(f"fresh effective_std: {std_fresh:.2f}°F")
        print(f"stale effective_std: {std_stale:.2f}°F")
        print(f"difference: {std_stale - std_fresh:.2f}°F")
        
        # Fresh should be tighter (lower std) or equal
        # If stale is actually tighter, that indicates a problem
        # (Currently, both may be very tight due to daylight_progress=1.0 override)

    def test_multi_city_sunset_sanity(self):
        """MATH-001 Test 4: Multi-city sunset sanity check.
        
        Verify sunset sanity holds across different configurations.
        """
        cities = [
            {"name": "NYC", "obs_high": 72.0, "ens_mean": 71.0, "ens_std": 2.0},
            {"name": "London", "obs_high": 18.0, "ens_mean": 17.5, "ens_std": 1.0, "unit": "C"},
            {"name": "Tokyo", "obs_high": 25.0, "ens_mean": 24.5, "ens_std": 1.5, "unit": "C"},
        ]
        
        results = []
        rng = np.random.default_rng(42)
        
        for city in cities:
            unit = city.get("unit", "F")
            ens_remaining = rng.normal(city["ens_mean"], city["ens_std"], 51)
            
            sig = Day0Signal(
                observed_high_so_far=city["obs_high"],
                current_temp=city["obs_high"] - 4.0,
                hours_remaining=0.0,
                member_maxes_remaining=ens_remaining,
                unit=unit,
                daylight_progress=1.0,
                observation_source="wu/asos",
                observation_time="2026-04-03T23:29:00+00:00",
                current_utc_timestamp="2026-04-03T23:30:00+00:00",
            )
            
            # Use appropriate bins for unit
            # Celsius bins must be exactly 1 degree wide (per market.py invariant)
            if unit == "C":
                bins_c = [
                    Bin(low=None, high=15, label="15 or below", unit="C"),
                    Bin(low=16, high=16, label="16", unit="C"),
                    Bin(low=17, high=17, label="17", unit="C"),
                    Bin(low=18, high=18, label="18", unit="C"),
                    Bin(low=19, high=19, label="19", unit="C"),
                    Bin(low=20, high=20, label="20", unit="C"),
                    Bin(low=21, high=21, label="21", unit="C"),
                    Bin(low=22, high=22, label="22", unit="C"),
                    Bin(low=23, high=23, label="23", unit="C"),
                    Bin(low=24, high=24, label="24", unit="C"),
                    Bin(low=25, high=25, label="25", unit="C"),
                    Bin(low=26, high=None, label="26 or higher", unit="C"),
                ]
                bins = bins_c
            else:
                bins = self.EXTENDED_BINS
            
            obs_weight = sig.observation_weight()
            p = sig.p_vector(bins, n_mc=2000)
            
            # Calculate metrics in the appropriate unit range
            obs_value = city["obs_high"]
            half_width = 2.0 if unit == "F" else 1.0
            p_within_range = self._p_within_range(p, bins, obs_value, half_width)
            effective_std = self._distribution_effective_std(p, bins)
            
            results.append({
                "city": city["name"],
                "unit": unit,
                "obs_weight": obs_weight,
                "p_within_range": p_within_range,
                "effective_std": effective_std,
            })
        
        print(f"\n=== MATH-001 Test: Multi-City Sunset Sanity ===")
        for r in results:
            print(f"{r['city']} ({r['unit']}): obs_weight={r['obs_weight']:.4f}, "
                  f"p_within_range={r['p_within_range']:.4f}, std={r['effective_std']:.2f}")
        
        # All cities should have high observation weight at sunset
        for r in results:
            assert r["obs_weight"] > 0.90, f"{r['city']} sunset obs_weight too low: {r['obs_weight']:.4f}"

    def test_near_sunset_progression(self):
        """MATH-001 Test: Distribution progression as daylight_progress approaches 1.0.
        
        Tests the transition from mid-afternoon to sunset to verify gradual
        distribution narrowing rather than sudden cliff at sunset.
        """
        rng = np.random.default_rng(42)
        ens_remaining = rng.normal(71.0, 2.0, 51)
        
        progress_values = [0.5, 0.7, 0.85, 0.95, 0.99, 1.0]
        results = []
        
        for progress in progress_values:
            sig = Day0Signal(
                observed_high_so_far=72.0,
                current_temp=68.0,
                hours_remaining=12.0 * (1.0 - progress),  # Correlate with daylight progress
                member_maxes_remaining=ens_remaining,
                unit="F",
                daylight_progress=progress,
                observation_source="wu/asos",
                observation_time="2026-04-03T23:29:00+00:00",
                current_utc_timestamp="2026-04-03T23:30:00+00:00",
            )
            
            obs_weight = sig.observation_weight()
            p = sig.p_vector(self.EXTENDED_BINS, n_mc=1000)
            p_within_2F = self._p_within_range(p, self.EXTENDED_BINS, 72.0, 2.0)
            effective_std = self._distribution_effective_std(p, self.EXTENDED_BINS)
            
            results.append({
                "progress": progress,
                "hours_remaining": 12.0 * (1.0 - progress),
                "obs_weight": obs_weight,
                "p_within_2F": p_within_2F,
                "effective_std": effective_std,
            })
        
        print(f"\n=== MATH-001: Daylight Progress Transition ===")
        for r in results:
            print(f"progress={r['progress']:.2f} (h_rem={r['hours_remaining']:.1f}h): "
                  f"obs_weight={r['obs_weight']:.4f}, p_within_2F={r['p_within_2F']:.4f}, "
                  f"std={r['effective_std']:.2f}°F")
        
        # Verify monotonic increase in obs_weight as we approach sunset
        for i in range(1, len(results)):
            assert results[i]["obs_weight"] >= results[i-1]["obs_weight"] - 0.01, \
                f"obs_weight should not decrease significantly as sunset approaches"
        
        # At progress=1.0 (post-sunset), should be fully locked
        assert results[-1]["obs_weight"] == pytest.approx(1.0, abs=0.01)
        assert results[-1]["p_within_2F"] > 0.95

    def test_hours_remaining_impact_on_distribution(self):
        """MATH-001: Test distribution width vs hours_remaining at various daylight phases.
        
        This tests the time_closure signal independent of daylight_progress.
        """
        rng = np.random.default_rng(42)
        ens_remaining = rng.normal(75.0, 3.0, 51)  # ENS higher than obs
        
        hours_values = [12.0, 6.0, 3.0, 1.0, 0.5, 0.0]
        results = []
        
        for hours in hours_values:
            sig = Day0Signal(
                observed_high_so_far=72.0,
                current_temp=70.0,
                hours_remaining=hours,
                member_maxes_remaining=ens_remaining,
                unit="F",
                daylight_progress=0.5,  # Mid-day, no sunset override
                observation_source="wu/asos",
                observation_time="2026-04-03T12:00:00+00:00",
                current_utc_timestamp="2026-04-03T12:01:00+00:00",
            )
            
            obs_weight = sig.observation_weight()
            p = sig.p_vector(self.EXTENDED_BINS, n_mc=1000)
            effective_std = self._distribution_effective_std(p, self.EXTENDED_BINS)
            
            results.append({
                "hours": hours,
                "obs_weight": obs_weight,
                "effective_std": effective_std,
            })
        
        print(f"\n=== MATH-001: Hours Remaining Impact (daylight_progress=0.5) ===")
        for r in results:
            print(f"hours_remaining={r['hours']:.1f}: obs_weight={r['obs_weight']:.4f}, "
                  f"std={r['effective_std']:.2f}°F")
        
        # With more hours remaining and ENS higher than obs, should have wider distribution
        # As hours decrease, observation should dominate more
        for i in range(1, len(results)):
            assert results[i]["obs_weight"] >= results[i-1]["obs_weight"] - 0.01, \
                f"obs_weight should increase or stay stable as hours decrease"
