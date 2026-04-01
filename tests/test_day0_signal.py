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
        )
        p_dawn = dawn.p_vector(BINS, n_mc=400)
        p_dusk = dusk.p_vector(BINS, n_mc=400)
        # Late-day logic should suppress the hot upside tail much more aggressively.
        assert p_dawn[-1] > p_dusk[-1]

    def test_ens_dominance_strengthens_observation_weight(self):
        dominated = Day0Signal(
            observed_high_so_far=50.0,
            current_temp=48.0,
            hours_remaining=6.0,
            member_maxes_remaining=np.full(51, 45.0),
            daylight_progress=0.5,
        )
        not_dominated = Day0Signal(
            observed_high_so_far=40.0,
            current_temp=38.0,
            hours_remaining=6.0,
            member_maxes_remaining=np.full(51, 50.0),
            daylight_progress=0.5,
        )
        assert dominated.observation_weight() > not_dominated.observation_weight()
