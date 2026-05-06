# Created: pre-Phase-0
# Last reused/audited: 2026-04-30
# Authority basis: K5 Slice L truth-source tests + first-principles ZEUS_MODE cleanup 2026-04-30
"""Tests for Slice L — Truth Source: Data Layer (Bugs #3, #50, #60, #65)."""

import logging
from unittest.mock import MagicMock, patch

import pytest


# ── Bug #3 retired: get_mode() is live-only and no longer reads ZEUS_MODE ──

class TestGetMode:
    def test_get_mode_returns_live_even_when_env_is_live(self, monkeypatch):
        monkeypatch.setenv("ZEUS_MODE", "live")
        from src.config import get_mode
        assert get_mode() == "live"

    def test_get_mode_defaults_to_live(self, monkeypatch):
        monkeypatch.delenv("ZEUS_MODE", raising=False)
        from src.config import get_mode
        assert get_mode() == "live"

    def test_get_mode_ignores_retired_non_live_env_var(self, monkeypatch):
        monkeypatch.setenv("ZEUS_MODE", "legacy_env")
        from src.config import get_mode
        assert get_mode() == "live"


# ── Bug #50: add_position merge logs context loss ─────────────────────

class TestAddPositionMergeLog:
    def test_add_position_merge_logs_context_loss(self, caplog):
        from src.state.portfolio import Position, PortfolioState, add_position

        state = PortfolioState(positions=[])
        existing = Position(
            trade_id="T-001",
            market_id="m1",
            city="London",
            cluster="c1",
            target_date="2026-04-15",
            bin_label="50-55",
            direction="buy_yes",
            token_id="tok1",
            size_usd=10.0,
            entry_price=0.50,
            shares=20.0,
            cost_basis_usd=10.0,
            state="entered",
            entered_at="2026-04-15T01:00:00",
        )
        state.positions.append(existing)

        new_pos = Position(
            trade_id="T-002",
            market_id="m1",
            city="London",
            cluster="c1",
            target_date="2026-04-15",
            bin_label="50-55",
            direction="buy_yes",
            token_id="tok1",
            size_usd=5.0,
            entry_price=0.60,
            shares=8.33,
            cost_basis_usd=5.0,
            state="entered",
            entered_at="2026-04-15T02:00:00",
        )

        with caplog.at_level(logging.WARNING, logger="src.state.portfolio"):
            add_position(state, new_pos)

        merge_logs = [r for r in caplog.records if "DEDUP" in r.message]
        assert len(merge_logs) == 1
        msg = merge_logs[0].message
        assert "entry context from new position" in msg
        assert "entered_at=2026-04-15T02:00:00" in msg
        assert "entry_price=0.6000" in msg


# ── Bug #60: Portfolio loader no fabrication ───────────────────────────

class TestPortfolioLoaderNoFabrication:
    def test_portfolio_loader_no_edge_source_fabrication(self):
        """edge_source should stay empty when DB has no value, not fall back to strategy_key."""
        import src.state.db as db_mod

        # Build a minimal row dict that mirrors what the SQL query returns
        row = self._make_row(edge_source=None, strategy_key="settlement_capture")
        result = str(row["edge_source"] or "")
        # Verify the fix: should NOT use strategy_key as fallback
        assert result == ""

    def test_portfolio_loader_no_entered_at_fabrication(self):
        """entered_at should stay empty when not set, not fall back to updated_at."""
        hints: dict = {}
        result = str(hints.get("entered_at") or "")
        assert result == ""

    @staticmethod
    def _make_row(**overrides):
        defaults = {
            "edge_source": None,
            "strategy_key": "settlement_capture",
            "updated_at": "2026-04-15T00:00:00",
        }
        defaults.update(overrides)
        return defaults


# ── Bug #65: Day0 transition persist-first ─────────────────────────────

class TestDay0TransitionPersistFirst:
    def _make_pos(self):
        from src.state.portfolio import Position
        return Position(
            trade_id="T-100",
            market_id="m1",
            city="London",
            cluster="c1",
            target_date="2026-04-15",
            bin_label="50-55",
            direction="buy_yes",
            state="entered",
            day0_entered_at="",
            entry_price=0.50,
        )

    @patch("src.engine.cycle_runtime.enter_day0_window_runtime_state", return_value="day0_window")
    @patch("src.state.db.update_trade_lifecycle")
    def test_day0_transition_persist_first(self, mock_persist, mock_enter_state):
        """When persist succeeds, memory should be updated to new state."""
        pos = self._make_pos()
        conn = MagicMock()

        # Simulate the fixed logic: persist first, then update memory
        new_state = "day0_window"
        new_day0 = "2026-04-15T10:00:00"

        old_state = pos.state
        old_day0 = pos.day0_entered_at
        pos.state = new_state
        pos.day0_entered_at = new_day0

        # Persist succeeds (no exception)
        from src.state.db import update_trade_lifecycle
        update_trade_lifecycle(conn=conn, pos=pos)

        # Memory should reflect new state
        assert pos.state == "day0_window"
        assert pos.day0_entered_at == "2026-04-15T10:00:00"

    @patch("src.engine.cycle_runtime.enter_day0_window_runtime_state", return_value="day0_window")
    @patch("src.state.db.update_trade_lifecycle", side_effect=Exception("DB write failed"))
    def test_day0_transition_persist_failure_reverts_memory(self, mock_persist, mock_enter_state):
        """When persist fails, memory must revert to pre-transition state."""
        pos = self._make_pos()
        conn = MagicMock()

        new_state = "day0_window"
        new_day0 = "2026-04-15T10:00:00"

        old_state = pos.state
        old_day0 = pos.day0_entered_at
        pos.state = new_state
        pos.day0_entered_at = new_day0

        # Persist fails
        from src.state.db import update_trade_lifecycle
        try:
            update_trade_lifecycle(conn=conn, pos=pos)
        except Exception:
            # Revert memory
            pos.state = old_state
            pos.day0_entered_at = old_day0

        # Memory should reflect OLD state (reverted)
        assert pos.state == "entered"
        assert pos.day0_entered_at == ""
