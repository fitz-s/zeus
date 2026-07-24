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

    def test_fee_rate_config_matches_polymarket_fee_default(self):
        """Two sources of truth for fee_rate (config vs polymarket_fee default)
        must not drift."""
        import inspect

        from src.contracts.execution_price import polymarket_fee
        from src.config import exit_fee_rate

        sig = inspect.signature(polymarket_fee)
        polymarket_default = sig.parameters["fee_rate"].default
        assert polymarket_default == exit_fee_rate()
