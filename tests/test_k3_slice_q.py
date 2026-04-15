"""Tests for K3 Slice Q: kelly_size validation guards.

Bug #12 — kelly_size must return 0.0 for degenerate inputs.
"""
from __future__ import annotations

import pytest

from src.strategy.kelly import kelly_size


class TestKellyValidationGuards:
    """Bug #12: kelly_size must reject degenerate inputs gracefully."""

    def test_entry_price_zero(self):
        """entry_price=0 → return 0 (no edge, infinite f*)."""
        assert kelly_size(0.60, 0.0, 100.0) == 0.0

    def test_entry_price_negative(self):
        """entry_price < 0 → return 0."""
        assert kelly_size(0.60, -0.10, 100.0) == 0.0

    def test_bankroll_zero(self):
        """bankroll=0 → return 0."""
        assert kelly_size(0.60, 0.40, 0.0) == 0.0

    def test_bankroll_negative(self):
        """bankroll < 0 → return 0."""
        assert kelly_size(0.60, 0.40, -50.0) == 0.0

    def test_p_posterior_above_one(self):
        """p_posterior > 1.0 → return 0."""
        assert kelly_size(1.01, 0.40, 100.0) == 0.0

    def test_p_posterior_below_zero(self):
        """p_posterior < 0 → return 0."""
        assert kelly_size(-0.01, 0.40, 100.0) == 0.0

    def test_p_posterior_equals_entry(self):
        """p_posterior == entry → no edge → return 0."""
        assert kelly_size(0.50, 0.50, 100.0) == 0.0

    def test_p_posterior_below_entry(self):
        """p_posterior < entry → negative edge → return 0."""
        assert kelly_size(0.40, 0.50, 100.0) == 0.0

    def test_valid_inputs_still_work(self):
        """Normal inputs: p=0.60, entry=0.40, bankroll=100 → positive size."""
        size = kelly_size(0.60, 0.40, 100.0, kelly_mult=0.25)
        expected = (0.60 - 0.40) / (1.0 - 0.40) * 0.25 * 100.0
        assert size == pytest.approx(expected)
