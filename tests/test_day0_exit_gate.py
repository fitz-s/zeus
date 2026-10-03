# Created: 2026-04-07
# Last reused/audited: 2026-10-03
# Lifecycle: created=2026-04-07; last_reviewed=2026-10-03; last_reused=2026-10-03
# Purpose: Lock current Day0 held-side mean, confidence and executable-depth exit authority.
# Reuse: Run when Day0 exit probability, CI, bid, or monitor authority changes.
# Authority basis: docs/operations/current/finite_evidence_probability_symmetry/PLAN.md
"""Day0 exit authority tests.

Object-meaning invariant (2026-05-05): stale model probability is not Day0
observation authority. Every value-based exit, including near settlement,
must fail closed when fresh_prob_is_fresh=False. Executable bid evidence must
not be proxied from a diagnostic/current market price.
"""
import math
from dataclasses import replace
from decimal import Decimal

import pytest
from src.state.portfolio import ExitContext, Position


def _make_position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t-day0",
        market_id="m1",
        city="NYC",
        cluster="US-Northeast",
        target_date="2026-04-01",
        bin_label="84-85F",
        direction="buy_yes",
        unit="F",
        size_usd=1.20,
        entry_price=0.02,
        p_posterior=0.02,
        edge=0.00,
        entered_at="2026-04-01T06:00:00Z",
        shares=60.0,
        cost_basis_usd=1.20,
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _make_day0_exit_context(
    fresh_prob: float,
    fresh_prob_is_fresh: bool,
    current_market_price: float,
    best_bid: float | None,
    hours_to_settlement: float = 3.0,
    current_ci: tuple[float, float] | None = None,
) -> ExitContext:
    if current_ci is None:
        current_ci = (
            max(0.0, fresh_prob - 0.01),
            min(1.0, fresh_prob + 0.01),
        )
    return ExitContext(
        bid_size=60.0,
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=fresh_prob_is_fresh,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid,
        current_ci=current_ci,
        hours_to_settlement=hours_to_settlement,
        position_state="day0_window",
        day0_active=True,
    )


