"""Tests for Day0Signal."""

import numpy as np
import pytest

from src.signal.day0_signal import Day0Signal
from src.types import Bin


BINS = [
    Bin(low=None, high=32, label="32 or below"),
    Bin(low=33, high=34, label="33-34"),
    Bin(low=35, high=36, label="35-36"),
    Bin(low=37, high=38, label="37-38"),
    Bin(low=39, high=40, label="39-40"),
    Bin(low=41, high=42, label="41-42"),
    Bin(low=43, high=44, label="43-44"),
    Bin(low=45, high=46, label="45-46"),
    Bin(low=47, high=48, label="47-48"),
    Bin(low=49, high=50, label="49-50"),
    Bin(low=51, high=None, label="51 or higher"),
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
