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


class TestHarvesterBoundaryErrors:
    """B043 + B045 relationship tests for src/execution/harvester.py.

    B043: canonical-exit flag must not mask TypeError/RuntimeError as
          "flag disabled" \u2014 only legitimate "settings unavailable"
          cases return False.
    B045: mid-pagination HTTPError must NOT produce a partial
          settled-events list that looks like the full set to
          downstream portfolio accounting.
    """

    def test_b043_canonical_exit_flag_propagates_unknown_failures(self, monkeypatch):
        """A RuntimeError while reading feature_flags is a code bug,
        NOT a legitimate \"flag disabled\" state. It must propagate.
        """
        from src.execution.harvester import _get_canonical_exit_flag
        from src.config import Settings

        def _boom(self):
            raise RuntimeError("feature_flags backend corrupt")

        # Patch the property on the class so settings.feature_flags raises.
        monkeypatch.setattr(Settings, "feature_flags", property(_boom))
        with pytest.raises(RuntimeError, match="feature_flags backend corrupt"):
            _get_canonical_exit_flag()

    def test_b043_canonical_exit_flag_returns_false_on_attribute_error(self, monkeypatch):
        """Legitimate AttributeError path (settings surface missing) still
        returns False \u2014 backwards-compat guarantee.
        """
        from src.execution.harvester import _get_canonical_exit_flag
        from src.config import Settings

        def _raise_attr(self):
            raise AttributeError("feature_flags not configured")

        monkeypatch.setattr(Settings, "feature_flags", property(_raise_attr))
        assert _get_canonical_exit_flag() is False

    def test_b045_fetch_settled_events_raises_on_mid_pagination_failure(self, monkeypatch):
        """HTTPError at offset > 0 must raise, not silently truncate."""
        import httpx
        from src.execution import harvester

        call_state = {"n": 0}

        def _fake_get(url, params=None, timeout=None):
            call_state["n"] += 1
            # First call (offset=0): return 200 items (full page)
            if params.get("offset", 0) == 0:
                class _Resp:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        # Return 200 items so the loop continues to next page
                        return [{"title": "Temperature NYC", "slug": f"s{i}"} for i in range(200)]
                return _Resp()
            # Second call (offset=200): simulate HTTPError
            raise httpx.HTTPError("simulated 503 on page 2")

        monkeypatch.setattr(harvester.httpx, "get", _fake_get)

        with pytest.raises(RuntimeError, match="pagination failed at offset=200"):
            harvester._fetch_settled_events()

    def test_b045_fetch_settled_events_tolerates_first_page_failure(self, monkeypatch):
        """HTTPError at offset == 0 is still tolerated (warning + empty list).

        Backwards-compat: a transient Gamma outage at the start of a
        cycle should not crash the cron job — next cycle will retry.
        """
        import httpx
        from src.execution import harvester

        def _fake_get(url, params=None, timeout=None):
            raise httpx.HTTPError("simulated 503 on page 1")

        monkeypatch.setattr(harvester.httpx, "get", _fake_get)
        result = harvester._fetch_settled_events()
        assert result == []

    def test_b045_fetch_settled_events_happy_path_single_page(self, monkeypatch):
        """Single short page (< 200) returns normally \u2014 no regression."""
        import httpx
        from src.execution import harvester

        def _fake_get(url, params=None, timeout=None):
            class _Resp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return [{"title": "Temperature NYC", "slug": "a"}]
            return _Resp()

        monkeypatch.setattr(harvester.httpx, "get", _fake_get)
        result = harvester._fetch_settled_events()
        assert len(result) == 1
        assert result[0]["slug"] == "a"
