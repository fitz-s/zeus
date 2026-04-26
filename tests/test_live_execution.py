"""Tier 5.1 u2014 Live execution mock test.

Mock Polymarket CLOB, run _live_order through happy path + error modes.
Assert OrderResult and position_events side effects.

P1.S3 update: _live_order now persists a venue_commands row (INV-30) before
submitting. Tests that call _live_order without an explicit conn use the
`_mem_conn` autouse fixture to supply an in-memory DB with schema.
"""
import sqlite3

import pytest
from unittest.mock import MagicMock, patch
from src.execution.executor import _live_order, OrderResult


@pytest.fixture(autouse=True)
def _mem_conn(monkeypatch):
    """Inject an in-memory DB into _live_order's get_connection fallback.

    _live_order accepts an optional `conn` parameter (P1.S3). When tests
    call it without one, the executor calls `get_connection()`. We patch
    that call to return an in-memory DB with schema instead of the live
    state file, so unit tests don't depend on on-disk DB state.
    """
    from src.state.db import init_schema

    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute("PRAGMA foreign_keys=ON")
    init_schema(mem)
    monkeypatch.setattr("src.execution.executor.get_connection", lambda: mem)
    yield mem
    mem.close()


def _make_intent(**overrides):
    """Minimal ExecutionIntent for testing."""
    defaults = {
        "direction": MagicMock(value="BUY"),
        "token_id": "tok_" + "a" * 60,
        "limit_price": 0.45,
        "market_id": "mkt_test",
        "timeout_seconds": 30,
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestLiveOrderHappyPath:
    def test_successful_order_returns_pending(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.place_limit_order.return_value = {"orderID": "ord-123"}
        monkeypatch.setattr(
            "src.data.polymarket_client.PolymarketClient",
            lambda: mock_client,
        )
        monkeypatch.setattr(
            "src.execution.executor.alert_trade",
            lambda **kwargs: None,
        )

        result = _live_order("trade-1", _make_intent(), shares=10.0)

        assert result.status == "pending"
        assert result.order_id == "ord-123"
        assert result.trade_id == "trade-1"
        mock_client.place_limit_order.assert_called_once()

    def test_order_id_fallback_chain(self, monkeypatch):
        """orderID -> orderId -> id -> trade_id."""
        mock_client = MagicMock()
        mock_client.place_limit_order.return_value = {"id": "fallback-id"}
        monkeypatch.setattr(
            "src.data.polymarket_client.PolymarketClient",
            lambda: mock_client,
        )
        monkeypatch.setattr(
            "src.execution.executor.alert_trade",
            lambda **kwargs: None,
        )

        result = _live_order("trade-2", _make_intent(), shares=5.0)
        assert result.order_id == "fallback-id"


class TestLiveOrderErrorModes:
    def test_clob_returns_none(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.place_limit_order.return_value = None
        monkeypatch.setattr(
            "src.data.polymarket_client.PolymarketClient",
            lambda: mock_client,
        )

        result = _live_order("trade-3", _make_intent(), shares=10.0)

        assert result.status == "rejected"
        assert "None" in result.reason

    def test_clob_raises_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.place_limit_order.side_effect = ConnectionError("CLOB down")
        monkeypatch.setattr(
            "src.data.polymarket_client.PolymarketClient",
            lambda: mock_client,
        )

        result = _live_order("trade-4", _make_intent(), shares=10.0)

        assert result.status == "rejected"
        assert "CLOB down" in result.reason

    def test_discord_alert_failure_does_not_block_order(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.place_limit_order.return_value = {"orderID": "ord-ok"}
        monkeypatch.setattr(
            "src.data.polymarket_client.PolymarketClient",
            lambda: mock_client,
        )
        monkeypatch.setattr(
            "src.execution.executor.alert_trade",
            MagicMock(side_effect=RuntimeError("Discord down")),
        )

        result = _live_order("trade-5", _make_intent(), shares=10.0)

        assert result.status == "pending"  # order succeeded despite alert failure
        assert result.order_id == "ord-ok"


class TestModeIsolation:
    """Tier 5.2 u2014 Mode isolation regression test.

    Even though paper is decommissioned, this test protects Phase 2
    refactor from accidentally re-introducing cross-mode leakage.
    """

    @pytest.mark.skip(reason="Phase2: paper_mode param removed")
    def test_live_order_always_uses_paper_mode_false(self, monkeypatch):
        """PolymarketClient is ALWAYS constructed with paper_mode=False in _live_order."""
        captured_paper_mode = []

        def capture_client(paper_mode):
            captured_paper_mode.append(paper_mode)
            client = MagicMock()
            client.place_limit_order.return_value = {"orderID": "test"}
            return client

        monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", capture_client)
        monkeypatch.setattr("src.execution.executor.alert_trade", lambda **kw: None)

        _live_order("t1", _make_intent(), shares=1.0)

        assert captured_paper_mode == [False]
