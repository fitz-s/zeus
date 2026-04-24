# Created: 2026-04-24
# Last reused/audited: 2026-04-24
# Authority basis: T6.4 HoldValue fee + time cost wiring into exit-decision seam (midstream fix plan 2026-04-23)
"""Tests for T6.4 — HoldValue.compute_with_exit_costs factory + exit-path
fee/time cost integration under feature_flags.HOLD_VALUE_EXIT_COSTS.

Covers:
- HoldValue.compute_with_exit_costs factory arithmetic (fee via polymarket_fee,
  time via daily_hurdle * hours/24 * capital_locked)
- hours_to_settlement=None collapses time_cost to 0.0 soft default
- correlation_crowding=0.0 is no-op (phase2 hook reserved)
- costs_declared list reflects actual cost categories
- Regression: flag-OFF path preserves pre-T6.4 zero-cost HoldValue.compute
  semantics exactly (delta = 0 on representative fixtures)
- plan-premise correction #21: _buy_no_exit previously bypassed HoldValue;
  T6.4 brings it through the contract under flag parity.
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from src.contracts.hold_value import HoldValue, HoldValueCostDeclarationError


class TestComputeWithExitCosts:
    """Factory arithmetic tests (unit-level)."""

    def test_fee_cost_uses_polymarket_formula(self):
        """fee_cost = shares × polymarket_fee(best_bid, fee_rate)
        polymarket_fee formula is fee_rate × p × (1-p).
        """
        shares = 100.0
        current_p_posterior = 0.60
        best_bid = 0.55
        fee_rate = 0.05
        # Expected: fee_per_share = 0.05 × 0.55 × 0.45 = 0.012375
        #           fee_cost = 100 × 0.012375 = 1.2375
        expected_fee = 100.0 * 0.05 * 0.55 * 0.45

        hv = HoldValue.compute_with_exit_costs(
            shares=shares,
            current_p_posterior=current_p_posterior,
            best_bid=best_bid,
            hours_to_settlement=0.0,  # isolate fee from time
            fee_rate=fee_rate,
            daily_hurdle_rate=0.0001,
        )
        assert hv.fee_cost == pytest.approx(expected_fee, abs=1e-9)

    def test_time_cost_scales_with_hours_and_hurdle(self):
        """time_cost = shares × best_bid × (hours / 24) × daily_hurdle_rate"""
        shares = 200.0
        current_p_posterior = 0.7
        best_bid = 0.5
        hours = 48.0  # 2 days
        daily_hurdle = 0.001  # 0.1%/day
        # Expected: 200 × 0.5 × (48/24) × 0.001 = 200 × 0.5 × 2 × 0.001 = 0.2
        expected_time = 200.0 * 0.5 * 2.0 * 0.001

        hv = HoldValue.compute_with_exit_costs(
            shares=shares,
            current_p_posterior=current_p_posterior,
            best_bid=best_bid,
            hours_to_settlement=hours,
            fee_rate=0.0,  # isolate time from fee
            daily_hurdle_rate=daily_hurdle,
        )
        assert hv.time_cost == pytest.approx(expected_time, abs=1e-9)

    def test_hours_to_settlement_none_collapses_time_cost_to_zero(self):
        """hours_to_settlement=None → time_cost=0.0 (soft default;
        authority failures surface via INCOMPLETE_EXIT_CONTEXT upstream)."""
        hv = HoldValue.compute_with_exit_costs(
            shares=100.0,
            current_p_posterior=0.5,
            best_bid=0.5,
            hours_to_settlement=None,
            fee_rate=0.05,
            daily_hurdle_rate=0.01,  # would produce non-trivial time_cost if hours were known
        )
        assert hv.time_cost == 0.0

    def test_negative_hours_treated_as_none(self):
        """Defensive: past-settlement hours (would be negative if clock
        drifted) should not produce negative time_cost."""
        hv = HoldValue.compute_with_exit_costs(
            shares=100.0,
            current_p_posterior=0.5,
            best_bid=0.5,
            hours_to_settlement=-1.0,
            fee_rate=0.05,
            daily_hurdle_rate=0.01,
        )
        assert hv.time_cost == 0.0

    def test_correlation_crowding_default_zero_not_declared(self):
        """correlation_crowding=0.0 (T6.4-phase2 hook) leaves costs_declared
        without the 'correlation_crowding' entry — only fee+time."""
        hv = HoldValue.compute_with_exit_costs(
            shares=100.0,
            current_p_posterior=0.6,
            best_bid=0.55,
            hours_to_settlement=24.0,
            fee_rate=0.05,
            daily_hurdle_rate=0.0001,
        )
        assert "fee" in hv.costs_declared
        assert "time" in hv.costs_declared
        assert "correlation_crowding" not in hv.costs_declared

    def test_correlation_crowding_nonzero_is_recorded(self):
        """When T6.4-phase2 wires real portfolio-context correlation
        crowding, passing it flows through to net_value + costs_declared."""
        hv = HoldValue.compute_with_exit_costs(
            shares=100.0,
            current_p_posterior=0.6,
            best_bid=0.55,
            hours_to_settlement=24.0,
            fee_rate=0.05,
            daily_hurdle_rate=0.0001,
            correlation_crowding=0.15,
        )
        assert "correlation_crowding" in hv.costs_declared
        # net_value should reflect the extra cost deduction
        expected_extra_deduction = 0.15
        assert hv.net_value == pytest.approx(
            hv.gross_value - hv.fee_cost - hv.time_cost - expected_extra_deduction,
            abs=1e-9,
        )

    def test_net_value_equals_gross_minus_all_costs(self):
        """Sanity: net_value must equal arithmetic expectation.
        HoldValue.__post_init__ enforces this invariant."""
        hv = HoldValue.compute_with_exit_costs(
            shares=150.0,
            current_p_posterior=0.65,
            best_bid=0.60,
            hours_to_settlement=12.0,
            fee_rate=0.05,
            daily_hurdle_rate=0.0001,
        )
        expected_gross = 150.0 * 0.65
        assert hv.gross_value == pytest.approx(expected_gross)
        assert hv.net_value == pytest.approx(
            hv.gross_value - hv.fee_cost - hv.time_cost,
            abs=1e-9,
        )


class TestFlagOffPreservesPreT64Semantics:
    """Regression guard: when feature_flags.HOLD_VALUE_EXIT_COSTS is OFF
    (default), the _buy_yes_exit / _buy_no_exit paths must produce HoldValue
    records IDENTICAL to pre-T6.4 (fee=0, time=0, costs_declared=['fee','time'],
    net_value=gross_value).
    """

    def test_zero_cost_compute_matches_legacy_call_shape(self):
        """The fallback HoldValue.compute(gross, 0.0, 0.0) call shape the
        four portfolio.py sites use when flag is OFF produces the same
        HoldValue record as pre-T6.4 code."""
        shares = 100.0
        p_posterior = 0.6
        hv = HoldValue.compute(
            gross_value=shares * p_posterior,
            fee_cost=0.0,
            time_cost=0.0,
        )
        assert hv.fee_cost == 0.0
        assert hv.time_cost == 0.0
        assert hv.net_value == hv.gross_value
        assert hv.costs_declared == ["fee", "time"]


class TestPortfolioExitIntegration:
    """Integration: verify the 4 wire-in sites in src/state/portfolio.py
    respond correctly to the feature flag. These tests use direct Position
    construction + _buy_yes_exit / _buy_no_exit calls.
    """

    def _make_position(self, direction: str = "buy_yes"):
        from src.state.portfolio import Position

        # Minimal Position constructor — fields needed for _buy_yes_exit
        # / _buy_no_exit EV gate paths.
        pos = Position(
            trade_id="test_trade_t64",
            market_id="test_market",
            city="Chicago",
            cluster="midwest",
            target_date="2026-04-25",
            bin_label="60-61°F",
            direction=direction,
            entry_price=0.40 if direction == "buy_yes" else 0.15,
            size_usd=50.0,
            entry_method="calibrated",
        )
        # neg_edge_count=2 so the _buy_yes_exit / _buy_no_exit consecutive
        # gate triggers on first below-threshold call.
        pos.neg_edge_count = 2
        return pos

    def test_flag_off_buy_yes_exit_uses_zero_cost(self):
        """With flag OFF, _buy_yes_exit EV gate should match pre-T6.4 behavior
        (no fee/time cost applied). The applied_validations list should NOT
        include 'hold_value_exit_costs_enabled'."""
        pos = self._make_position("buy_yes")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=False):
            decision = pos._buy_yes_exit(
                forward_edge=-0.05,
                current_p_posterior=0.55,
                best_bid=0.50,
                day0_active=False,
                hours_to_settlement=48.0,
                applied=[],
            )
        assert "hold_value_exit_costs_enabled" not in decision.applied_validations

    def test_flag_on_buy_yes_exit_records_cost_awareness(self):
        """With flag ON, _buy_yes_exit records the 'hold_value_exit_costs_enabled'
        breadcrumb in applied_validations, proving the factory was routed."""
        pos = self._make_position("buy_yes")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            decision = pos._buy_yes_exit(
                forward_edge=-0.05,
                current_p_posterior=0.55,
                best_bid=0.50,
                day0_active=False,
                hours_to_settlement=48.0,
                applied=[],
            )
        assert "hold_value_exit_costs_enabled" in decision.applied_validations

    def test_flag_off_buy_no_exit_uses_zero_cost(self):
        """Plan-premise correction #21 scope: pre-T6.4, _buy_no_exit bypassed
        HoldValue entirely. T6.4 brings it through the contract but preserves
        behavior when flag is OFF — no 'hold_value_exit_costs_enabled' breadcrumb."""
        pos = self._make_position("buy_no")
        pos.neg_edge_count = 2  # trigger consecutive gate on this call
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=False):
            decision = pos._buy_no_exit(
                forward_edge=-0.05,
                current_p_posterior=0.85,
                current_market_price=0.80,
                hours_to_settlement=48.0,
                day0_active=False,
                applied=[],
            )
        assert "hold_value_exit_costs_enabled" not in decision.applied_validations

    def test_flag_on_buy_no_exit_records_cost_awareness(self):
        """T6.4 contract-consistency fix: _buy_no_exit now goes through
        HoldValue under flag ON, parity with _buy_yes_exit."""
        pos = self._make_position("buy_no")
        pos.neg_edge_count = 2
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            decision = pos._buy_no_exit(
                forward_edge=-0.05,
                current_p_posterior=0.85,
                current_market_price=0.80,
                hours_to_settlement=48.0,
                day0_active=False,
                applied=[],
            )
        assert "hold_value_exit_costs_enabled" in decision.applied_validations

    def test_flag_on_buy_yes_day0_exit_records_cost_awareness(self):
        """T6.4-hardening (surrogate HIGH finding): Day0 EV-gate site at
        portfolio.py:545 was claimed wire-in but had zero integration test.
        This test exercises that path to prove flag-ON routes through the
        cost-aware factory when day0_active=True.
        """
        pos = self._make_position("buy_yes")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            decision = pos._buy_yes_exit(
                forward_edge=-0.10,  # below edge_threshold, triggers Day0 gate
                current_p_posterior=0.55,
                best_bid=0.50,
                day0_active=True,
                hours_to_settlement=12.0,
                applied=[],
            )
        assert "hold_value_exit_costs_enabled" in decision.applied_validations
        # Day0 gate path must tag day0_observation_gate breadcrumb too
        assert "day0_observation_gate" in decision.applied_validations

    def test_flag_on_buy_no_day0_exit_records_cost_awareness(self):
        """T6.4-hardening (surrogate HIGH finding): Day0 EV-gate site at
        portfolio.py:684 (_buy_no_exit Day0 branch) proven wired under
        flag ON via integration path, not just factory-level assertion.
        """
        pos = self._make_position("buy_no")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            decision = pos._buy_no_exit(
                forward_edge=-0.10,
                current_p_posterior=0.85,
                current_market_price=0.80,
                hours_to_settlement=12.0,
                day0_active=True,
                applied=[],
            )
        assert "hold_value_exit_costs_enabled" in decision.applied_validations
        assert "day0_observation_gate" in decision.applied_validations

    def test_flag_on_extreme_best_bid_does_not_crash(self):
        """T6.4-hardening (surrogate HIGH finding): pre-fix, best_bid ∈
        {0.0, 1.0} would hit polymarket_fee ValueError and be caught by
        the cycle_runtime except-all, silently converting should_exit=True
        into monitor_failed state. Post-fix: factory clamps to (EPS, 1-EPS)
        so extreme prices are handled gracefully.

        This test proves the crash path is closed for both boundary values.
        """
        # Trigger Day0 path which has the best_bid proxy `max(0, market*0.95)`
        # that can legally produce 0.0.
        pos = self._make_position("buy_yes")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            # Extreme price: best_bid=0.0 previously crashed via polymarket_fee
            decision_zero = pos._buy_yes_exit(
                forward_edge=-0.10,
                current_p_posterior=0.55,
                best_bid=0.0,
                day0_active=True,
                hours_to_settlement=12.0,
                applied=[],
            )
            # Does not crash — returns a valid ExitDecision
            assert decision_zero is not None
            assert "hold_value_exit_costs_enabled" in decision_zero.applied_validations

        pos2 = self._make_position("buy_no")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            # Extreme upper: current_market_price=1.0 previously crashed
            decision_one = pos2._buy_no_exit(
                forward_edge=-0.10,
                current_p_posterior=0.85,
                current_market_price=1.0,
                hours_to_settlement=12.0,
                day0_active=True,
                applied=[],
            )
            assert decision_one is not None
            assert "hold_value_exit_costs_enabled" in decision_one.applied_validations


class TestHardeningValidators:
    """T6.4-hardening from surrogate review: factory-level + config
    getter bounds validations that close silent-failure categories."""

    def test_negative_correlation_crowding_raises(self):
        """Surrogate MEDIUM finding: negative correlation_crowding was
        silently dropped pre-hardening. T6.4-phase2 wiring may inherit
        sign bugs upstream; reject at factory boundary."""
        with pytest.raises(ValueError, match="correlation_crowding must be >= 0"):
            HoldValue.compute_with_exit_costs(
                shares=100.0,
                current_p_posterior=0.6,
                best_bid=0.55,
                hours_to_settlement=24.0,
                fee_rate=0.05,
                daily_hurdle_rate=0.0001,
                correlation_crowding=-0.05,
            )

    def test_zero_correlation_crowding_accepted(self):
        """Regression: default 0.0 is still accepted (no-op phase2 hook)."""
        hv = HoldValue.compute_with_exit_costs(
            shares=100.0,
            current_p_posterior=0.6,
            best_bid=0.55,
            hours_to_settlement=24.0,
            fee_rate=0.05,
            daily_hurdle_rate=0.0001,
            correlation_crowding=0.0,
        )
        assert hv.extra_costs_total == 0.0
        assert "correlation_crowding" not in hv.costs_declared

    def test_exit_fee_rate_bounds_validation(self):
        """Surrogate MEDIUM finding: operator misconfiguration catch."""
        import copy
        from src import config as config_mod

        original = config_mod.settings["exit"]["fee_rate"]
        try:
            # Bogus rate outside [0, 0.1]
            config_mod.settings["exit"]["fee_rate"] = 0.5
            with pytest.raises(ValueError, match="exit.fee_rate"):
                config_mod.exit_fee_rate()
        finally:
            config_mod.settings["exit"]["fee_rate"] = original

    def test_exit_daily_hurdle_rate_bounds_validation(self):
        """Surrogate MEDIUM finding: operator misconfiguration catch."""
        from src import config as config_mod

        original = config_mod.settings["exit"]["daily_hurdle_rate"]
        try:
            config_mod.settings["exit"]["daily_hurdle_rate"] = 0.1
            with pytest.raises(ValueError, match="exit.daily_hurdle_rate"):
                config_mod.exit_daily_hurdle_rate()
        finally:
            config_mod.settings["exit"]["daily_hurdle_rate"] = original


class TestConNyxPostEditHardening:
    """Post-edit hardening for con-nyx findings (b), (c), (d), (h), (i).
    Additional safety guards before operator flips HOLD_VALUE_EXIT_COSTS.
    """

    def _make_position(self, direction: str = "buy_yes"):
        from src.state.portfolio import Position

        pos = Position(
            trade_id="test_trade_t64_hardening",
            market_id="test_market",
            city="Chicago",
            cluster="midwest",
            target_date="2026-04-25",
            bin_label="60-61°F",
            direction=direction,
            entry_price=0.40 if direction == "buy_yes" else 0.15,
            size_usd=50.0,
            entry_method="calibrated",
        )
        pos.neg_edge_count = 2
        return pos

    def test_flag_on_hours_none_emits_authority_gap_breadcrumb(self):
        """Con-nyx finding (c): when flag ON and hours_to_settlement is
        None, time_cost silently collapses to 0.0. Surface the authority
        gap via applied_validations breadcrumb so monitor summaries can
        count these D6-bypass occurrences.
        """
        pos = self._make_position("buy_yes")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            decision = pos._buy_yes_exit(
                forward_edge=-0.05,
                current_p_posterior=0.55,
                best_bid=0.50,
                day0_active=False,
                hours_to_settlement=None,  # authority gap
                applied=[],
            )
        assert "hold_value_exit_costs_enabled" in decision.applied_validations
        assert "hold_value_hours_unknown_time_cost_zero" in decision.applied_validations

    def test_flag_on_hours_none_buy_no_emits_breadcrumb(self):
        """Finding (c) for buy_no path: hours=None under flag ON also
        surfaces the authority gap."""
        pos = self._make_position("buy_no")
        pos.neg_edge_count = 2
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            decision = pos._buy_no_exit(
                forward_edge=-0.05,
                current_p_posterior=0.85,
                current_market_price=0.80,
                hours_to_settlement=None,
                day0_active=False,
                applied=[],
            )
        assert "hold_value_hours_unknown_time_cost_zero" in decision.applied_validations

    def test_flag_on_hours_positive_does_not_emit_unknown_breadcrumb(self):
        """Regression: breadcrumb MUST NOT fire when hours is valid
        (non-None, non-negative). Otherwise it becomes a noisy spam
        indistinguishable from real authority gaps."""
        pos = self._make_position("buy_yes")
        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True):
            decision = pos._buy_yes_exit(
                forward_edge=-0.05,
                current_p_posterior=0.55,
                best_bid=0.50,
                day0_active=False,
                hours_to_settlement=48.0,
                applied=[],
            )
        assert "hold_value_hours_unknown_time_cost_zero" not in decision.applied_validations

    def test_flag_off_parity_with_pre_t64_bare_math(self):
        """Con-nyx finding (d) belt-and-suspenders: flag-OFF path must
        produce the SAME ExitDecision as the pre-T6.4 bare-math formula
        for identical inputs. This test computes the bare-math expected
        decision inline and asserts the wired path matches.
        """
        pos = self._make_position("buy_yes")
        # Inputs: shares * best_bid vs shares * p_posterior comparison.
        shares = pos.size_usd / pos.entry_price  # 50 / 0.40 = 125
        p_posterior = 0.50
        best_bid = 0.48
        # Pre-T6.4 logic: sell_value <= hold_gross → don't exit
        # sell_value = 125 * 0.48 = 60; hold_gross = 125 * 0.50 = 62.5
        # 60 <= 62.5 → don't exit → should_exit=False
        expected_should_exit = not (shares * best_bid <= shares * p_posterior)

        with patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=False):
            decision = pos._buy_yes_exit(
                forward_edge=-0.02,
                current_p_posterior=p_posterior,
                best_bid=best_bid,
                day0_active=False,
                hours_to_settlement=24.0,
                applied=[],
            )
        # Pre-T6.4 bare math says don't exit (60 < 62.5); flag-OFF must agree.
        # Note: decision.should_exit depends on all the EV gate layers, not
        # just the hold-value test; this asserts the hold-value gate
        # specifically by checking it returned False (held) not True (exit).
        if expected_should_exit is False:
            assert decision.should_exit is False, (
                f"Flag-OFF should match pre-T6.4 bare math. "
                f"Pre-T6.4 said hold (sell={shares*best_bid:.2f} <= "
                f"hold={shares*p_posterior:.2f}), got should_exit={decision.should_exit}"
            )

    def test_fee_rate_config_matches_polymarket_fee_default(self):
        """Con-nyx finding (i) — two sources of truth for fee_rate:
        config/settings.json exit.fee_rate AND src/contracts/execution_price.py
        polymarket_fee(price, fee_rate=0.05) default. If polymarket_fee
        default drifts, config may go stale. This test locks the coupling
        so drift is caught at CI time.
        """
        import inspect

        from src.contracts.execution_price import polymarket_fee
        from src.config import exit_fee_rate

        sig = inspect.signature(polymarket_fee)
        polymarket_default = sig.parameters["fee_rate"].default
        config_rate = exit_fee_rate()
        assert polymarket_default == config_rate, (
            f"Drift detected: polymarket_fee default fee_rate={polymarket_default} "
            f"but config exit.fee_rate={config_rate}. Either update the other "
            f"to match or introduce a single-source getter to eliminate drift."
        )


class TestCorrelationCrowdingPhase2:
    """T6.4-phase2: correlation-crowding cost wired through ExitContext
    into HoldValue.compute_with_exit_costs. Feature-flag-gated via
    HOLD_VALUE_EXIT_COSTS (ON → computed; OFF → untouched) + rate-gated
    via exit.correlation_crowding_rate (default 0.0 = no-op even when
    feature flag ON, so phase2 shipping doesn't alter live behavior
    without operator config flip).
    """

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

    def test_compute_helper_zero_when_rate_zero(self):
        from src.state.portfolio import _compute_exit_correlation_crowding

        cost = _compute_exit_correlation_crowding(
            this_cluster="Chicago",
            portfolio_positions=(("Houston", 100.0, "other_pos"),),
            bankroll=200.0,
            shares=125.0,
            best_bid=0.48,
            crowding_rate=0.0,  # feature disabled
        )
        assert cost == 0.0

    def test_compute_helper_zero_when_no_other_positions(self):
        from src.state.portfolio import _compute_exit_correlation_crowding

        cost = _compute_exit_correlation_crowding(
            this_cluster="Chicago",
            portfolio_positions=(),
            bankroll=200.0,
            shares=125.0,
            best_bid=0.48,
            crowding_rate=0.01,
        )
        assert cost == 0.0

    def test_compute_helper_zero_when_bankroll_missing(self):
        from src.state.portfolio import _compute_exit_correlation_crowding

        cost = _compute_exit_correlation_crowding(
            this_cluster="Chicago",
            portfolio_positions=(("Houston", 100.0, "other_pos"),),
            bankroll=None,
            shares=125.0,
            best_bid=0.48,
            crowding_rate=0.01,
        )
        assert cost == 0.0

    def test_compute_helper_nonzero_when_all_inputs_present(self):
        """Golden-path arithmetic test with a known correlation value."""
        from unittest.mock import patch

        from src.state.portfolio import _compute_exit_correlation_crowding

        # Stub get_correlation so the test doesn't depend on live matrix
        # values (which may drift as correlation data is refreshed).
        with patch("src.state.portfolio.get_correlation", return_value=0.80):
            cost = _compute_exit_correlation_crowding(
                this_cluster="Chicago",
                portfolio_positions=(("Houston", 100.0, "other_pos"),),
                bankroll=200.0,
                shares=125.0,
                best_bid=0.48,
                crowding_rate=0.01,
            )
        # Expected: rate × exposure_ratio × shares × best_bid
        # exposure_ratio = (100/200) × 0.80 = 0.40
        # cost = 0.01 × 0.40 × 125 × 0.48 = 0.24
        expected = 0.01 * (100.0 / 200.0) * 0.80 * 125.0 * 0.48
        assert cost == pytest.approx(expected, abs=1e-9)

    def test_compute_helper_excludes_self_via_tuple_filter(self):
        """Regression: _build_exit_context is supposed to have pre-excluded
        self via trade_id filter. This helper does NOT re-filter — it sums
        across whatever tuple it receives. Documenting the contract via
        test: if self somehow reaches this helper, its contribution flows
        through. Caller responsibility (build_exit_context) to exclude."""
        from unittest.mock import patch

        from src.state.portfolio import _compute_exit_correlation_crowding

        # Self accidentally left in the tuple — helper sums it regardless
        with patch("src.state.portfolio.get_correlation", return_value=1.0):
            cost_with_self = _compute_exit_correlation_crowding(
                this_cluster="Chicago",
                portfolio_positions=(("Chicago", 50.0, "pos_self"),),  # self!
                bankroll=200.0,
                shares=125.0,
                best_bid=0.48,
                crowding_rate=0.01,
            )
        # Helper trusts caller to filter — sums the 50.0 exposure × 1.0 corr.
        # This documents the contract; actual filter happens at _build_exit_context.
        assert cost_with_self > 0.0

    def test_compute_helper_skips_malformed_entry(self):
        from unittest.mock import patch

        from src.state.portfolio import _compute_exit_correlation_crowding

        # Mixed valid + malformed entries — malformed should be skipped, not crash
        with patch("src.state.portfolio.get_correlation", return_value=0.5):
            cost = _compute_exit_correlation_crowding(
                this_cluster="Chicago",
                portfolio_positions=(
                    ("Houston", 100.0, "other_pos"),  # valid
                    ("malformed",),  # wrong arity — skipped
                ),
                bankroll=200.0,
                shares=125.0,
                best_bid=0.48,
                crowding_rate=0.01,
            )
        # Only valid entry contributes
        expected = 0.01 * (100.0 / 200.0) * 0.5 * 125.0 * 0.48
        assert cost == pytest.approx(expected, abs=1e-9)

    def test_exit_correlation_crowding_rate_bounds_validation(self):
        """Bounds guard: operator misconfig (rate > 0.1) must raise."""
        from src import config as config_mod

        original = config_mod.settings["exit"]["correlation_crowding_rate"]
        try:
            config_mod.settings["exit"]["correlation_crowding_rate"] = 0.5
            with pytest.raises(ValueError, match="exit.correlation_crowding_rate"):
                config_mod.exit_correlation_crowding_rate()
        finally:
            config_mod.settings["exit"]["correlation_crowding_rate"] = original

    def test_exit_correlation_crowding_rate_default_is_zero(self):
        """Phase2 default 0.0 preserves pre-phase2 behavior when shipped."""
        from src.config import exit_correlation_crowding_rate

        assert exit_correlation_crowding_rate() == 0.0, (
            "phase2 shipping must NOT alter live behavior — default rate "
            "must be 0.0 until operator flips post-replay-audit."
        )

    def test_flag_on_crowding_breadcrumb_when_rate_positive(self):
        """Integration: under flag ON + rate > 0 + portfolio has co-held
        positions + bankroll, exit path appends hold_value_correlation_
        crowding_applied to applied_validations."""
        from unittest.mock import patch

        from src.state.portfolio import Position

        pos = self._make_position("buy_yes", trade_id="pos_self")
        other = Position(
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
        pos.neg_edge_count = 2

        patches = [
            patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True),
            patch("src.state.portfolio.exit_correlation_crowding_rate", return_value=0.01),
            patch("src.state.portfolio.get_correlation", return_value=0.80),
        ]
        for p in patches:
            p.start()
        try:
            decision = pos._buy_yes_exit(
                forward_edge=-0.05,
                current_p_posterior=0.55,
                best_bid=0.50,
                day0_active=False,
                hours_to_settlement=48.0,
                applied=[],
                portfolio_positions=(("Houston", 100.0, "pos_other"),),
                bankroll=200.0,
            )
        finally:
            for p in patches:
                p.stop()

        assert "hold_value_correlation_crowding_applied" in decision.applied_validations

    def test_flag_on_no_crowding_breadcrumb_when_rate_zero(self):
        """Regression: default rate=0.0 means crowding cost is 0.0 even
        when portfolio has co-held positions — breadcrumb must NOT fire
        (otherwise phase2 noisily breadcrumbs every decision when rate is
        still at default)."""
        from unittest.mock import patch

        pos = self._make_position("buy_yes", trade_id="pos_self")
        pos.neg_edge_count = 2

        patches = [
            patch("src.state.portfolio.hold_value_exit_costs_enabled", return_value=True),
            # rate stays at default 0.0
        ]
        for p in patches:
            p.start()
        try:
            decision = pos._buy_yes_exit(
                forward_edge=-0.05,
                current_p_posterior=0.55,
                best_bid=0.50,
                day0_active=False,
                hours_to_settlement=48.0,
                applied=[],
                portfolio_positions=(("Houston", 100.0, "pos_other"),),
                bankroll=200.0,
            )
        finally:
            for p in patches:
                p.stop()

        assert "hold_value_correlation_crowding_applied" not in decision.applied_validations

    def test_build_exit_context_excludes_self_from_portfolio_positions(self):
        """Plumbing: _build_exit_context must filter self out of the
        portfolio_positions tuple so the helper doesn't double-count
        this position's own exposure."""
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

        # Minimal edge_ctx stub — fields read by _build_exit_context
        edge_ctx = SimpleNamespace(
            p_posterior=0.55,
            p_market=[0.50],
            divergence_score=0.0,
            market_velocity_1h=0.0,
        )
        # Minimal monitor fields on pos_self
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
        # Should contain ONLY the other position; self must be filtered.
        assert len(ctx.portfolio_positions) == 1
        cluster, size_usd, trade_id = ctx.portfolio_positions[0]
        assert trade_id == "pos_other"
        assert cluster == "Houston"
        assert size_usd == 100.0
        assert ctx.bankroll == 200.0

    def test_build_exit_context_portfolio_none_produces_empty_tuple(self):
        """Backward-compat: callers not yet passing portfolio kwarg get
        empty tuple + None bankroll; downstream helper safely returns 0
        cost. Preserves pre-phase2 behavior for untouched callsites."""
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
            # portfolio kwarg omitted — pre-phase2 call shape
        )
        assert ctx.portfolio_positions == ()
        assert ctx.bankroll is None