class TestDay0ExitGateStaleProbability:
    """Day0 model probability authority tests."""

    def test_stale_prob_does_not_authorize_exit_with_executable_bid(self):
        """A stale probability must not authorize a model-driven Day0 hold/exit."""
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx_no_exit = _make_day0_exit_context(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_market_price=0.05,
            best_bid=0.05,
        )
        decision = pos.evaluate_exit(ctx_no_exit)
        assert not decision.should_exit, (
            f"Stale Day0 probability must not authorize an exit, got: {decision.reason}"
        )
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "fresh_prob_is_fresh" in ctx_no_exit.missing_authority_fields()
        assert "evidence_unavailable_third_state" in decision.applied_validations

    def test_stale_prob_fails_closed_in_day0_model_exit(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_market_price=0.05,
            best_bid=0.05,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "fresh_prob_is_fresh" in ctx.missing_authority_fields()
        assert "predicted_bin_exit_law" in decision.applied_validations
        assert "evidence_unavailable_third_state" in decision.applied_validations
        assert "day0_stale_prob_authority_waived" not in decision.applied_validations

    def test_fresh_prob_uses_model_not_market(self):
        """When fresh_prob_is_fresh=True, the model posterior is trusted (original behavior).
        EV gate: best_bid(0.60) <= fresh_prob(0.62) -> HOLD (model sees more value than market).
        """
        pos = _make_position(p_posterior=0.62, entry_price=0.61)
        ctx = _make_day0_exit_context(
            fresh_prob=0.62,
            fresh_prob_is_fresh=True,
            current_market_price=0.60,
            best_bid=0.60,
        )
        decision = pos.evaluate_exit(ctx)
        # Fresh model says 0.62 > market 0.60: EV gate holds
        assert not decision.should_exit, (
            f"Fresh model should veto exit when model > market, got: {decision.reason}"
        )
        assert decision.reason == decision.trigger == "HOLD"
        assert "hold" in decision.applied_validations
        assert "stale_prob_substitution" not in decision.applied_validations

    def test_stale_prob_substitution_is_not_allowed_in_validations(self):
        pos = _make_position(p_posterior=0.001, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=False,
            current_market_price=0.05,
            best_bid=0.05,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "fresh_prob_is_fresh" in ctx.missing_authority_fields()
        assert "stale_prob_substitution" not in decision.applied_validations
        assert "day0_stale_prob_authority_waived" not in decision.applied_validations
        assert "evidence_unavailable_third_state" in decision.applied_validations

    def test_stale_prob_primary_case_returns_evidence_unavailable(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.01,
            fresh_prob_is_fresh=False,
            current_market_price=0.05,
            best_bid=0.05,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "fresh_prob_is_fresh" in ctx.missing_authority_fields()
        assert "evidence_unavailable_third_state" in decision.applied_validations

    def test_stale_prob_outside_day0_returns_evidence_unavailable(self):
        """Stale held-side probability fails closed in either observation phase."""
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = ExitContext(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,  # stale
            current_ci=(0.01, 0.03),
            current_market_price=0.05,
            current_market_price_is_fresh=True,
            best_bid=0.05,
            hours_to_settlement=10.0,
            position_state="active",
            day0_active=False,  # NOT day0
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "fresh_prob_is_fresh" in ctx.missing_authority_fields()
        assert "evidence_unavailable_third_state" in decision.applied_validations

    def test_day0_missing_best_bid_does_not_use_degraded_market_price_proxy(self):
        pos = _make_position(p_posterior=0.001, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_ci=(0.0, 0.011),
            current_market_price=0.05,
            best_bid=None,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "HOLD"
        assert ctx.current_ci is not None
        assert ctx._current_ci_ok()
        assert not ctx._is_finite(ctx.best_bid)
        assert pos._exit_bid_breakpoints(ctx, Decimal(str(pos.effective_shares))) == ()
        assert "hold" in decision.applied_validations
        assert "best_bid_proxy_from_current_market_price" not in decision.applied_validations
        assert "best_bid_proxy_tick_discount" not in decision.applied_validations

    @pytest.mark.parametrize("bad_bid", [math.nan, math.inf, -math.inf])
    def test_day0_nonfinite_best_bid_does_not_authorize_exit(self, bad_bid):
        pos = _make_position(p_posterior=0.001, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_ci=(0.0, 0.011),
            current_market_price=0.05,
            best_bid=bad_bid,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "HOLD"
        assert ctx.current_ci is not None
        assert ctx._current_ci_ok()
        assert not ctx._is_finite(ctx.best_bid)
        assert pos._exit_bid_breakpoints(ctx, Decimal(str(pos.effective_shares))) == ()
        assert "hold" in decision.applied_validations

    def test_settlement_imminent_cannot_exit_without_model_probability_authority(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_market_price=0.05,
            best_bid=0.05,
            hours_to_settlement=0.5,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "fresh_prob_is_fresh" in ctx.missing_authority_fields()
        assert decision.trigger != "SETTLEMENT_IMMINENT"
        assert "evidence_unavailable_third_state" in decision.applied_validations

    def test_settlement_imminent_still_requires_executable_best_bid(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_ci=(0.0, 0.011),
            current_market_price=0.05,
            best_bid=None,
            hours_to_settlement=0.5,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "HOLD"
        assert ctx.current_ci is not None
        assert ctx._current_ci_ok()
        assert not ctx._is_finite(ctx.best_bid)
        assert pos._exit_bid_breakpoints(ctx, Decimal(str(pos.effective_shares))) == ()
        assert "hold" in decision.applied_validations
        assert "model_probability_authority_not_required:settlement_imminent" not in decision.applied_validations

    def test_settlement_imminent_still_rejects_nonfinite_best_bid(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_ci=(0.0, 0.011),
            current_market_price=0.05,
            best_bid=math.nan,
            hours_to_settlement=0.5,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "HOLD"
        assert ctx.current_ci is not None
        assert ctx._current_ci_ok()
        assert not ctx._is_finite(ctx.best_bid)
        assert pos._exit_bid_breakpoints(ctx, Decimal(str(pos.effective_shares))) == ()
        assert "hold" in decision.applied_validations

    def test_settlement_imminent_legal_bid_buy_no_near_certain_winner_holds(self):
        pos = _make_position(
            direction="buy_no",
            p_posterior=0.52,
            entry_price=0.52,
            bin_label="17C",
        )
        ctx = _make_day0_exit_context(
            fresh_prob=0.999,
            fresh_prob_is_fresh=True,
            current_market_price=0.95,
            best_bid=0.95,
            hours_to_settlement=0.5,
            current_ci=(0.80, 1.0),
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.trigger != "SETTLEMENT_IMMINENT"
        assert decision.reason == decision.trigger == "HOLD"
        assert "hold" in decision.applied_validations
        assert "near_settlement_terminal_bid_hold" not in decision.applied_validations

    @pytest.mark.parametrize("q_mean", [0.20, 0.8333333333333334])
    def test_settlement_imminent_legal_bid_sells_lower_held_mean(self, q_mean):
        pos = _make_position(
            direction="buy_no",
            p_posterior=0.52,
            entry_price=0.52,
            bin_label="17C",
        )
        ctx = _make_day0_exit_context(
            fresh_prob=q_mean,
            fresh_prob_is_fresh=True,
            current_market_price=0.95,
            best_bid=0.95,
            hours_to_settlement=0.5,
        )

        decision = pos.evaluate_exit(ctx)

        assert decision.should_exit
        assert decision.reason == decision.trigger == "SELL_REVERSAL"
        assert "sell_reversal" in decision.applied_validations
        assert "near_settlement_terminal_bid_hold" not in decision.applied_validations

    def test_whale_toxicity_cannot_bypass_model_probability_authority(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = ExitContext(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_ci=(0.01, 0.03),
            current_market_price=0.05,
            current_market_price_is_fresh=True,
            best_bid=0.05,
            hours_to_settlement=3.0,
            position_state="day0_window",
            day0_active=True,
            whale_toxicity=True,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.trigger != "WHALE_TOXICITY"
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "fresh_prob_is_fresh" in ctx.missing_authority_fields()
        assert "model_probability_authority_not_required:whale_toxicity" not in decision.applied_validations
        assert "evidence_unavailable_third_state" in decision.applied_validations

    def test_whale_toxicity_still_requires_executable_best_bid(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = ExitContext(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_ci=(0.0, 0.011),
            current_market_price=0.05,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=3.0,
            position_state="day0_window",
            day0_active=True,
            whale_toxicity=True,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "HOLD"
        assert ctx.current_ci is not None
        assert ctx._current_ci_ok()
        assert not ctx._is_finite(ctx.best_bid)
        assert pos._exit_bid_breakpoints(ctx, Decimal(str(pos.effective_shares))) == ()
        assert "hold" in decision.applied_validations
        assert "model_probability_authority_not_required:whale_toxicity" not in decision.applied_validations

    @pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
    @pytest.mark.parametrize("trigger_field", ["settlement", "whale"])
    @pytest.mark.parametrize("bad_bid", [None, math.nan, math.inf, -math.inf])
    def test_day0_force_exits_require_finite_best_bid_with_fresh_probability(
        self,
        direction,
        trigger_field,
        bad_bid,
    ):
        pos = _make_position(direction=direction, p_posterior=0.001, entry_price=0.02)
        ctx = ExitContext(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_ci=(0.0, 0.011),
            current_market_price=0.05,
            current_market_price_is_fresh=True,
            best_bid=bad_bid,
            hours_to_settlement=0.5 if trigger_field == "settlement" else 3.0,
            position_state="day0_window",
            day0_active=True,
            whale_toxicity=trigger_field == "whale",
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "HOLD"
        assert ctx.current_ci is not None
        assert ctx._current_ci_ok()
        assert not ctx._is_finite(ctx.best_bid)
        assert pos._exit_bid_breakpoints(ctx, Decimal(str(pos.effective_shares))) == ()
        assert "hold" in decision.applied_validations

    @pytest.mark.parametrize("current_ci", [None, (math.nan, 0.2), (0.2, 0.3)])
    @pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
    def test_current_confidence_carrier_is_required_even_when_sell_value_wins(
        self, current_ci, direction
    ):
        pos = _make_position(direction=direction)
        ctx = replace(
            _make_day0_exit_context(0.001, True, 0.05, 0.05), current_ci=current_ci
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == decision.trigger == "EVIDENCE_UNAVAILABLE"
        assert "current_ci" in ctx.missing_authority_fields()
        assert "evidence_unavailable_third_state" in decision.applied_validations

    @pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
    def test_fresh_held_mean_and_executable_bid_authorize_sell(self, direction):
        pos = _make_position(direction=direction)
        # The diagnostic mark and wide confidence upper tail do not replace the
        # held-side payoff mean or the current executable sell price.
        ctx = _make_day0_exit_context(
            0.001, True, 0.001, 0.05, current_ci=(0.0, 0.90)
        )

        decision = pos.evaluate_exit(ctx)

        assert ctx.missing_authority_fields() == []
        assert decision.should_exit
        assert decision.reason == decision.trigger == "SELL_REVERSAL"
        assert "sell_reversal" in decision.applied_validations

    def test_terminal_bid_is_not_a_legal_submit_price(self):
        from src.contracts.venue_submission_envelope import assert_live_order_unit_price

        # Local optimal stopping is not final execution authority. The old
        # terminal-bid fixture cannot be reused as a lawful SELL price.
        with pytest.raises(ValueError, match=r"inclusive \[0\.05, 0\.95\]"):
            assert_live_order_unit_price(0.999)
        assert assert_live_order_unit_price(0.95) == Decimal("0.95")
