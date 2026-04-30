# Lifecycle: created=2026-04-27; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Mock live execution happy/error path coverage with R3 cutover guard opt-outs.
# Reuse: Run when _live_order side effects, ACK semantics, or mock CLOB behavior changes.
# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: R3 Z1 cutover guard audit; pre-existing live execution mock tests updated to opt out of CutoverGuard so they keep testing executor mechanics.
"""Tier 5.1 u2014 Live execution mock test.

Mock Polymarket CLOB, run _live_order through happy path + error modes.
Assert OrderResult and position_events side effects.

P1.S3 update: _live_order now persists a venue_commands row (INV-30) before
submitting. Tests that call _live_order without an explicit conn use the
`_mem_conn` autouse fixture to supply an in-memory DB with schema.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from unittest.mock import MagicMock, patch
from src.contracts.execution_intent import DecisionSourceContext
from src.execution.executor import _live_order, OrderResult

_TEST_CONN = None
_NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)


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
    global _TEST_CONN
    _TEST_CONN = mem
    monkeypatch.setattr("src.execution.executor.get_trade_connection_with_world", lambda: mem)
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)
    yield mem
    _TEST_CONN = None
    mem.close()


def _make_intent(**overrides):
    """Minimal ExecutionIntent for testing."""
    token_id = overrides.get("token_id", "tok_" + "a" * 60)
    snapshot_id = _ensure_snapshot(_TEST_CONN, token_id=token_id)
    defaults = {
        "direction": MagicMock(value="BUY"),
        "token_id": token_id,
        "limit_price": 0.45,
        "market_id": "mkt_test",
        "timeout_seconds": 30,
        "executable_snapshot_id": snapshot_id,
        "executable_snapshot_min_tick_size": Decimal("0.01"),
        "executable_snapshot_min_order_size": Decimal("0.01"),
        "executable_snapshot_neg_risk": False,
        "decision_source_context": _decision_source_context(),
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def _decision_source_context() -> DecisionSourceContext:
    return DecisionSourceContext(
        source_id="tigge",
        model_family="ecmwf_ifs025",
        forecast_issue_time="2026-04-27T00:00:00+00:00",
        forecast_valid_time="2026-04-27T06:00:00+00:00",
        forecast_fetch_time="2026-04-27T01:00:00+00:00",
        forecast_available_at="2026-04-27T00:30:00+00:00",
        raw_payload_hash="a" * 64,
        degradation_level="OK",
        forecast_source_role="entry_primary",
        authority_tier="FORECAST",
        decision_time="2026-04-27T02:00:00+00:00",
        decision_time_status="OK",
    )


def _ensure_snapshot(conn, *, token_id: str) -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    assert conn is not None
    snapshot_id = f"snap-{token_id}"
    if get_snapshot(conn, snapshot_id) is not None:
        return snapshot_id
    insert_snapshot(
        conn,
        ExecutableMarketSnapshotV2(
            snapshot_id=snapshot_id,
            gamma_market_id="gamma-test",
            event_id="event-test",
            event_slug="event-test",
            condition_id="condition-test",
            question_id="question-test",
            yes_token_id=token_id,
            no_token_id=f"{token_id}-no",
            selected_outcome_token_id=token_id,
            outcome_label="YES",
            enable_orderbook=True,
            active=True,
            closed=False,
            accepting_orders=True,
            market_start_at=None,
            market_end_at=None,
            market_close_at=None,
            sports_start_at=None,
            min_tick_size=Decimal("0.01"),
            min_order_size=Decimal("0.01"),
            fee_details={},
            token_map_raw={"YES": token_id, "NO": f"{token_id}-no"},
            rfqe=None,
            neg_risk=False,
            orderbook_top_bid=Decimal("0.44"),
            orderbook_top_ask=Decimal("0.46"),
            orderbook_depth_jsonb="{}",
            raw_gamma_payload_hash="a" * 64,
            raw_clob_market_info_hash="b" * 64,
            raw_orderbook_hash="c" * 64,
            authority_tier="CLOB",
            captured_at=_NOW,
            freshness_deadline=_NOW + timedelta(days=365),
        ),
    )
    return snapshot_id


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
        assert "clob_returned_none" in result.reason

    def test_clob_raises_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.place_limit_order.side_effect = ConnectionError("CLOB down")
        monkeypatch.setattr(
            "src.data.polymarket_client.PolymarketClient",
            lambda: mock_client,
        )

        result = _live_order("trade-4", _make_intent(), shares=10.0)

        assert result.status == "unknown_side_effect"
        assert result.command_state == "SUBMIT_UNKNOWN_SIDE_EFFECT"
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
