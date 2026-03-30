"""Tests for calibration drift detection."""

from datetime import date

import numpy as np
import pytest

from src.calibration.drift import (
    hosmer_lemeshow, directional_failure_check, is_seasonal_boundary,
    HL_THRESHOLD,
)


class TestHosmerLemeshow:
    def test_perfect_calibration(self):
        """Perfect predictions → low χ², not drifted."""
        rng = np.random.default_rng(42)
        p = rng.uniform(0.1, 0.9, 100)
        outcomes = (rng.random(100) < p).astype(int).tolist()
        chi2, drifted = hosmer_lemeshow(p.tolist(), outcomes)
        assert not drifted

    def test_severe_miscalibration(self):
        """All predictions wrong direction → high χ², drifted."""
        p = [0.9] * 50  # Predict high
        outcomes = [0] * 50  # All negative
        chi2, drifted = hosmer_lemeshow(p, outcomes)
        assert drifted
        assert chi2 > HL_THRESHOLD

    def test_insufficient_data(self):
        """Too few points → returns 0, not drifted."""
        chi2, drifted = hosmer_lemeshow([0.5, 0.6], [1, 0])
        assert chi2 == 0.0
        assert not drifted


class TestDirectionalFailure:
    def test_no_failure(self):
        p = [0.8] * 20
        outcomes = [1] * 20  # All correct
        assert directional_failure_check(p, outcomes) is False

    def test_emergency(self):
        """8/20 directional misses → emergency."""
        p = [0.8] * 12 + [0.8] * 8  # 8 will be wrong
        outcomes = [1] * 12 + [0] * 8
        assert directional_failure_check(p, outcomes) is True

    def test_insufficient_data(self):
        assert directional_failure_check([0.5] * 10, [1] * 10) is False


class TestSeasonalBoundary:
    def test_equinox(self):
        assert is_seasonal_boundary(date(2026, 3, 20)) is True
        assert is_seasonal_boundary(date(2026, 9, 22)) is True

    def test_solstice(self):
        assert is_seasonal_boundary(date(2026, 6, 21)) is True
        assert is_seasonal_boundary(date(2026, 12, 21)) is True

    def test_normal_day(self):
        assert is_seasonal_boundary(date(2026, 3, 15)) is False
