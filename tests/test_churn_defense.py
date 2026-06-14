"""Tests for 8-layer churn defense.

Each test targets one specific churn vector from the legacy-predecessor forensic audit.
Wave 3 (2026-06-02): evaluate_exit_triggers deleted (dead twin). Tests repointed to
  Position.evaluate_exit (the one live path). _make_edge_context removed; _call_exit
  wraps ExitContext construction.
"""

import pytest
from src.state.portfolio import (
    Position, PortfolioState, ExitContext,
    has_same_city_range_open,
    remove_position,
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


def _call_exit(
    pos: Position,
    fresh_prob: float,
    current_market_price: float,
    *,
    hours_to_settlement: float = 24.0,
    best_bid: float | None = None,
    divergence_score: float = 0.0,
):
    """Thin wrapper: call the one live exit path."""
    ctx = ExitContext(
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid if best_bid is not None else current_market_price,
        hours_to_settlement=hours_to_settlement,
        position_state="active",
        market_velocity_1h=0.0,
        divergence_score=divergence_score,
    )
    return pos.evaluate_exit(ctx)


class TestBuyNoNoFalseReversal:
    def test_clob_failure_fallback_no_exit(self):
        """CLOB refresh fails → fallback uses stored values → edge sign stays correct."""
        pos = _pos(p_posterior=0.95, entry_price=0.91)
        # fresh_prob=0.95, current_market=0.91 → buy_no edge positive → HOLD
        decision = _call_exit(pos, 0.95, 0.91)
        assert not decision.should_exit

    def test_mixed_space_bug_scenario(self):
        """The exact bug: p_posterior=0.95 (NO), entry_price=0.09 (YES) → false reversal.

        With the fix, this scenario shouldn't happen because executor stores
        fill_price in native space. But if it DID happen, the simple subtraction
        would give 0.95 - 0.09 = 0.86 → positive → HOLD (not reversal).
        """
        pos = _pos(p_posterior=0.95, entry_price=0.09)
        # fresh_prob=0.95 far above market=0.09 → strong positive edge → HOLD
        decision = _call_exit(pos, 0.95, 0.09)
        assert not decision.should_exit


class TestBuyNoConsecutiveCycles:
    def test_one_negative_cycle_holds(self):
        """1 negative cycle → HOLD. Must see 2 consecutive.

        Use hours_to_settlement=72.0 to bypass near_settlement_gate (fires at <48h),
        so the consecutive-cycle counter actually runs.
        """
        pos = _pos()
        # fresh_prob=0.80, market=0.91 → buy_no edge = 0.80-0.91 = -0.11 (negative)
        decision = _call_exit(pos, 0.80, 0.91, hours_to_settlement=72.0)
        assert not decision.should_exit
        assert pos.neg_edge_count == 1

    def test_two_consecutive_exits(self):
        """2 consecutive negative cycles → BUY_NO_EDGE_EXIT."""
        pos = _pos()
        _call_exit(pos, 0.80, 0.91, hours_to_settlement=72.0)  # cycle 1: neg
        decision = _call_exit(pos, 0.80, 0.91, hours_to_settlement=72.0)  # cycle 2: neg
        assert decision.should_exit
        assert decision.trigger == "BUY_NO_EDGE_EXIT"

    def test_reset_on_positive(self):
        """Negative → positive → negative → only 1 count (reset)."""
        pos = _pos()
        _call_exit(pos, 0.80, 0.91, hours_to_settlement=72.0)  # neg → count=1
        _call_exit(pos, 0.95, 0.91, hours_to_settlement=72.0)  # pos → count=0
        decision = _call_exit(pos, 0.80, 0.91, hours_to_settlement=72.0)  # neg → count=1
        assert not decision.should_exit  # Only 1 cycle, not 2

    def test_near_settlement_hold(self):
        """Buy-no near settlement (<near_settlement_hours): hold unless deeply negative."""
        pos = _pos()
        # Mildly negative edge, near settlement (2h < 48h threshold)
        decision = _call_exit(pos, 0.80, 0.91, hours_to_settlement=2.0)
        assert not decision.should_exit  # Hold: -0.11 is not deeply negative (-0.15)


class TestTimeBansRemoved:
    """Layers 5 (is_reentry_blocked, 20-min reversal time-ban) and 6
    (is_token_on_cooldown, 1-hr post-fail time-ban) were DELETED 2026-06-14 under
    the operator no-caps law (time-bans are not derived from belief/quote/edge/
    Kelly/arm). This is the removal antibody: the functions must stay gone so a
    later session cannot silently re-introduce a wall-clock churn cap. Honest
    inflight dedup (Layer 7) is verified by TestSameCityRange / layer-7 tests."""

    def test_reentry_time_ban_function_removed(self):
        import src.state.portfolio as pf
        assert not hasattr(pf, "is_reentry_blocked"), (
            "is_reentry_blocked (20-min time-ban) must stay removed — operator no-caps law")

    def test_token_cooldown_time_ban_function_removed(self):
        import src.state.portfolio as pf
        assert not hasattr(pf, "is_token_on_cooldown"), (
            "is_token_on_cooldown (1-hr time-ban) must stay removed — operator no-caps law")


class TestEVGate:
    def test_ev_gate_prevents_spread_loss(self):
        """Edge reversed but sell price < hold EV → HOLD."""
        pos = _pos(direction="buy_yes", p_posterior=0.60, entry_price=0.50)
        pos.neg_edge_count = 1  # Pre-set to trigger on next negative

        # Edge negative (fresh_prob=0.40, market=0.55 → buy_yes edge = -0.15)
        # best_bid (0.35) < p_posterior (0.60) → selling worse than holding
        decision = _call_exit(pos, 0.40, 0.55, best_bid=0.35)
        assert not decision.should_exit  # EV gate blocks the exit


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
        decision = _call_exit(pos, 0.50, 0.91)
        assert not decision.should_exit
