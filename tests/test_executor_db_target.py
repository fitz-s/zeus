# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: P1.S3 critic CRITICAL finding — DB target regression
"""Regression test: executor must write venue_commands to zeus_trades.db, not zeus.db.

Closes critic CRITICAL finding: pre-fix _live_order / execute_exit_order called
get_connection() which opened zeus.db; venue_command tables live in zeus_trades.db.
This test verifies the post-fix behavior: the command row lands in zeus_trades.db.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_entry_intent(limit_price: float = 0.55, token_id: str = "tok-" + "0" * 36):
    """Build a minimal ExecutionIntent that passes the ExecutionPrice guard."""
    from src.contracts.execution_intent import ExecutionIntent
    from src.contracts import Direction

    return ExecutionIntent(
        direction=Direction("buy_yes"),
        target_size_usd=10.0,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=0.02,
        is_sandbox=False,
        market_id="mkt-test-001",
        token_id=token_id,
        timeout_seconds=3600,
        decision_edge=0.05,
    )


def _make_exit_intent(trade_id: str = "trd-dbtarget", token_id: str = "tok-" + "1" * 36):
    """Build a minimal ExitOrderIntent."""
    from src.execution.executor import create_exit_order_intent

    return create_exit_order_intent(
        trade_id=trade_id,
        token_id=token_id,
        shares=10.0,
        current_price=0.55,
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

        intent = _make_entry_intent()

        mock_client = MagicMock()
        mock_client.v2_preflight.return_value = None
        mock_client.place_limit_order.return_value = {"orderID": "ord-dbtarget-001"}

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

        intent = _make_exit_intent()

        mock_client = MagicMock()
        mock_client.place_limit_order.return_value = {"orderID": "ord-exit-dbtarget-001"}

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
