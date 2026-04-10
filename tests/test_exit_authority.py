"""Tests for F1 exit authority consolidation.

Covers: mark_settled() facade in exit_lifecycle, feature flag gating.
"""

import pytest
from unittest.mock import patch

from src.execution.exit_lifecycle import mark_settled
from src.state.portfolio import (
    Position,
    PortfolioState,
    compute_settlement_close,
)


def _make_position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1", market_id="m1", city="NYC",
        cluster="US-Northeast", target_date="2026-01-15",
        bin_label="39-40", direction="buy_yes",
        size_usd=10.0, entry_price=0.40, p_posterior=0.60,
        edge=0.20, entered_at="2026-01-12T00:00:00Z",
    )
    defaults.update(kwargs)
    return Position(**defaults)


class TestMarkSettled:

    def test_mark_settled_closes_position(self):
        """mark_settled delegates to compute_settlement_close and returns closed position."""
        pos = _make_position()
        portfolio = PortfolioState(positions=[pos])
        closed = mark_settled(portfolio, "t1", 1.0)
        assert closed is not None
        assert closed.trade_id == "t1"
        assert closed.state == "settled"
        assert closed.exit_reason == "SETTLEMENT"
        assert len(portfolio.positions) == 0

    def test_mark_settled_missing_trade_id(self):
        """mark_settled returns None for unknown trade_id."""
        pos = _make_position()
        portfolio = PortfolioState(positions=[pos])
        closed = mark_settled(portfolio, "nonexistent", 1.0)
        assert closed is None
        assert len(portfolio.positions) == 1

    def test_mark_settled_parity_with_direct_call(self):
        """mark_settled produces identical result to direct compute_settlement_close."""
        pos_a = _make_position(trade_id="a")
        pos_b = _make_position(trade_id="b")
        portfolio_a = PortfolioState(positions=[pos_a])
        portfolio_b = PortfolioState(positions=[pos_b])

        closed_direct = compute_settlement_close(portfolio_a, "a", 1.0, "SETTLEMENT")
        closed_facade = mark_settled(portfolio_b, "b", 1.0, "SETTLEMENT")

        assert closed_direct is not None
        assert closed_facade is not None
        assert closed_direct.state == closed_facade.state
        assert closed_direct.exit_reason == closed_facade.exit_reason
        assert closed_direct.exit_price == closed_facade.exit_price
        assert closed_direct.pnl == closed_facade.pnl

    def test_mark_settled_preserves_economic_close_price(self):
        """Already economically closed positions keep their exit_price at settlement."""
        pos = _make_position()
        pos.state = "economically_closed"
        pos.exit_price = 0.75
        portfolio = PortfolioState(positions=[pos])
        closed = mark_settled(portfolio, "t1", 1.0)
        assert closed is not None
        assert closed.state == "settled"
        # Economic close price is preserved, NOT overwritten by settlement_price
        assert closed.exit_price == 0.75

    def test_mark_settled_buy_no(self):
        """mark_settled works for buy_no positions."""
        pos = _make_position(direction="buy_no", entry_price=0.60)
        portfolio = PortfolioState(positions=[pos])
        closed = mark_settled(portfolio, "t1", 0.0, "SETTLEMENT")
        assert closed is not None
        assert closed.state == "settled"


class TestCanonicalExitFlag:

    def test_flag_defaults_to_false(self):
        """CANONICAL_EXIT_PATH defaults to False."""
        from src.execution.harvester import _get_canonical_exit_flag
        # The flag is false in config/settings.json
        result = _get_canonical_exit_flag()
        assert result is False

    def test_flag_returns_true_when_set(self):
        """CANONICAL_EXIT_PATH returns True when explicitly enabled."""
        from src.execution.harvester import _get_canonical_exit_flag
        from src.config import settings
        original = settings._data.get("feature_flags", {}).get("CANONICAL_EXIT_PATH")
        try:
            settings._data.setdefault("feature_flags", {})["CANONICAL_EXIT_PATH"] = True
            assert _get_canonical_exit_flag() is True
        finally:
            if original is None:
                settings._data.get("feature_flags", {}).pop("CANONICAL_EXIT_PATH", None)
            else:
                settings._data["feature_flags"]["CANONICAL_EXIT_PATH"] = original
