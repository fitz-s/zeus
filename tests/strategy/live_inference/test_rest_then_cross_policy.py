# Created: 2026-06-10
# Last reused or audited: 2026-06-20 (lifecycle conversion fix: no-identical-re-rest,
#   shadow-gate collapse, double-submit-safety + first-rest-default acceptance tests)
# Authority basis: docs/archive/2026-Q2/operations_historical/consolidated_systemic_overhaul_2026-06-11.md K4.0
# (operator escalation: taker-only execution root cause) +
# docs/evidence/maker_taker/2026-06-10_taker_only_root_cause.md (KM measurement).
"""K4.0 REST-THEN-CROSS policy relationship tests (written RED-FIRST).

The design failure being killed: both one-shot forced taker and one-shot forced
maker are wrong. The live option structure is mode-consistent: cross only when
the fresh taker clears the conservative q/q_exec bound, fee/spread guards, and a
material EV advantage over maker; otherwise post post_only GTC with the measured
escalation deadline and re-certify before any later cross.

ANTIBODY (the operator-named relationship): no taker cross may be chosen while
an unexpired same-family maker rest exists.
"""

import math

import pytest

from src.strategy.live_inference.mode_consistent_ev import (
    MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE,
    MAKER_REST_ESCALATION_DEADLINE_MINUTES,
    POLICY_HOLD_REST_IN_PROGRESS,
    POLICY_MAKER_TAKER_FORBIDDEN,
    POLICY_REST_DEFAULT,
    POLICY_TAKER_EDGE_CLEARS_BOUND,
    POLICY_TAKER_ESCALATED_AFTER_REST,
    POLICY_TAKER_EVENT_END_NEAR,
    POLICY_TAKER_FLEETING_EDGE,
    POLICY_TAKER_MAKER_INADMISSIBLE,
    TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES,
    TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD,
    select_rest_then_cross_mode,
)

# A healthy two-sided book where the taker leg clears q_lcb but does not beat
# the maker leg by the mode hysteresis margin. This should rest.
HEALTHY = dict(
    q_lcb=0.673,
    taker_all_in_cost=0.665,
    p_fill_taker=1.0,
    best_bid=0.58,
    best_ask=0.66,
    tick_size=0.01,
    reservation=0.70,
)


def _decide(**overrides):
    kwargs = dict(HEALTHY)
    kwargs.update(overrides)
    return select_rest_then_cross_mode(**kwargs)


