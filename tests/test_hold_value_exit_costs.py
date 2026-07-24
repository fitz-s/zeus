# Created: 2026-04-24
# Last reused/audited: 2026-07-24
# Lifecycle: created=2026-04-24; last_reviewed=2026-07-24; last_reused=2026-07-24
# Authority basis: docs/operations/current/plans/ultimate_alpha_2026-07-23/ (PR-1
#   离场律 collapse — HoldValue exit cost model reduced to fee-only; static daily
#   hurdle + correlation surcharge RETIRED; exit stop is predicted_bin_law).
# Purpose: Lock the SURVIVING HoldValue exit-cost contract (fee only) and the
#   config-getter bounds. The retired T6.4 machinery (compute_with_exit_costs
#   time_cost/correlation, _buy_yes_exit/_buy_no_exit integration) is gone with
#   the evaluate_exit collapse and is intentionally no longer covered here.
# Reuse: Run when HoldValue fee accounting, exit fee/hurdle config getters, or
#   the (dead-but-present) correlation-crowding helper change.
"""Tests for the HoldValue exit-cost contract after the PR-1 离场律 collapse.

compute_with_exit_costs now carries fee as the only forward friction; time cost
is always 0.0 (the static daily hurdle and correlation surcharge are retired and
replaced by the PR-2 allocator ΔJ term). The exit decision itself is made by
src/decision/predicted_bin_law.exit_decision, not HoldValue.
"""
from __future__ import annotations

import pytest

from src.contracts.hold_value import HoldValue, HoldValueCostDeclarationError


class TestComputeWithExitCosts:
    """Fee-only factory arithmetic (unit-level)."""

    def test_fee_cost_uses_polymarket_formula(self):
        """fee_cost = shares × polymarket_fee(best_bid, fee_rate),
        polymarket_fee = fee_rate × p × (1-p)."""
        shares = 100.0
        best_bid = 0.55
        fee_rate = 0.05
        expected_fee = 100.0 * 0.05 * 0.55 * 0.45

        hv = HoldValue.compute_with_exit_costs(
            shares=shares,
            current_p_posterior=0.60,
            best_bid=best_bid,
            fee_rate=fee_rate,
        )
        assert hv.fee_cost == pytest.approx(expected_fee, abs=1e-9)

    def test_time_cost_always_zero(self):
        """PR-1: the static daily-hurdle time cost is retired — always 0.0."""
        hv = HoldValue.compute_with_exit_costs(
            shares=200.0,
            current_p_posterior=0.7,
            best_bid=0.5,
            fee_rate=0.05,
        )
        assert hv.time_cost == 0.0

    def test_correlation_crowding_not_declared(self):
        """PR-1: the correlation-crowding surcharge is retired — never declared."""
        hv = HoldValue.compute_with_exit_costs(
            shares=100.0,
            current_p_posterior=0.6,
            best_bid=0.55,
            fee_rate=0.05,
        )
        assert "fee" in hv.costs_declared
        assert "time" in hv.costs_declared
        assert "correlation_crowding" not in hv.costs_declared
        assert hv.extra_costs_total == 0.0

    def test_net_value_equals_gross_minus_fee(self):
        """net_value = gross − fee (time and extras are zero)."""
        hv = HoldValue.compute_with_exit_costs(
            shares=150.0,
            current_p_posterior=0.65,
            best_bid=0.60,
            fee_rate=0.05,
        )
        assert hv.gross_value == pytest.approx(150.0 * 0.65)
        assert hv.net_value == pytest.approx(hv.gross_value - hv.fee_cost, abs=1e-9)

    def test_extreme_bid_does_not_raise(self):
        """A bid at {0.0, 1.0} is clamped so polymarket_fee stays finite."""
        for bid in (0.0, 1.0):
            hv = HoldValue.compute_with_exit_costs(
                shares=100.0,
                current_p_posterior=0.5,
                best_bid=bid,
                fee_rate=0.05,
            )
            assert hv is not None
            assert hv.fee_cost >= 0.0


class TestHoldValueContract:
    """Surviving HoldValue base contract (used by the fee-only factory)."""

    def test_requires_fee_and_time_declarations(self):
        with pytest.raises(HoldValueCostDeclarationError):
            HoldValue(
                gross_value=10.0,
                fee_cost=0.0,
                time_cost=0.0,
                net_value=10.0,
                costs_declared=[],
            )

    def test_zero_cost_compute_declares_fee_and_time(self):
        hv = HoldValue.compute(gross_value=60.0, fee_cost=0.0, time_cost=0.0)
        assert hv.fee_cost == 0.0
        assert hv.time_cost == 0.0
        assert hv.net_value == hv.gross_value
        assert hv.costs_declared == ["fee", "time"]


