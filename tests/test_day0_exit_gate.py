# Created: 2026-04-07
# Last reused/audited: 2026-04-23
# Authority basis: midstream verdict v2 2026-04-23 (docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T1.a midstream guardian panel)
"""Day0 exit authority tests.

Object-meaning invariant (2026-05-05): stale model probability is not Day0
observation authority. Model-driven exit gates must fail closed when
fresh_prob_is_fresh=False, unless the selected trigger explicitly does not
consume model probability authority. Executable bid evidence must not be
proxied from a diagnostic/current market price.
"""
import math

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
        # entry_ci_width left as default 0.0 u2192 edge_threshold = -0.01 (shallow floor)
    )
    defaults.update(kwargs)
    return Position(**defaults)


def _make_day0_exit_context(
    fresh_prob: float,
    fresh_prob_is_fresh: bool,
    current_market_price: float,
    best_bid: float | None,
    hours_to_settlement: float = 3.0,
) -> ExitContext:
    return ExitContext(
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=fresh_prob_is_fresh,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid,
        hours_to_settlement=hours_to_settlement,
        position_state="day0_window",
        day0_active=True,
    )


class TestDay0ExitGateStaleProbability:
    """Day0 model probability authority tests."""

    def test_stale_prob_does_not_block_exit_when_market_has_moved_against_position(self):
        """A stale probability must not authorize a model-driven Day0 hold/exit."""
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx_no_exit = _make_day0_exit_context(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_market_price=0.019,
            best_bid=0.019,
        )
        decision = pos.evaluate_exit(ctx_no_exit)
        assert not decision.should_exit, (
            f"Stale Day0 probability must not authorize an exit, got: {decision.reason}"
        )
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh)"
        assert "day0_probability_authority_blocked" in decision.applied_validations

    def test_stale_prob_fails_closed_in_day0_model_exit(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_market_price=0.01,
            best_bid=0.01,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh)"
        assert "exit_context_incomplete" in decision.applied_validations
        assert "day0_probability_authority_blocked" in decision.applied_validations
        assert "day0_stale_prob_authority_waived" not in decision.applied_validations

    def test_fresh_prob_uses_model_not_market(self):
        """When fresh_prob_is_fresh=True, the model posterior is trusted (original behavior).
        EV gate: best_bid(0.015) <= fresh_prob(0.02) -> HOLD (model sees more value than market).
        """
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.02,
            fresh_prob_is_fresh=True,
            current_market_price=0.015,
            best_bid=0.015,
        )
        decision = pos.evaluate_exit(ctx)
        # Fresh model says 0.02 > market 0.015: EV gate holds
        assert not decision.should_exit, (
            f"Fresh model should veto exit when model > market, got: {decision.reason}"
        )
        assert "stale_prob_substitution" not in decision.applied_validations

    def test_stale_prob_substitution_is_not_allowed_in_validations(self):
        pos = _make_position(p_posterior=0.001, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=False,
            current_market_price=0.03,
            best_bid=0.03,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh)"
        assert "stale_prob_substitution" not in decision.applied_validations
        assert "day0_stale_prob_authority_waived" not in decision.applied_validations
        assert "day0_probability_authority_blocked" in decision.applied_validations

    def test_stale_prob_primary_case_still_returns_incomplete_when_capped(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.01,
            fresh_prob_is_fresh=False,
            current_market_price=0.01,
            best_bid=0.01,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=fresh_prob_is_fresh)"
        assert "day0_probability_authority_blocked" in decision.applied_validations

    def test_stale_prob_outside_day0_still_returns_incomplete(self):
        """Outside day0_window, stale prob should still fail INCOMPLETE.
        The exception is only for day0 positions.
        """
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = ExitContext(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,  # stale
            current_market_price=0.01,
            current_market_price_is_fresh=True,
            best_bid=0.01,
            hours_to_settlement=10.0,
            position_state="entered",
            day0_active=False,  # NOT day0
        )
        decision = pos.evaluate_exit(ctx)
        assert decision.reason.startswith("INCOMPLETE_EXIT_CONTEXT"), (
            f"Non-day0 stale prob must return INCOMPLETE, got: {decision.reason}"
        )

    def test_day0_missing_best_bid_does_not_use_degraded_market_price_proxy(self):
        pos = _make_position(p_posterior=0.001, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_market_price=0.03,
            best_bid=None,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
        assert "best_bid_unavailable" in decision.applied_validations
        assert "best_bid_proxy_from_current_market_price" not in decision.applied_validations
        assert "best_bid_proxy_tick_discount" not in decision.applied_validations

    @pytest.mark.parametrize("bad_bid", [math.nan, math.inf, -math.inf])
    def test_day0_nonfinite_best_bid_does_not_authorize_exit(self, bad_bid):
        pos = _make_position(p_posterior=0.001, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_market_price=0.03,
            best_bid=bad_bid,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
        assert "best_bid_unavailable" in decision.applied_validations

    def test_settlement_imminent_can_exit_without_model_probability_authority(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_market_price=0.01,
            best_bid=0.01,
            hours_to_settlement=0.5,
        )

        decision = pos.evaluate_exit(ctx)

        assert decision.should_exit
        assert decision.trigger == "SETTLEMENT_IMMINENT"
        assert "model_probability_authority_not_required:settlement_imminent" in decision.applied_validations
        assert "day0_probability_authority_blocked" not in decision.applied_validations

    def test_settlement_imminent_still_requires_executable_best_bid(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_market_price=0.01,
            best_bid=None,
            hours_to_settlement=0.5,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
        assert "best_bid_unavailable" in decision.applied_validations
        assert "model_probability_authority_not_required:settlement_imminent" not in decision.applied_validations

    def test_settlement_imminent_still_rejects_nonfinite_best_bid(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = _make_day0_exit_context(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_market_price=0.01,
            best_bid=math.nan,
            hours_to_settlement=0.5,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
        assert "best_bid_unavailable" in decision.applied_validations

    def test_whale_toxicity_can_exit_without_model_probability_authority(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = ExitContext(
            fresh_prob=0.02,
            fresh_prob_is_fresh=False,
            current_market_price=0.01,
            current_market_price_is_fresh=True,
            best_bid=0.01,
            hours_to_settlement=3.0,
            position_state="day0_window",
            day0_active=True,
            whale_toxicity=True,
        )

        decision = pos.evaluate_exit(ctx)

        assert decision.should_exit
        assert decision.trigger == "WHALE_TOXICITY"
        assert "model_probability_authority_not_required:whale_toxicity" in decision.applied_validations
        assert "day0_probability_authority_blocked" not in decision.applied_validations

    def test_whale_toxicity_still_requires_executable_best_bid(self):
        pos = _make_position(p_posterior=0.02, entry_price=0.02)
        ctx = ExitContext(
            fresh_prob=0.001,
            fresh_prob_is_fresh=True,
            current_market_price=0.01,
            current_market_price_is_fresh=True,
            best_bid=None,
            hours_to_settlement=3.0,
            position_state="day0_window",
            day0_active=True,
            whale_toxicity=True,
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
        assert "best_bid_unavailable" in decision.applied_validations
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
            current_market_price=0.03,
            current_market_price_is_fresh=True,
            best_bid=bad_bid,
            hours_to_settlement=0.5 if trigger_field == "settlement" else 3.0,
            position_state="day0_window",
            day0_active=True,
            whale_toxicity=trigger_field == "whale",
        )

        decision = pos.evaluate_exit(ctx)

        assert not decision.should_exit
        assert decision.reason == "INCOMPLETE_EXIT_CONTEXT (missing=best_bid)"
        assert "best_bid_unavailable" in decision.applied_validations
