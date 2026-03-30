"""Tests for Kelly sizing and risk limits."""

import pytest

from src.strategy.kelly import kelly_size, dynamic_kelly_mult
from src.strategy.risk_limits import RiskLimits, check_position_allowed


class TestKellySize:
    def test_positive_edge(self):
        """p_posterior > entry → positive size."""
        size = kelly_size(0.60, 0.40, 100.0, kelly_mult=0.25)
        assert size > 0

    def test_no_edge(self):
        """p_posterior <= entry → zero."""
        assert kelly_size(0.40, 0.50, 100.0) == 0.0
        assert kelly_size(0.50, 0.50, 100.0) == 0.0

    def test_formula_correctness(self):
        """f* = (0.6 - 0.4) / (1 - 0.4) = 0.333. Size = 0.333 × 0.25 × 100 = 8.33"""
        size = kelly_size(0.60, 0.40, 100.0, kelly_mult=0.25)
        expected = (0.60 - 0.40) / (1.0 - 0.40) * 0.25 * 100.0
        assert size == pytest.approx(expected)

    def test_entry_at_one(self):
        """entry_price = 1.0 → no trade (division by zero guard)."""
        assert kelly_size(0.99, 1.0, 100.0) == 0.0

    def test_small_edge_small_size(self):
        """Small edge → small position."""
        size = kelly_size(0.11, 0.10, 100.0, kelly_mult=0.25)
        assert 0 < size < 5  # Small size for small edge


class TestDynamicKellyMult:
    def test_base_unchanged(self):
        """Default params → returns base."""
        m = dynamic_kelly_mult(base=0.25)
        assert m == 0.25

    def test_wide_ci_reduces(self):
        """ci_width > 0.15 → aggressive reduction."""
        m = dynamic_kelly_mult(base=0.25, ci_width=0.20)
        assert m < 0.25 * 0.7 * 0.5 + 0.01

    def test_long_lead_reduces(self):
        m_short = dynamic_kelly_mult(base=0.25, lead_days=1.0)
        m_long = dynamic_kelly_mult(base=0.25, lead_days=6.0)
        assert m_long < m_short

    def test_losing_streak_reduces(self):
        m_winning = dynamic_kelly_mult(base=0.25, rolling_win_rate_20=0.60)
        m_losing = dynamic_kelly_mult(base=0.25, rolling_win_rate_20=0.35)
        assert m_losing < m_winning

    def test_drawdown_reduces(self):
        m = dynamic_kelly_mult(base=0.25, drawdown_pct=0.10, max_drawdown=0.20)
        assert m == pytest.approx(0.25 * 0.5)

    def test_full_drawdown_zeros(self):
        m = dynamic_kelly_mult(base=0.25, drawdown_pct=0.20, max_drawdown=0.20)
        assert m == 0.0


class TestRiskLimits:
    def test_allowed(self):
        ok, reason = check_position_allowed(
            size_usd=5.0, bankroll=100.0,
            city="NYC", cluster="US-Northeast",
            current_city_exposure=0.0, current_cluster_exposure=0.0,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is True

    def test_below_minimum(self):
        ok, reason = check_position_allowed(
            size_usd=0.50, bankroll=100.0,
            city="NYC", cluster="US-Northeast",
            current_city_exposure=0.0, current_cluster_exposure=0.0,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is False
        assert "minimum" in reason

    def test_exceeds_single_position(self):
        ok, reason = check_position_allowed(
            size_usd=15.0, bankroll=100.0,
            city="NYC", cluster="US-Northeast",
            current_city_exposure=0.0, current_cluster_exposure=0.0,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is False
        assert "single position" in reason

    def test_exceeds_portfolio_heat(self):
        ok, reason = check_position_allowed(
            size_usd=5.0, bankroll=100.0,
            city="NYC", cluster="US-Northeast",
            current_city_exposure=0.0, current_cluster_exposure=0.0,
            current_portfolio_heat=0.48, limits=RiskLimits(),
        )
        assert ok is False
        assert "heat" in reason

    def test_exceeds_city_limit(self):
        ok, reason = check_position_allowed(
            size_usd=5.0, bankroll=100.0,
            city="NYC", cluster="US-Northeast",
            current_city_exposure=0.18, current_cluster_exposure=0.0,
            current_portfolio_heat=0.0, limits=RiskLimits(),
        )
        assert ok is False
        assert "City" in reason