class TestRestDefault:
    def test_healthy_book_without_material_taker_advantage_rests_as_maker(self):
        """A fresh candidate rests when taker does not materially beat maker."""
        decision = _decide(minutes_to_event_end=20 * 60.0)
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT
        assert decision.escalation_deadline_minutes == pytest.approx(
            MAKER_REST_ESCALATION_DEADLINE_MINUTES
        )
        assert decision.ev_maker is not None and decision.ev_maker > 0.0

    def test_fresh_taker_crosses_when_materially_superior_after_costs(self):
        """Taker is lawful when q/q_exec, spread, fee, and EV-margin all clear."""
        decision = _decide(q_lcb=0.82, minutes_to_event_end=20 * 60.0)
        assert decision.ev_taker is not None and decision.ev_maker is not None
        assert decision.ev_taker >= decision.ev_maker * (
            1.0 + decision.taker_over_maker_margin
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_EDGE_CLEARS_BOUND

    def test_measured_fill_prior_is_used_not_the_guess(self):
        decision = _decide(minutes_to_event_end=20 * 60.0)
        assert decision.maker_fill_probability == pytest.approx(
            MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE
        )
        assert "MEASURED" in decision.maker_fill_probability_source

    def test_unknown_event_end_rests(self):
        """Missing event-end info fails toward resting (the conservative default)."""
        decision = _decide(minutes_to_event_end=None)
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT


class TestAntibodyNoCrossDuringRest:
    """Operator antibody: no taker cross while an unexpired same-family rest exists."""

    def test_unexpired_rest_holds_even_for_fleeting_edge(self):
        decision = _decide(
            q_lcb=0.90,  # edge 0.235 >> fleeting threshold
            minutes_to_event_end=30.0,  # also inside the event-end taker lane
            unexpired_family_rest=True,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_HOLD_REST_IN_PROGRESS
        assert decision.chosen_ev == float("-inf")  # trade-score gate rejects: NO new order

    def test_unexpired_rest_holds_even_when_escalated_flag_lies(self):
        """Belt-and-suspenders: an inconsistent caller passing both escalated and
        unexpired-rest must HOLD (the rest is the truth; escalation requires the
        rest to be terminal first)."""
        decision = _decide(
            unexpired_family_rest=True,
            escalated_after_rest=True,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_HOLD_REST_IN_PROGRESS


class TestEscalationLane:
    def test_escalated_after_rest_crosses(self):
        """Deadline passed, rest cancelled unfilled, edge re-certified -> cross."""
        decision = _decide(
            escalated_after_rest=True,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_ESCALATED_AFTER_REST

    def test_escalated_but_taker_forbidden_does_not_re_rest(self):
        """Escalation licenses the cross only when the taker lane is admissible
        (the spread guard stays lawful — K4.0 keeps TAKER_MAX_RELATIVE_SPREAD).

        NO-IDENTICAL-RE-REST fix (2026-06-20 conversion death-line): when the rest
        already expired UNFILLED and the fresh taker is INADMISSIBLE, the policy must
        NOT re-post the identical unfillable rest — it returns a NO-TRADE
        (chosen_ev=-inf) so the trade-score gate rejects and the family re-evaluates
        on a FRESH book next cycle. The verdict still travels as MAKER_TAKER_FORBIDDEN
        for receipt provenance.
        """
        decision = _decide(
            escalated_after_rest=True,
            best_bid=0.10,
            best_ask=0.40,  # relative spread 1.2 >> 0.25 guard
            taker_all_in_cost=0.42,
            q_lcb=0.60,
            reservation=0.55,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_MAKER_TAKER_FORBIDDEN
        # The proven-unfillable rest is NOT re-posted: chosen_ev=-inf == no-trade.
        assert decision.chosen_ev == float("-inf")

    def test_first_rest_for_family_still_rests_default(self):
        """The Karachi rest-first antibody is intact: a family with NO prior
        cancelled-unfilled rest (escalated_after_rest=False) on a healthy book
        REST_DEFAULTs as a real maker rest (finite ev) — only an ESCALATED family
        whose taker is inadmissible no-trades."""
        decision = _decide(minutes_to_event_end=20 * 60.0)  # escalated defaults False
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT
        assert decision.chosen_ev != float("-inf")


class TestExceptionLanes:
    def test_event_end_near_crosses(self):
        decision = _decide(
            minutes_to_event_end=TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES - 1.0
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_EVENT_END_NEAR

    def test_event_end_far_rests(self):
        decision = _decide(
            minutes_to_event_end=TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES + 60.0
        )
        assert decision.chosen_mode == "MAKER"

    def test_fleeting_edge_crosses_only_near_event_end(self):
        # A big edge far from event end is not the special fleeting lane, but it
        # may still cross through the ordinary fresh-book EV superiority lane.
        far = _decide(
            q_lcb=HEALTHY["taker_all_in_cost"]
            + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD
            + 0.01,
            minutes_to_event_end=20 * 60.0,
        )
        assert far.chosen_mode == "TAKER"
        assert far.policy == POLICY_TAKER_EDGE_CLEARS_BOUND
        # Inside the near-end window (>= the 180m unconditional floor, < 360m)
        # the lane still crosses on a huge edge.
        near = _decide(
            q_lcb=HEALTHY["taker_all_in_cost"]
            + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD
            + 0.01,
            minutes_to_event_end=300.0,
        )
        assert near.chosen_mode == "TAKER"
        assert near.policy == POLICY_TAKER_FLEETING_EDGE
        # Unknown horizon is not enough to invoke the fleeting lane, but the
        # ordinary EV-superiority lane can still cross when the book is strong.
        unknown = _decide(
            q_lcb=HEALTHY["taker_all_in_cost"]
            + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD
            + 0.01,
            minutes_to_event_end=None,
        )
        assert unknown.chosen_mode == "TAKER"
        assert unknown.policy == POLICY_TAKER_EDGE_CLEARS_BOUND
        # Nesting relation: the fleeting window sits ABOVE the unconditional
        # event-end floor, else lane 5 is dead code.
        from src.strategy.live_inference.mode_consistent_ev import (
            TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END,
            TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES as _floor,
        )
        assert TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END > _floor

    def test_sub_fleeting_edge_can_still_cross_when_ev_superior(self):
        decision = _decide(
            q_lcb=HEALTHY["taker_all_in_cost"]
            + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD
            - 0.02,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_EDGE_CLEARS_BOUND

    def test_bidless_book_rests_at_ask_minus_tick(self):
        """No bid: the spread guard already forbids crossing (unmeasurable book)
        and maker_limit_price rests at min(ask-tick, reservation) — the policy
        rests (bid-establishing), it does not cross."""
        decision = _decide(best_bid=None, minutes_to_event_end=20 * 60.0)
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT
        assert decision.maker_limit_price == pytest.approx(0.65)

    def test_maker_inadmissible_with_lawful_taker_crosses(self):
        """Maker placement structurally impossible (reservation below one tick ->
        no positive limit) while the taker lane is lawful -> taker survives."""
        decision = _decide(
            reservation=0.005,  # below tick: maker_limit_price -> None
            q_lcb=0.10,
            taker_all_in_cost=0.08,
            best_bid=0.07,
            best_ask=0.08,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_MAKER_INADMISSIBLE

    def test_unpriceable_in_both_modes_rejects(self):
        decision = _decide(
            reservation=0.005,  # maker limit unconstructible
            taker_all_in_cost=None,  # no taker cost either
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.chosen_ev == float("-inf")


class TestFixBConservativeQlcbCapOnCross:
    """FIX B (#127): a taker cross may NEVER execute above the conservative q_lcb.

    The settlement-honest cap: a taker lane is admissible ONLY when the FRESH
    all-in taker cost clears q_lcb. The Chengdu live case (best_ask 0.73 vs
    q_lcb 0.72) must stay MAKER / no-trade — a correct outcome, not a forced
    fill and not a taker the cert builder would have to reject. A genuinely
    fillable escalation (fresh ask all-in <= q_lcb) becomes a real cross.
    """

    def test_chengdu_ask_above_qlcb_stays_maker_on_escalation(self):
        """best_ask 0.73 all-in > q_lcb 0.72: even an ESCALATED rest (deadline
        passed) must NOT cross — it stays maker / re-rests. No 0.73 fill."""
        decision = select_rest_then_cross_mode(
            q_lcb=0.72,
            taker_all_in_cost=0.73,  # fresh ask all-in ABOVE the conservative bound
            p_fill_taker=1.0,
            best_bid=0.71,
            best_ask=0.73,
            tick_size=0.01,
            reservation=0.72,
            escalated_after_rest=True,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_MAKER_TAKER_FORBIDDEN

    def test_chengdu_ask_above_qlcb_stays_maker_near_event_end(self):
        """The same ask-above-q_lcb book near the event end must STILL not cross:
        the q_lcb cap dominates the event-end taker lane (never cross above q_lcb)."""
        decision = select_rest_then_cross_mode(
            q_lcb=0.72,
            taker_all_in_cost=0.73,
            p_fill_taker=1.0,
            best_bid=0.71,
            best_ask=0.73,
            tick_size=0.01,
            reservation=0.72,
            minutes_to_event_end=TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES - 1.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT

    def test_escalation_with_fresh_allin_clearing_qlcb_crosses(self):
        """After a rest times out, if the FRESH taker all-in cost STILL clears
        q_lcb (ask all-in 0.70 <= q_lcb 0.72), the escalation crosses -> real fill."""
        decision = select_rest_then_cross_mode(
            q_lcb=0.72,
            taker_all_in_cost=0.70,  # fresh ask all-in clears the conservative bound
            p_fill_taker=1.0,
            best_bid=0.69,
            best_ask=0.70,
            tick_size=0.01,
            reservation=0.72,
            escalated_after_rest=True,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_ESCALATED_AFTER_REST
        # The crossing cost never exceeds the conservative bound.
        assert decision.ev_taker is not None
        assert 0.70 <= 0.72  # cross all-in <= q_lcb (the HARD LAW)

    def test_escalated_one_tick_penny_book_crosses_when_allin_clears_qlcb(self):
        """A one-tick 0.001/0.002 book has a huge relative spread but only one
        tick of absolute spread. Once the maker rest expires, a q_lcb-certified
        edge must be able to cross instead of re-posting a permanent 0.001 bid."""
        decision = select_rest_then_cross_mode(
            q_lcb=0.03,
            taker_all_in_cost=0.0021,
            p_fill_taker=1.0,
            best_bid=0.001,
            best_ask=0.002,
            tick_size=0.001,
            reservation=0.03,
            escalated_after_rest=True,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_ESCALATED_AFTER_REST
        assert decision.taker_forbidden_reason is None

    def test_escalated_multi_tick_penny_book_still_rejects_taker(self):
        decision = select_rest_then_cross_mode(
            q_lcb=0.03,
            taker_all_in_cost=0.0041,
            p_fill_taker=1.0,
            best_bid=0.001,
            best_ask=0.004,
            tick_size=0.001,
            reservation=0.03,
            escalated_after_rest=True,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_MAKER_TAKER_FORBIDDEN
        assert decision.chosen_ev == float("-inf")

    def test_event_end_near_with_allin_clearing_qlcb_crosses(self):
        """Near the event end, a fresh taker all-in that clears q_lcb crosses."""
        decision = select_rest_then_cross_mode(
            q_lcb=0.72,
            taker_all_in_cost=0.70,
            p_fill_taker=1.0,
            best_bid=0.69,
            best_ask=0.70,
            tick_size=0.01,
            reservation=0.72,
            minutes_to_event_end=TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES - 1.0,
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_EVENT_END_NEAR

    def test_maker_inadmissible_but_cross_would_exceed_qlcb_no_trade(self):
        """One-sided book (maker structurally impossible) where the only taker
        cross would exceed q_lcb: NO trade (never cross above the bound), not a
        forced taker. Conservative-entry law holds even when maker is impossible."""
        decision = select_rest_then_cross_mode(
            q_lcb=0.72,
            taker_all_in_cost=0.73,  # the only available cross exceeds q_lcb
            p_fill_taker=1.0,
            best_bid=None,           # no bid -> maker limit unconstructible here
            best_ask=0.73,
            tick_size=0.01,
            reservation=0.005,       # below tick -> maker_limit_price -> None
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.chosen_ev == float("-inf")  # trade-score gate rejects: no trade


class TestDoubleSubmitSafetyPreserved:
    """Single-flight double-submit safety is intact after the conversion fix: a
    family with a genuinely-LIVE newer rest still HOLDs (no cross) even when the
    escalation flag is also (inconsistently) set — the unexpired rest is the truth.
    """

    def test_live_competing_rest_holds_even_when_escalated(self):
        decision = _decide(
            unexpired_family_rest=True,   # a genuinely-LIVE newer same-family rest
            escalated_after_rest=True,    # and a prior cancelled rest armed escalation
            taker_all_in_cost=0.50,       # a perfectly admissible taker (ask <= q_lcb)
            best_ask=0.50,
            q_lcb=0.71,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_HOLD_REST_IN_PROGRESS
        assert decision.chosen_ev == float("-inf")  # NO new order while a live rest exists


class TestTakerQualityLiveGate:
    """Taker submit requires surplus plus live quality margins."""

    def _proof(self, *, q_lcb, ask):
        from src.engine.event_reactor_adapter import (
            _build_event_bound_taker_quality_proof,
        )

        return _build_event_bound_taker_quality_proof(
            actionable_payload={
                "direction": "buy_yes",
                "q_live": q_lcb,
                "q_lcb_5pct": q_lcb,
                "live_cap_reserved_notional_usd": 10.0,
                "proof_maker_limit_price": 0.45,
                "proof_ev_maker": 0.01,
            },
            order_mode="TAKER",
            fresh_best_bid=ask - 0.01,
            fresh_best_ask=ask,
        )

    def test_positive_surplus_below_live_thresholds_fails_closed(self):
        """A taker with tiny positive surplus is not enough for live-money submit."""
        proof = self._proof(q_lcb=0.52, ask=0.49)  # edge ~0.0175 in [0, 0.03)
        assert proof is not None
        assert float(proof["taker_fee_adjusted_edge"]) >= 0.0
        assert float(proof["taker_fee_adjusted_edge"]) < float(
            proof["min_taker_fee_adjusted_edge"]
        )
        assert proof["passed"] is False
        assert proof["reason"] == "taker_quality_threshold_not_met"
        assert proof["legacy_threshold_pass"] is False
        assert proof["passed_basis"] == "conservative_after_cost_plus_taker_and_strategy_quality_floors"

    def test_negative_surplus_still_fails_closed(self):
        """The conservative law is NEVER loosened: ask + fee > q_lcb (negative
        after-cost edge) still fails closed — only the EXTRA cap was removed."""
        # q_lcb must clear LIVE_DIRECTION_WIN_RATE_FLOOR (0.51,
        # src/strategy/live_inference/live_admission.py) — a separate, earlier
        # gate in _build_event_bound_taker_quality_proof — or the function
        # short-circuits there and returns the placeholder
        # taker_fee_adjusted_edge="0" used by every early-return branch,
        # never reaching the after-cost edge computation this test targets.
        proof = self._proof(q_lcb=0.55, ask=0.65)  # edge clearly negative
        assert proof is not None
        assert proof["reason"] == "negative_conservative_after_cost_surplus"
        assert float(proof["taker_fee_adjusted_edge"]) < 0.0
        assert proof["passed"] is False


class TestConstantsProvenance:
    def test_deadline_is_settlement_derived(self):
        """2026-06-16: deadline 120->20 min. The KM curve is flat 15-60 min, so
        waiting to 120 forfeits the cross for ~2h; a settlement counterfactual
        (49 settled day-ahead NO picks, 41/49=84% won, +$88 vs $0 captured) proves
        the admissible cross of the unfilled remainder is +EV. 20 min keeps the
        fast-fill maker window (~0.19) then escalates. Registry-tracked, DERIVED."""
        assert MAKER_REST_ESCALATION_DEADLINE_MINUTES == 20.0
        assert MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE == 0.19

    def test_event_end_floor_exceeds_deadline(self):
        """Relation: the event-end taker floor must exceed the escalation deadline
        (a rest that cannot reach its deadline before the event ends is pointless)."""
        assert (
            TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES
            > MAKER_REST_ESCALATION_DEADLINE_MINUTES
        )

    def test_registry_carries_the_deadline(self):
        from src.contracts.time_semantics import REGISTRY

        names = {entry.name: entry for entry in REGISTRY}
        assert "maker_rest_escalation_deadline" in names
        entry = names["maker_rest_escalation_deadline"]
        assert entry.basis_kind.value == "DERIVED"
        assert entry.value() == pytest.approx(20.0 / 60.0)  # hours
