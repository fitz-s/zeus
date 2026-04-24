"""Tests for K5 Slice K — Truth Source: Risk & Reconciliation (Bugs #56, #73, #75)."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from src.state.portfolio import Position, PortfolioState
from src.state.portfolio_loader_policy import LoaderPolicyDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_position(
    trade_id: str = "test-pos-1",
    shares: float = 10.0,
    chain_state: str = "unknown",
) -> Position:
    return Position(
        trade_id=trade_id,
        market_id="test-market",
        city="Chicago",
        cluster="test-cluster",
        target_date="2026-04-15",
        bin_label="60-65",
        direction="buy_yes",
        shares=shares,
        chain_state=chain_state,
    )


def _make_in_memory_db_with_outcome_fact(rows: list[dict] | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE outcome_fact (
            strategy_key TEXT, city TEXT, target_date TEXT,
            position_id TEXT, exit_reason TEXT, settled_at TEXT, pnl REAL
        )"""
    )
    if rows:
        for r in rows:
            conn.execute(
                "INSERT INTO outcome_fact VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    r.get("strategy_key", "s1"),
                    r.get("city", "Chicago"),
                    r.get("target_date", "2026-04-15"),
                    r.get("position_id", "p1"),
                    r.get("exit_reason", "SETTLEMENT"),
                    r.get("settled_at", "2026-04-14T12:00:00"),
                    r.get("pnl", 1.5),
                ),
            )
    return conn


def _make_in_memory_db_with_chronicle(rows: list[dict] | None = None) -> sqlite3.Connection:
    """DB with chronicle table only (no outcome_fact)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE chronicle (
            id INTEGER PRIMARY KEY, event_type TEXT, env TEXT,
            trade_id TEXT, timestamp TEXT, details_json TEXT
        )"""
    )
    if rows:
        for i, r in enumerate(rows):
            import json
            details = json.dumps({
                "city": r.get("city", "Chicago"),
                "range_label": r.get("range_label", "60-65"),
                "target_date": r.get("target_date", "2026-04-15"),
                "direction": r.get("direction", "buy_yes"),
                "exit_reason": r.get("exit_reason", "SETTLEMENT"),
                "pnl": r.get("pnl", 1.5),
            })
            conn.execute(
                "INSERT INTO chronicle VALUES (?, ?, ?, ?, ?, ?)",
                (i + 1, "SETTLEMENT", r.get("env", "live"), r.get("trade_id", f"t{i}"), "2026-04-14T12:00:00", details),
            )
    return conn


# ---------------------------------------------------------------------------
# Bug #56: Size mismatch without baseline → tagged, not skipped
# ---------------------------------------------------------------------------


def test_reconciliation_size_mismatch_no_baseline_tags_unresolved():
    """When canonical baseline is missing (conn=None), position is tagged
    size_mismatch_unresolved and chain-verified fields are still applied."""
    from src.state.chain_reconciliation import reconcile, ChainPosition

    pos = _make_position(trade_id="test-pos-1", shares=10.0, chain_state="unknown")
    pos.token_id = "tok-abc"
    portfolio = PortfolioState(positions=[pos], bankroll=100.0)

    chain_positions = [ChainPosition(
        token_id="tok-abc", size=15.0, avg_price=0.55, cost=8.25, condition_id="cond-1",
    )]

    # conn=None makes _append_canonical_size_correction_if_available return False
    stats = reconcile(portfolio, chain_positions, conn=None)

    updated_pos = portfolio.positions[0]
    assert updated_pos.chain_state == "size_mismatch_unresolved"
    # Chain-verified fields should still be applied (no silent continue)
    assert updated_pos.chain_shares == 15.0
    assert updated_pos.entry_price == 0.55
    assert updated_pos.condition_id == "cond-1"
    assert stats.get("skipped_size_correction_missing_canonical_baseline", 0) >= 1
    assert stats.get("synced", 0) >= 1  # fell through to position update


def test_reconciliation_size_mismatch_with_baseline_still_works():
    """Normal correction path: baseline available via conn → updated, synced."""
    from src.state.chain_reconciliation import reconcile, ChainPosition

    pos = _make_position(trade_id="test-pos-2", shares=10.0, chain_state="unknown")
    pos.token_id = "tok-def"
    portfolio = PortfolioState(positions=[pos], bankroll=100.0)

    chain_positions = [ChainPosition(
        token_id="tok-def", size=12.0, avg_price=0.60, cost=7.20, condition_id="cond-2",
    )]

    # Provide a real in-memory DB with the tables the nested function needs
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE position_current (position_id TEXT, phase TEXT)")
    conn.execute("INSERT INTO position_current VALUES ('test-pos-2', 'active')")
    conn.execute("CREATE TABLE position_events (position_id TEXT, sequence_no INTEGER)")

    with patch(
        "src.engine.lifecycle_events.build_chain_size_corrected_canonical_write",
        return_value=([], {}),
    ), patch(
        "src.state.db.append_many_and_project",
    ):
        stats = reconcile(portfolio, chain_positions, conn=conn)

    updated_pos = portfolio.positions[0]
    assert updated_pos.chain_state == "synced"
    assert updated_pos.shares == 12.0
    assert stats.get("updated", 0) >= 1


