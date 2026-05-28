# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Exit Strategy math review (operator, 2026-05-27)
# Lifecycle: created=2026-05-27; last_reviewed=2026-05-27; last_reused=never
#
# Purpose: lock the D3 ExitFamilyDecision optimizer math — the
# deterministic-impossibility short-circuit, contradiction fail-closed,
# EV cash-out, and HOLD_DOMINANT verdicts. These are the rules every
# multi-bin family liquidation will obey at runtime.
"""Tests for src/strategy/exit_family_optimizer.py (D3 pure layer)."""
from __future__ import annotations

import math

import pytest

from src.strategy.exit_constrained_posterior import (
    constrain_family_posterior_by_observation,
)
from src.strategy.exit_family_optimizer import (
    ExitLegInput,
    optimize_exit_family,
)
from src.strategy.exit_observation_constraint import (
    build_settlement_progress_constraint,
)
from src.types.market import Bin


# ----- helpers -----


def _wbin(low: float | None, high: float | None) -> Bin:
    label_low = "lo" if low is None else f"{low}"
    label_high = "hi" if high is None else f"{high}"
    return Bin(low=low, high=high, unit="F", label=f"{label_low}-{label_high}°F")


def _det_high(value: float):
    return build_settlement_progress_constraint({
        "temperature_metric": "high",
        "high_so_far": value,
        "low_so_far": None,
        "source_authorized_for_settlement": 1,
        "local_date_matches_target": 1,
        "coverage_status": "OK",
        "freshness_status": "FRESH",
    })


def _advisory():
    return build_settlement_progress_constraint(None)


# ----- (a) Deterministic-impossibility short-circuit -----


