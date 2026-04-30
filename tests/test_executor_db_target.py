# Lifecycle: created=2026-04-26; last_reviewed=2026-04-27; last_reused=2026-04-27
# Purpose: Ensure executor command writes target zeus_trades DB, not legacy zeus DB.
# Reuse: Run when executor DB connection targeting or venue command schema changes.
# Created: 2026-04-26
# Last reused/audited: 2026-04-27
# Authority basis: P1.S3 critic CRITICAL finding — DB target regression
"""Regression test: executor must write venue_commands to zeus_trades.db, not zeus.db.

Closes critic CRITICAL finding: pre-fix _live_order / execute_exit_order called
get_connection() which opened zeus.db; venue_command tables live in zeus_trades.db.
This test verifies the post-fix behavior: the command row lands in zeus_trades.db.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _cutover_guard_live_enabled(monkeypatch):
    """This file tests DB targeting, not cutover gating."""
    monkeypatch.setattr("src.control.cutover_guard.assert_submit_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_buy_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.state.collateral_ledger.assert_sell_preflight", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_buy", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.execution.executor._reserve_collateral_for_sell", lambda *args, **kwargs: None)


def _ensure_snapshot(conn, *, token_id: str, snapshot_id: str | None = None) -> str:
    from src.contracts.executable_market_snapshot_v2 import ExecutableMarketSnapshotV2
    from src.state.snapshot_repo import get_snapshot, insert_snapshot

    snapshot_id = snapshot_id or f"snap-{token_id}"
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
            orderbook_top_bid=Decimal("0.49"),
            orderbook_top_ask=Decimal("0.56"),
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


def _decision_source_context():
    from src.contracts.execution_intent import DecisionSourceContext

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


def _capture_bound_submission_envelope(mock_client):
    bound = {}
    mock_client.bind_submission_envelope.side_effect = lambda envelope: bound.__setitem__("envelope", envelope)
    return bound


def _final_submit_result(bound: dict, *, order_id: str) -> dict:
    envelope = bound.get("envelope")
    if envelope is None:
        raise AssertionError("test client did not receive a bound submission envelope")
    final = envelope.with_updates(
        raw_response_json=json.dumps({"orderID": order_id, "status": "LIVE"}, sort_keys=True, separators=(",", ":")),
        order_id=order_id,
    )
    return {
        "orderID": order_id,
        "status": "LIVE",
        "_venue_submission_envelope": final.to_dict(),
    }


def _make_entry_intent(conn=None, limit_price: float = 0.55, token_id: str = "tok-" + "0" * 36):
    """Build a minimal ExecutionIntent that passes the ExecutionPrice guard."""
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts import Direction
    from src.contracts.slippage_bps import SlippageBps

    snapshot_id = f"snap-{token_id}"
    if conn is not None:
        _ensure_snapshot(conn, token_id=token_id, snapshot_id=snapshot_id)
    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=SlippageBps(value_bps=200.0, direction="adverse"),
        is_sandbox=False,
        market_id="mkt-test-001",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
        decision_source_context=_decision_source_context(),
    )


def _make_exit_intent(conn=None, trade_id: str = "trd-dbtarget", token_id: str = "tok-" + "1" * 36):
    """Build a minimal ExitOrderIntent."""
    from src.execution.executor import create_exit_order_intent

    snapshot_id = f"snap-{token_id}"
    if conn is not None:
        _ensure_snapshot(conn, token_id=token_id, snapshot_id=snapshot_id)
    return create_exit_order_intent(
        trade_id=trade_id,
        token_id=token_id,
        shares=10.0,
        current_price=0.55,
        executable_snapshot_id=snapshot_id,
        executable_snapshot_min_tick_size=Decimal("0.01"),
        executable_snapshot_min_order_size=Decimal("0.01"),
        executable_snapshot_neg_risk=False,
    )


class TestExecutorDbTarget:
    """Verify venue_commands rows land in zeus_trades.db, not zeus.db."""

    def test_live_order_writes_command_to_trades_db(self, tmp_path, monkeypatch):
        """_live_order(conn=None) writes venue_commands row to zeus_trades.db.

        Sets up two DB files in tmp_path: zeus.db (positions) and zeus_trades.db
        (venue_commands). Patches STATE_DIR so the real DB connection logic points
        to tmp_path. Asserts the command row appears in zeus_trades.db and NOT
        in zeus.db.
        """
        from src.state.db import init_schema, get_trade_connection_with_world

        # Initialise both databases in tmp_path
        trades_db_path = tmp_path / "zeus_trades.db"
        zeus_db_path = tmp_path / "zeus.db"

        trades_conn = sqlite3.connect(str(trades_db_path))
        trades_conn.row_factory = sqlite3.Row
        trades_conn.execute("PRAGMA foreign_keys=ON")
        init_schema(trades_conn)
        trades_conn.commit()

        zeus_conn = sqlite3.connect(str(zeus_db_path))
        zeus_conn.row_factory = sqlite3.Row
        zeus_conn.execute("PRAGMA foreign_keys=ON")
        init_schema(zeus_conn)
        zeus_conn.commit()

        # Patch get_trade_connection_with_world to return the trades DB
        monkeypatch.setattr(
            "src.execution.executor.get_trade_connection_with_world",
            lambda: sqlite3.connect(str(trades_db_path)),
        )

        from src.execution.executor import _live_order

        intent = _make_entry_intent(trades_conn)
        trades_conn.commit()

        mock_client = MagicMock()
        mock_client.v2_preflight.return_value = None
        bound = _capture_bound_submission_envelope(mock_client)
        mock_client.place_limit_order.side_effect = (
            lambda **kwargs: _final_submit_result(bound, order_id="ord-dbtarget-001")
        )

        with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
            with patch("src.execution.executor.alert_trade", lambda **kw: None):
                result = _live_order(
                    trade_id="trd-dbtarget-001",
                    intent=intent,
                    shares=10.0,
                    conn=None,
                    decision_id="dec-dbtarget-001",
                )

        assert result is not None and result.status == "pending"

        # Assert command row landed in zeus_trades.db
        verify_trades = sqlite3.connect(str(trades_db_path))
        row_count_trades = verify_trades.execute(
            "SELECT COUNT(*) FROM venue_commands"
        ).fetchone()[0]
        verify_trades.close()
        assert row_count_trades == 1, (
            f"Expected 1 venue_commands row in zeus_trades.db, found {row_count_trades}"
        )

        # Assert command row did NOT land in zeus.db
        row_count_zeus = zeus_conn.execute(
            "SELECT COUNT(*) FROM venue_commands"
        ).fetchone()[0]
        zeus_conn.close()
        trades_conn.close()
        assert row_count_zeus == 0, (
            f"Expected 0 venue_commands rows in zeus.db (wrong target!), found {row_count_zeus}"
        )

    def test_exit_order_writes_command_to_trades_db(self, tmp_path, monkeypatch):
        """execute_exit_order(conn=None) writes venue_commands row to zeus_trades.db."""
        from src.state.db import init_schema

        trades_db_path = tmp_path / "zeus_trades.db"
        zeus_db_path = tmp_path / "zeus.db"

        trades_conn = sqlite3.connect(str(trades_db_path))
        trades_conn.row_factory = sqlite3.Row
        trades_conn.execute("PRAGMA foreign_keys=ON")
        init_schema(trades_conn)
        trades_conn.commit()

        zeus_conn = sqlite3.connect(str(zeus_db_path))
        zeus_conn.row_factory = sqlite3.Row
        zeus_conn.execute("PRAGMA foreign_keys=ON")
        init_schema(zeus_conn)
        zeus_conn.commit()

        monkeypatch.setattr(
            "src.execution.executor.get_trade_connection_with_world",
            lambda: sqlite3.connect(str(trades_db_path)),
        )

        from src.execution.executor import execute_exit_order

        intent = _make_exit_intent(trades_conn)
        trades_conn.commit()

        mock_client = MagicMock()
        bound = _capture_bound_submission_envelope(mock_client)
        mock_client.place_limit_order.side_effect = (
            lambda **kwargs: _final_submit_result(bound, order_id="ord-exit-dbtarget-001")
        )

        with patch("src.data.polymarket_client.PolymarketClient", return_value=mock_client):
            with patch("src.execution.executor.alert_trade", lambda **kw: None):
                result = execute_exit_order(
                    intent=intent,
                    conn=None,
                    decision_id="dec-exit-dbtarget-001",
                )

        assert result is not None and result.status == "pending"

        verify_trades = sqlite3.connect(str(trades_db_path))
        row_count_trades = verify_trades.execute(
            "SELECT COUNT(*) FROM venue_commands"
        ).fetchone()[0]
        verify_trades.close()
        assert row_count_trades == 1, (
            f"Expected 1 venue_commands row in zeus_trades.db, found {row_count_trades}"
        )

        row_count_zeus = zeus_conn.execute(
            "SELECT COUNT(*) FROM venue_commands"
        ).fetchone()[0]
        zeus_conn.close()
        trades_conn.close()
        assert row_count_zeus == 0, (
            f"Expected 0 venue_commands rows in zeus.db (wrong target!), found {row_count_zeus}"
        )
