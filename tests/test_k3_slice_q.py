"""Tests for K3 Slice Q: kelly_size validation guards + ExecutionPrice construction.

Bug #12 — kelly_size must return 0.0 for degenerate inputs.

P10E REWRITE: split into:
  (a) ExecutionPrice construction-validity tests (invalid field values raise ValueError)
  (b) kelly-level tests for remaining regressions (bankroll, p_posterior bounds/ordering)
"""
from __future__ import annotations

import math

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.strategy.kelly import kelly_size


def _ep(value: float) -> ExecutionPrice:
    """Build a valid Kelly-safe ExecutionPrice for test use."""
    return ExecutionPrice(
        value=value,
        price_type="fee_adjusted",
        fee_deducted=True,
        currency="probability_units",
    )


# ---------------------------------------------------------------------------
# (a) ExecutionPrice construction-validity tests
# ---------------------------------------------------------------------------

class TestExecutionPriceConstructionValidity:
    """P10E: construction guards migrated from kelly_size bare-float path."""

    def test_negative_value_raises(self):
        """value < 0 → ValueError at __post_init__."""
        with pytest.raises(ValueError):
            ExecutionPrice(value=-0.10, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")

    def test_value_above_one_probability_units_raises(self):
        """value > 1.0 in probability_units → ValueError at __post_init__."""
        with pytest.raises(ValueError):
            ExecutionPrice(value=1.01, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")

    def test_nan_value_raises(self):
        """NaN value → ValueError at __post_init__."""
        with pytest.raises(ValueError):
            ExecutionPrice(value=float("nan"), price_type="fee_adjusted", fee_deducted=True, currency="probability_units")

    def test_inf_value_raises(self):
        """Infinite value → ValueError at __post_init__."""
        with pytest.raises(ValueError):
            ExecutionPrice(value=math.inf, price_type="fee_adjusted", fee_deducted=True, currency="probability_units")


# ---------------------------------------------------------------------------
# (b) Kelly-level regression tests (bankroll / p_posterior guards)
# ---------------------------------------------------------------------------

class TestKellyValidationGuards:
    """Bug #12: kelly_size must reject degenerate inputs gracefully."""

    def test_bankroll_zero(self):
        """bankroll=0 → return 0."""
        assert kelly_size(0.60, _ep(0.40), 0.0) == 0.0

    def test_bankroll_negative(self):
        """bankroll < 0 → return 0."""
        assert kelly_size(0.60, _ep(0.40), -50.0) == 0.0

    def test_p_posterior_above_one(self):
        """p_posterior > 1.0 → return 0."""
        assert kelly_size(1.01, _ep(0.40), 100.0) == 0.0

    def test_p_posterior_below_zero(self):
        """p_posterior < 0 → return 0."""
        assert kelly_size(-0.01, _ep(0.40), 100.0) == 0.0

    def test_p_posterior_equals_entry(self):
        """p_posterior == entry → no edge → return 0."""
        assert kelly_size(0.50, _ep(0.50), 100.0) == 0.0

    def test_p_posterior_below_entry(self):
        """p_posterior < entry → negative edge → return 0."""
        assert kelly_size(0.40, _ep(0.50), 100.0) == 0.0

    def test_valid_inputs_still_work(self):
        """Normal inputs: p=0.60, entry=0.40, bankroll=100 → positive size."""
        size = kelly_size(0.60, _ep(0.40), 100.0, kelly_mult=0.25)
        expected = (0.60 - 0.40) / (1.0 - 0.40) * 0.25 * 100.0
        assert size == pytest.approx(expected)