class TestExitCostConfigBounds:
    """Config getter bounds that still guard operator misconfiguration."""

    def test_exit_fee_rate_bounds_validation(self):
        from src import config as config_mod

        original = config_mod.settings["exit"]["fee_rate"]
        try:
            config_mod.settings["exit"]["fee_rate"] = 0.5
            with pytest.raises(ValueError, match="exit.fee_rate"):
                config_mod.exit_fee_rate()
        finally:
            config_mod.settings["exit"]["fee_rate"] = original

    def test_exit_daily_hurdle_rate_bounds_validation(self):
        """The daily-hurdle config getter still guards operator misconfiguration
        even though the hurdle no longer feeds the HoldValue cost model."""
        from src import config as config_mod

        original = config_mod.settings["exit"]["daily_hurdle_rate"]
        try:
            config_mod.settings["exit"]["daily_hurdle_rate"] = 0.1
            with pytest.raises(ValueError, match="exit.daily_hurdle_rate"):
                config_mod.exit_daily_hurdle_rate()
        finally:
            config_mod.settings["exit"]["daily_hurdle_rate"] = original

    def test_fee_rate_config_matches_polymarket_fee_default(self):
        """Two sources of truth for fee_rate (config vs polymarket_fee default)
        must not drift."""
        import inspect

        from src.contracts.execution_price import polymarket_fee
        from src.config import exit_fee_rate

        sig = inspect.signature(polymarket_fee)
        polymarket_default = sig.parameters["fee_rate"].default
        assert polymarket_default == exit_fee_rate()


class TestBuildExitContextPlumbing:
    """_build_exit_context self-exclusion and portfolio-kwarg handling. These
    survive the 离场律 collapse (the correlation-crowding surcharge they once fed
    is retired, but the context plumbing is still live)."""

    def _make_position(self, direction: str = "buy_yes", trade_id: str = "pos_self"):
        from src.state.portfolio import Position

        return Position(
            trade_id=trade_id,
            market_id="m_" + trade_id,
            city="Chicago",
            cluster="Chicago",
            target_date="2026-04-25",
            bin_label="60-61°F",
            direction=direction,
            entry_price=0.40 if direction == "buy_yes" else 0.15,
            size_usd=50.0,
            entry_method="calibrated",
        )

    def test_build_exit_context_excludes_self_from_portfolio_positions(self):
        """_build_exit_context must filter self out of the portfolio_positions
        tuple so the helper doesn't double-count this position's own exposure."""
        from src.engine.cycle_runtime import _build_exit_context
        from src.state.portfolio import ExitContext, Position, PortfolioState
        from types import SimpleNamespace

        pos_self = self._make_position("buy_yes", trade_id="pos_self")
        pos_other = Position(
            trade_id="pos_other",
            market_id="m_other",
            city="Houston",
            cluster="Houston",
            target_date="2026-04-25",
            bin_label="70-71°F",
            direction="buy_yes",
            entry_price=0.40,
            size_usd=100.0,
            entry_method="calibrated",
        )
        portfolio = PortfolioState(bankroll=200.0, positions=[pos_self, pos_other])

        edge_ctx = SimpleNamespace(
            p_posterior=0.55,
            p_market=[0.50],
            divergence_score=0.0,
            market_velocity_1h=0.0,
        )
        pos_self.last_monitor_prob_is_fresh = True
        pos_self.last_monitor_market_price_is_fresh = True
        pos_self.last_monitor_best_bid = 0.48
        pos_self.last_monitor_best_ask = 0.52
        pos_self.last_monitor_market_vig = 1.0
        pos_self.last_monitor_whale_toxicity = False
        pos_self.chain_state = "synced"

        ctx = _build_exit_context(
            pos_self,
            edge_ctx,
            hours_to_settlement=48.0,
            ExitContext=ExitContext,
            portfolio=portfolio,
        )
        assert len(ctx.portfolio_positions) == 1
        cluster, size_usd, trade_id = ctx.portfolio_positions[0]
        assert trade_id == "pos_other"
        assert cluster == "Houston"
        assert size_usd == 100.0
        assert ctx.bankroll == 200.0

    def test_build_exit_context_portfolio_none_produces_empty_tuple(self):
        """Callers not passing the portfolio kwarg get an empty tuple + None
        bankroll so the downstream helper safely returns zero cost."""
        from src.engine.cycle_runtime import _build_exit_context
        from src.state.portfolio import ExitContext
        from types import SimpleNamespace

        pos = self._make_position("buy_yes", trade_id="pos_self")
        edge_ctx = SimpleNamespace(
            p_posterior=0.55,
            p_market=[0.50],
            divergence_score=0.0,
            market_velocity_1h=0.0,
        )
        pos.last_monitor_prob_is_fresh = True
        pos.last_monitor_market_price_is_fresh = True
        pos.last_monitor_best_bid = 0.48
        pos.last_monitor_best_ask = 0.52
        pos.last_monitor_market_vig = 1.0
        pos.last_monitor_whale_toxicity = False
        pos.chain_state = "synced"

        ctx = _build_exit_context(
            pos,
            edge_ctx,
            hours_to_settlement=48.0,
            ExitContext=ExitContext,
        )
        assert ctx.portfolio_positions == ()
        assert ctx.bankroll is None