class TestDeterministicImpossibilityShortCircuit:
    """Operator §5: 'For any held bin impossible by authorized observation:
    sell all executable shares if bid > min_exit_bid. No edge confirmation.'"""

    def test_impossible_bin_with_positive_bid_sells_full(self):
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p = [0.30, 0.50, 0.20]
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                leg_id="held_lo", bin_index=0, bin_label="60-61",
                direction="buy_yes", shares=100.0, best_bid=0.07,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("NYC", "2026-05-28", "high", "mkt-1"),
            constraint=constraint,
            constrained_posterior=posterior,
            legs=legs,
        )
        assert len(decision.legs) == 1
        leg = decision.legs[0]
        assert leg.action == "SELL_FULL"
        assert leg.reason == "OBSERVATION_IMPOSSIBLE_HIGH"
        assert leg.sell_shares == 100.0
        assert leg.feasibility == "impossible"
        assert decision.any_deterministic_exit() is True

    def test_impossible_bin_with_no_bid_holds_with_diagnostic_reason(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.30, 0.70]
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                leg_id="held_lo", bin_index=0, bin_label="60-61",
                direction="buy_yes", shares=50.0, best_bid=None,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.legs[0].action == "HOLD"
        assert decision.legs[0].reason == "OBSERVATION_IMPOSSIBLE_NO_BID"

    def test_impossible_bin_with_bid_below_min_holds(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.30, 0.70]
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                leg_id="held", bin_index=0, bin_label="60-61",
                direction="buy_yes", shares=50.0, best_bid=0.005,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
            min_exit_bid=0.01,
        )
        assert decision.legs[0].action == "HOLD"
        assert decision.legs[0].reason == "OBSERVATION_IMPOSSIBLE_NO_BID"

    def test_buy_no_on_impossible_yes_bin_holds_as_guaranteed_winner(self):
        """Operator §5 nuance + critic F-1 fix (2026-05-27):
        buy_no on an impossible YES bin is the WINNING side. The held-side
        win probability is (1 - p_obs) = 1.0 when YES is impossible, so
        hold_value = shares × 1.0 dominates any sell bid < 1.0. Optimizer
        must HOLD, not liquidate the guaranteed winner."""
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.30, 0.70]
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        # bin (60,61) is impossible for HIGH; a buy_no on that bin pays $1
        # at settlement. Bid 0.90 should NOT trigger cash-out.
        legs = [
            ExitLegInput(
                leg_id="no_winner", bin_index=0, bin_label="60-61",
                direction="buy_no", shares=80.0, best_bid=0.90,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        # Not impossibility short-circuit (that branch is buy_yes-only).
        assert leg.reason != "OBSERVATION_IMPOSSIBLE_HIGH"
        assert leg.reason != "OBSERVATION_IMPOSSIBLE_NO_BID"
        # held_p = 1 - 0 = 1.0; hold_value = 80; sell_value ≈ 72.
        assert leg.action == "HOLD"
        assert leg.reason == "HOLD_DOMINANT"
        assert leg.hold_value > leg.sell_value


# ----- (b) Contradiction fail-closed -----


class TestContradictionFailClosed:
    def test_contradiction_buy_yes_feasible_zero_p_bin_sells_full(self):
        """Test branch (b) directly: leg on a feasible bin with zero p
        (so impossibility branch doesn't fire) inside a contradiction
        family (feasible_mass <= eps). Contradiction-fail-closed sells
        the buy_yes leg even though that bin isn't itself impossible."""
        # observed=80 makes (60,61) and (62,63) impossible; (90,91) stays
        # feasible. p concentrates mass in the now-impossible bins, so
        # feasible_mass = 0 → contradiction.
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(90, 91)]
        p = [0.5, 0.5, 0.0]
        constraint = _det_high(value=80.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        assert posterior.contradiction_flag is True
        assert posterior.impossible_mask == (True, True, False)
        # Leg on the feasible-but-zero-p bin: impossibility branch skips,
        # contradiction-fail-closed fires for buy_yes.
        legs = [
            ExitLegInput(
                leg_id="x", bin_index=2, bin_label="90-91",
                direction="buy_yes", shares=10.0, best_bid=0.5,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.contradiction is True
        assert decision.legs[0].action == "SELL_FULL"
        assert decision.legs[0].reason == "OBSERVATION_CONTRADICTION_FAIL_CLOSED"

    def test_contradiction_buy_yes_feasible_zero_p_bin_without_bid_holds(self):
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(90, 91)]
        p = [0.5, 0.5, 0.0]
        constraint = _det_high(value=80.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                leg_id="x", bin_index=2, bin_label="90-91",
                direction="buy_yes", shares=10.0, best_bid=None,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.contradiction is True
        assert decision.legs[0].action == "HOLD"
        assert decision.legs[0].reason == "OBSERVATION_CONTRADICTION_NO_BID"

    def test_contradiction_buy_no_defers_to_ev_does_not_fail_close_sell(self):
        """Critic F-1 fix (2026-05-27): when YES posterior is all-impossible
        (contradiction), a buy_no leg is the guaranteed winner — held_p =
        1 - 0 = 1.0 — so EV cash-out path must HOLD against any bid < 1.0.
        The contradiction-fail-closed branch must NOT blindly sell buy_no."""
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.5, 0.5]
        constraint = _det_high(value=80.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        assert posterior.contradiction_flag is True
        legs = [
            ExitLegInput(
                leg_id="no_winner", bin_index=0, bin_label="60-61",
                direction="buy_no", shares=100.0, best_bid=0.90,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert leg.action == "HOLD"
        assert leg.reason == "HOLD_DOMINANT"
        assert leg.reason != "OBSERVATION_CONTRADICTION_FAIL_CLOSED"
        # Cash-out would forfeit ~$10 of guaranteed payoff.
        assert leg.hold_value > leg.sell_value


# ----- (c) EV cash-out logic -----


class TestEVCashOut:
    def test_sell_value_dominates_hold_value_triggers_cash_out(self):
        """Bin contains_current_record; observed near upper edge → p_obs
        could be modest. If bid prices in optimistic outcome > hold value,
        cash out."""
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        # observed=63 → bin (60,61) impossible, (62,63) current, (64,65) feasible.
        # p_obs renormalised over (62,63),(64,65). If p was (0.1, 0.4, 0.5),
        # p_obs is (0, 4/9, 5/9). For leg in (62,63): p_obs ≈ 0.444. If bid
        # for that leg is 0.80 (over-priced by market vs Zeus belief),
        # sell_value > hold_value.
        p = [0.1, 0.4, 0.5]
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                leg_id="leg62", bin_index=1, bin_label="62-63",
                direction="buy_yes", shares=100.0, best_bid=0.80,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert leg.action == "SELL_FULL"
        assert leg.reason == "EV_CASH_OUT"
        assert leg.sell_value > leg.hold_value

    def test_hold_value_dominates_keeps_position(self):
        """Bid is below Zeus belief → hold is strictly better."""
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        p = [0.1, 0.4, 0.5]
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                leg_id="leg62", bin_index=1, bin_label="62-63",
                direction="buy_yes", shares=100.0, best_bid=0.20,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        leg = decision.legs[0]
        assert leg.action == "HOLD"
        assert leg.reason == "HOLD_DOMINANT"
        assert leg.hold_value > leg.sell_value

    def test_no_bid_returns_hold_no_executable_bid(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.4, 0.6]
        constraint = _det_high(value=58.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput(
                leg_id="leg", bin_index=1, bin_label="62-63",
                direction="buy_yes", shares=50.0, best_bid=None,
            ),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.legs[0].action == "HOLD"
        assert decision.legs[0].reason == "NO_EXECUTABLE_BID"


# ----- ADVISORY_ONLY: no deterministic exits emitted -----


class TestAdvisoryDoesNotEmitDeterministicExits:
    def test_advisory_constraint_no_impossibility_exits_only_ev(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.4, 0.6]
        constraint = _advisory()  # ADVISORY_ONLY
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        # No impossible mask under advisory; both legs go through EV cash-out.
        # bid 0.10 vs p_obs 0.4 → hold dominates for leg0.
        # bid 0.90 vs p_obs 0.6 → sell dominates for leg1.
        legs = [
            ExitLegInput("a", 0, "60-61", "buy_yes", 100, 0.10),
            ExitLegInput("b", 1, "62-63", "buy_yes", 100, 0.90),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        assert decision.constraint_authority == "ADVISORY_ONLY"
        assert decision.any_deterministic_exit() is False
        assert decision.legs[0].action == "HOLD"
        assert decision.legs[0].reason == "HOLD_DOMINANT"
        assert decision.legs[1].action == "SELL_FULL"
        assert decision.legs[1].reason == "EV_CASH_OUT"


# ----- Multi-bin family: mixed sell + hold in one pass -----


class TestMultiBinFamilyMixedVerdicts:
    """Operator §1: 'multi-bin family … real object is vector y'.
    One sweep returns a verdict per leg, and the impossibility short-circuit
    coexists with EV cash-out for still-feasible legs."""

    def test_mixed_loser_and_winner_in_one_family(self):
        bins = [_wbin(60, 61), _wbin(62, 63), _wbin(64, 65)]
        # Original Zeus belief: heavy on (62,63), light on (60,61) and (64,65).
        p = [0.10, 0.70, 0.20]
        # WU reports high=63 → (60,61) impossible.
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            # Held loser bin (60,61): WU made it impossible.
            ExitLegInput("loser", 0, "60-61", "buy_yes", 100.0, 0.05),
            # Held current bin (62,63): bid below belief → hold.
            ExitLegInput("winner", 1, "62-63", "buy_yes", 100.0, 0.40),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        # Loser exits same tick at executable bid.
        loser = decision.legs[0]
        assert loser.action == "SELL_FULL"
        assert loser.reason == "OBSERVATION_IMPOSSIBLE_HIGH"
        # Winner held — bid 0.40 vs p_obs ~7/9=0.778, hold dominates.
        winner = decision.legs[1]
        assert winner.action == "HOLD"
        assert winner.reason == "HOLD_DOMINANT"

    def test_sells_helper_returns_only_sell_legs(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.3, 0.7]
        constraint = _det_high(value=63.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [
            ExitLegInput("loser", 0, "60-61", "buy_yes", 50.0, 0.05),
            ExitLegInput("hold", 1, "62-63", "buy_yes", 50.0, 0.10),
        ]
        decision = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs,
        )
        sells = decision.sells()
        assert len(sells) == 1
        assert sells[0].leg_id == "loser"


# ----- Fee primitive (canonical polymarket_fee + boundary validation) -----


class TestSellValueFeeAndBidValidation:
    def test_zero_fee_rate_yields_raw_sell_value(self):
        from src.strategy.exit_family_optimizer import _per_leg_sell_value
        v = _per_leg_sell_value(shares=100.0, bid=0.40, fee_rate=0.0)
        assert math.isclose(v, 40.0, rel_tol=1e-12)

    def test_polymarket_fee_lowers_sell_value_at_mid(self):
        from src.strategy.exit_family_optimizer import _per_leg_sell_value
        # bid=0.5, rate=0.02 → canonical polymarket_fee = 0.02 * 0.5 * 0.5 = 0.005
        # sell value = 100 * (0.5 - 0.005) = 49.5
        v = _per_leg_sell_value(shares=100.0, bid=0.50, fee_rate=0.02)
        assert math.isclose(v, 49.5, rel_tol=1e-12)

    def test_bid_at_boundary_zero_or_one_clamps_to_no_fee(self):
        # Boundary {0.0, 1.0}: matches HoldValue.compute_with_exit_costs clamp;
        # canonical polymarket_fee raises on these — caller must clamp.
        from src.strategy.exit_family_optimizer import _per_leg_sell_value
        assert _per_leg_sell_value(100.0, 0.0, fee_rate=0.05) == 0.0
        assert _per_leg_sell_value(100.0, 1.0, fee_rate=0.05) == 100.0

    @pytest.mark.parametrize("bad_bid", [-0.01, 1.01, 1.5, -0.5, float("nan"), float("inf"), float("-inf")])
    def test_bid_outside_unit_interval_raises_not_silent_zero(self, bad_bid):
        """Critic F-2: silent-zero on garbage bid is a regression class.
        Canonical polymarket_fee raises on price ∉ (0,1); per-leg sell value
        validates bid ∈ [0,1] and finite up-front so EV decisions can't be
        driven by negative proceeds or impossible-price arithmetic."""
        from src.strategy.exit_family_optimizer import _per_leg_sell_value
        with pytest.raises(ValueError):
            _per_leg_sell_value(100.0, bad_bid, fee_rate=0.05)


# ----- Direction-aware hold_value (critic F-1 lock-in) -----


class TestHeldProbabilityDirection:
    def test_buy_yes_held_probability_equals_p_obs(self):
        from src.strategy.exit_family_optimizer import _held_probability
        assert _held_probability(0.4, "buy_yes") == 0.4

    def test_buy_no_held_probability_equals_one_minus_p_obs(self):
        from src.strategy.exit_family_optimizer import _held_probability
        assert math.isclose(_held_probability(0.4, "buy_no"), 0.6, rel_tol=1e-12)

    def test_buy_no_on_zero_p_obs_yields_one(self):
        # The exact regression class fixed by F-1: impossible YES bin gives
        # p_obs=0, so held_p for buy_no must be 1.0.
        from src.strategy.exit_family_optimizer import _held_probability
        assert _held_probability(0.0, "buy_no") == 1.0

    def test_unknown_direction_raises(self):
        from src.strategy.exit_family_optimizer import _held_probability
        with pytest.raises(ValueError):
            _held_probability(0.5, "sideways")


# ----- Hurdle composability (critic F-4 lock-in) -----


class TestHurdleComposability:
    """`hold_cost_extras` (per-leg $ deduction) + `daily_hurdle_dollars`
    (family-level $ floor) compose ADDITIVELY. Test locks the contract so
    a future caller can't accidentally double-count time-cost."""

    def test_extras_and_hurdle_compose_additively_in_ev_decision(self):
        bins = [_wbin(60, 61), _wbin(62, 63)]
        p = [0.4, 0.6]
        # No observation impossibility — pure EV path.
        constraint = _det_high(value=58.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)

        # leg: 100 shares, p_obs=0.4, bid=0.50 (≈zero fee at low rate).
        # sell_value ≈ 100 * 0.50 = 50.0
        # hold_value(no extras) = 100 * 0.4 - 0 = 40.0
        # sell_value > hold_value, EV cash-out triggers.
        legs_no_costs = [ExitLegInput("a", 0, "60-61", "buy_yes", 100.0, 0.50)]
        decision_no = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs_no_costs,
            daily_hurdle_dollars=0.0,
        )
        assert decision_no.legs[0].action == "SELL_FULL"

        # Same leg but with hold_cost_extras=5.0 + hurdle=6.0:
        # sell_value(50) > hold_value(40 - 5 = 35) + hurdle(6) = 41?
        # 50 > 41 → SELL_FULL still.
        legs_low_hurdle = [ExitLegInput("a", 0, "60-61", "buy_yes", 100.0, 0.50, hold_cost_extras=5.0)]
        decision_low = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs_low_hurdle,
            daily_hurdle_dollars=6.0,
        )
        assert decision_low.legs[0].action == "SELL_FULL"

        # Bump hurdle to 12: 50 > (40 - 5) + 12 = 47? → 50 > 47 → SELL_FULL.
        # Bump hurdle to 16: 50 > (40 - 5) + 16 = 51? → 50 > 51 is FALSE → HOLD.
        legs_high_hurdle = [ExitLegInput("a", 0, "60-61", "buy_yes", 100.0, 0.50, hold_cost_extras=5.0)]
        decision_high = optimize_exit_family(
            family_key=("X",), constraint=constraint,
            constrained_posterior=posterior, legs=legs_high_hurdle,
            daily_hurdle_dollars=16.0,
        )
        assert decision_high.legs[0].action == "HOLD"
        assert decision_high.legs[0].reason == "HOLD_DOMINANT"


# ----- Input validation -----


class TestInputValidation:
    def test_bin_index_out_of_range_raises(self):
        bins = [_wbin(60, 61)]
        p = [1.0]
        constraint = _det_high(value=58.0)
        posterior = constrain_family_posterior_by_observation(p, bins, constraint)
        legs = [ExitLegInput("x", 5, "out", "buy_yes", 1.0, 0.5)]
        with pytest.raises(IndexError, match="out of range"):
            optimize_exit_family(
                family_key=("X",), constraint=constraint,
                constrained_posterior=posterior, legs=legs,
            )
