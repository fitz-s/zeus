"""Tests for 8-layer churn defense.

Each test targets one specific churn vector from the legacy-predecessor forensic audit.
"""

import pytest
import numpy as np
from datetime import datetime, timezone

from src.execution.exit_triggers import (
    evaluate_exit_triggers, ExitSignal,
    _evaluate_buy_no_exit, _evaluate_buy_yes_exit,
)
from src.state.portfolio import (
    Position, PortfolioState,
    is_reentry_blocked, is_token_on_cooldown, has_same_city_range_open,
    remove_position,
)
from src.contracts import EdgeContext, EntryMethod


def _make_edge_context(p_posterior: float, entry_price: float) -> EdgeContext:
    """Build a minimal EdgeContext for tests. forward_edge = p_posterior - entry_price."""
    forward_edge = p_posterior - entry_price
    dummy_vec = np.array([1.0])
    return EdgeContext(
        p_raw=dummy_vec,
        p_cal=dummy_vec,
        p_market=dummy_vec,
        p_posterior=p_posterior,
        forward_edge=forward_edge,
        alpha=0.55,
        confidence_band_upper=forward_edge + 0.05,
        confidence_band_lower=forward_edge - 0.05,
        entry_provenance=EntryMethod.ENS_MEMBER_COUNTING,
        decision_snapshot_id="test-snap",
        n_edges_found=1,
        n_edges_after_fdr=1,
        market_velocity_1h=0.0,
        divergence_score=0.0,
    )


def _pos(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1", market_id="m1", city="NYC",
        cluster="US-Northeast", target_date="2026-04-01",
        bin_label="62°F or higher", direction="buy_no",
        size_usd=10.0, entry_price=0.91, p_posterior=0.95,
        edge=0.04, entered_at="2026-03-30T08:00:00Z",
        token_id="yes123", no_token_id="no456",
    )
    defaults.update(kwargs)
    return Position(**defaults)


class TestBuyNoNoFalseReversal:
    def test_clob_failure_fallback_no_exit(self):
        """CLOB refresh fails → fallback uses stored values → edge sign stays correct."""
        pos = _pos(p_posterior=0.95, entry_price=0.91)
        # Fallback: both values in NO-space
        # forward_edge = 0.95 - 0.91 = 0.04 → positive → HOLD
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.95, 0.91))
        assert signal is None

    def test_mixed_space_bug_scenario(self):
        """The exact bug: p_posterior=0.95 (NO), entry_price=0.09 (YES) → false reversal.

        With the fix, this scenario shouldn't happen because executor stores
        fill_price in native space. But if it DID happen, the simple subtraction
        would give 0.95 - 0.09 = 0.86 → positive → HOLD (not reversal).
        """
        pos = _pos(p_posterior=0.95, entry_price=0.09)
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.95, 0.09))
        # 0.95 - 0.09 = 0.86 → huge positive → HOLD
        assert signal is None


class TestBuyNoConsecutiveCycles:
    def test_one_negative_cycle_holds(self):
        """1 negative cycle → HOLD. Must see 2 consecutive."""
        pos = _pos()
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.80, 0.91))  # edge = -0.11
        assert signal is None
        assert pos.neg_edge_count == 1

    def test_two_consecutive_exits(self):
        """2 consecutive negative cycles → BUY_NO_EDGE_EXIT."""
        pos = _pos()
        evaluate_exit_triggers(pos, _make_edge_context(0.80, 0.91))  # cycle 1: neg
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.80, 0.91))  # cycle 2: neg
        assert signal is not None
        assert signal.trigger == "BUY_NO_EDGE_EXIT"

    def test_reset_on_positive(self):
        """Negative → positive → negative → only 1 count (reset)."""
        pos = _pos()
        evaluate_exit_triggers(pos, _make_edge_context(0.80, 0.91))  # neg → count=1
        evaluate_exit_triggers(pos, _make_edge_context(0.95, 0.91))  # pos → count=0
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.80, 0.91))  # neg → count=1
        assert signal is None  # Only 1 cycle, not 2

    def test_near_settlement_hold(self):
        """Buy-no near settlement (<4h): hold unless deeply negative."""
        pos = _pos()
        # Mildly negative edge, near settlement
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.80, 0.91), hours_to_settlement=2.0)
        assert signal is None  # Hold: -0.11 is not deeply negative (-0.20)


class TestReentryBlocked:
    def test_range_reentry_blocked_after_reversal(self):
        """Exit via EDGE_REVERSAL → same range blocked for 20min."""
        state = PortfolioState(bankroll=100.0)
        pos = _pos()
        state.positions.append(pos)

        remove_position(state, "t1", exit_reason="EDGE_REVERSAL")

        assert is_reentry_blocked(
            state, "NYC", "62°F or higher", "2026-04-01", minutes=20
        ) is True

    def test_different_range_not_blocked(self):
        state = PortfolioState(bankroll=100.0)
        pos = _pos()
        state.positions.append(pos)
        remove_position(state, "t1", exit_reason="EDGE_REVERSAL")

        assert is_reentry_blocked(
            state, "NYC", "58-59°F", "2026-04-01", minutes=20
        ) is False


class TestVoidedTokenCooldown:
    def test_voided_token_blocked(self):
        state = PortfolioState(bankroll=100.0)
        pos = _pos()
        state.positions.append(pos)
        remove_position(state, "t1", exit_reason="UNFILLED_ORDER")

        assert is_token_on_cooldown(state, "no456", hours=1.0) is True

    def test_non_voided_not_blocked(self):
        state = PortfolioState(bankroll=100.0)
        pos = _pos()
        state.positions.append(pos)
        remove_position(state, "t1", exit_reason="EDGE_REVERSAL")

        assert is_token_on_cooldown(state, "no456", hours=1.0) is False


class TestEVGate:
    def test_ev_gate_prevents_spread_loss(self):
        """Edge reversed but sell price < hold EV → HOLD."""
        pos = _pos(direction="buy_yes", p_posterior=0.60, entry_price=0.50)
        pos.neg_edge_count = 1  # Pre-set to trigger on next negative

        # Edge negative (0.40 - 0.55 = -0.15) but best_bid (0.35) < p_posterior (0.60)
        # Selling at 0.35 is worse than holding for EV of 0.60
        signal = evaluate_exit_triggers(
            pos, _make_edge_context(0.40, 0.55), best_bid=0.35,
        )
        assert signal is None  # EV gate blocks the exit


class TestCrossDateBlock:
    def test_same_city_range_blocked(self):
        state = PortfolioState(bankroll=100.0)
        state.positions.append(_pos(target_date="2026-04-01"))

        assert has_same_city_range_open(state, "NYC", "62°F or higher") is True

    def test_different_city_not_blocked(self):
        state = PortfolioState(bankroll=100.0)
        state.positions.append(_pos(target_date="2026-04-01"))

        assert has_same_city_range_open(state, "Chicago", "62°F or higher") is False


class TestMicroPositionHold:
    def test_micro_position_never_exits(self):
        """Positions < $1 are never sold — hold to settlement."""
        pos = _pos(size_usd=0.50)
        # Even with negative edge, micro-position holds
        signal = evaluate_exit_triggers(pos, _make_edge_context(0.50, 0.91))
        assert signal is None