# ---------------------------------------------------------------------------
# Bug #73: Riskguard rejects working-state fallback
# ---------------------------------------------------------------------------


def test_riskguard_rejects_working_state_fallback():
    """When policy returns json_fallback, riskguard raises RuntimeError."""
    from src.riskguard.riskguard import _load_riskguard_portfolio_truth

    fake_conn = MagicMock()
    with patch(
        "src.riskguard.riskguard.query_portfolio_loader_view",
        return_value={"status": "partial_stale", "positions": []},
    ), patch(
        "src.riskguard.riskguard.choose_portfolio_truth_source",
        return_value=LoaderPolicyDecision(
            source="json_fallback",
            reason="partial_stale must not silently hide open positions",
            escalate=True,
        ),
    ):
        with pytest.raises(RuntimeError, match="riskguard requires canonical truth source"):
            _load_riskguard_portfolio_truth(fake_conn)


def test_riskguard_accepts_canonical_db():
    """Canonical DB path works normally."""
    from src.riskguard.riskguard import _load_riskguard_portfolio_truth

    fake_conn = MagicMock()
    mock_meta = MagicMock()
    mock_meta.bankroll = 500.0
    mock_meta.updated_at = "2026-04-15T00:00:00"
    mock_meta.daily_baseline_total = 500.0
    mock_meta.weekly_baseline_total = 500.0
    mock_meta.recent_exits = []
    mock_meta.ignored_tokens = []

    with patch(
        "src.riskguard.riskguard.query_portfolio_loader_view",
        return_value={"status": "ok", "positions": []},
    ), patch(
        "src.riskguard.riskguard.choose_portfolio_truth_source",
        return_value=LoaderPolicyDecision(source="canonical_db", reason="ok"),
    ), patch(
        "src.riskguard.riskguard._load_riskguard_capital_metadata",
        return_value=(mock_meta, "db"),
    ):
        portfolio, truth = _load_riskguard_portfolio_truth(fake_conn)

    assert truth["source"] == "position_current"
    assert truth["fallback_active"] is False


# ---------------------------------------------------------------------------
# Bug #75: Realized exits degradation tracking
# ---------------------------------------------------------------------------


def test_realized_exits_outcome_fact_primary():
    """outcome_fact with rows → returns them, not degraded."""
    from src.riskguard.riskguard import _current_mode_realized_exits

    conn = _make_in_memory_db_with_outcome_fact([{"pnl": 2.5, "city": "NYC"}])
    exits, source, degraded = _current_mode_realized_exits(conn, env="live")
    assert source == "outcome_fact"
    assert degraded is False
    assert len(exits) == 1
    assert exits[0]["city"] == "NYC"


def test_realized_exits_empty_outcome_fact_does_not_degrade():
    """Empty outcome_fact table → valid empty result, not degraded to chronicle."""
    from src.riskguard.riskguard import _current_mode_realized_exits

    conn = _make_in_memory_db_with_outcome_fact([])  # table exists, no rows
    exits, source, degraded = _current_mode_realized_exits(conn, env="live")
    assert source == "outcome_fact"
    assert degraded is False
    assert exits == []


def test_realized_exits_missing_outcome_fact_degrades_with_warning(caplog):
    """OperationalError on outcome_fact → falls back to chronicle, degraded=True."""
    from src.riskguard.riskguard import _current_mode_realized_exits

    conn = _make_in_memory_db_with_chronicle([
        {"city": "London", "pnl": 3.0, "trade_id": "t1"},
    ])
    with caplog.at_level(logging.WARNING, logger="src.riskguard.riskguard"):
        exits, source, degraded = _current_mode_realized_exits(conn, env="live")
    assert source == "chronicle_dedup"
    assert degraded is True
    assert len(exits) == 1
    assert "outcome_fact unavailable" in caplog.text


def test_realized_exits_no_connection():
    """conn=None → empty result, not degraded."""
    from src.riskguard.riskguard import _current_mode_realized_exits

    exits, source, degraded = _current_mode_realized_exits(None, env="live")
    assert exits == []
    assert source == "none"
    assert degraded is False


def test_size_mismatch_unresolved_survives_position_roundtrip():
    """SIZE_MISMATCH_UNRESOLVED must be a valid ChainState for Position construction."""
    pos = _make_position(chain_state="size_mismatch_unresolved")
    assert pos.chain_state == "size_mismatch_unresolved"
    # Verify replace() also works (used by reconciliation)
    pos2 = replace(pos, trade_id="roundtrip-test")
    assert pos2.chain_state == "size_mismatch_unresolved"
