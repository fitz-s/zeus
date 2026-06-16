# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: docs/operations/consolidated_systemic_overhaul_2026-06-11.md K4.0
# (operator escalation: taker-only execution root cause) +
# docs/evidence/maker_taker/2026-06-10_taker_only_root_cause.md (KM measurement).
"""K4.0 REST-THEN-CROSS policy relationship tests (written RED-FIRST).

The design failure being killed: one-shot maker-XOR-taker EV comparison with a
p_fill_maker=0.10 GUESS handicapped the maker lane ~10x, so all 6 live fills
were FOK crosses paying 4.0% of notional to spread. The true option structure
is REST-THEN-CROSS: post post_only GTC at the maker limit with a measured
escalation deadline; cross only at the deadline after the edge re-certifies,
or immediately in the declared exception lanes.

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
    POLICY_TAKER_ESCALATED_AFTER_REST,
    POLICY_TAKER_EVENT_END_NEAR,
    POLICY_TAKER_FLEETING_EDGE,
    POLICY_TAKER_MAKER_INADMISSIBLE,
    TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES,
    TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD,
    select_rest_then_cross_mode,
)

# A healthy wide two-sided book of the class the operator flagged (Karachi-like):
# certified edge ~5c on a 8c-wide 0.58/0.66 book — under the OLD one-shot EV the
# taker always won here; under REST-THEN-CROSS this MUST rest.
HEALTHY = dict(
    q_lcb=0.71,
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
    def test_healthy_wide_book_rests_as_maker(self):
        """The Karachi-class fill: REST, never immediate cross."""
        decision = _decide(minutes_to_event_end=20 * 60.0)
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT
        assert decision.escalation_deadline_minutes == pytest.approx(
            MAKER_REST_ESCALATION_DEADLINE_MINUTES
        )
        assert decision.ev_maker is not None and decision.ev_maker > 0.0

    def test_rest_default_even_when_taker_ev_higher(self):
        """The policy overrides the one-shot EV comparison: a healthy non-fleeting
        edge rests even where EV_taker > EV_maker (that comparison was the disease)."""
        decision = _decide(minutes_to_event_end=20 * 60.0)
        # ev provenance still recorded for the settlement loop:
        assert decision.ev_taker is not None
        assert decision.chosen_mode == "MAKER"

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

    def test_escalated_but_taker_forbidden_rests(self):
        """Escalation licenses the cross only when the taker lane is admissible
        (the spread guard stays lawful — K4.0 keeps TAKER_MAX_RELATIVE_SPREAD)."""
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
        # OPERATOR DIRECTIVE 2026-06-11 (Denver first fill: lane 2 crossed a
        # 5-cent spread 26h before settlement, paying $0.43 mark-to-mid). A big
        # edge FAR from the event end is STRUCTURAL, not fleeting -> REST.
        far = _decide(
            q_lcb=HEALTHY["taker_all_in_cost"]
            + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD
            + 0.01,
            minutes_to_event_end=20 * 60.0,
        )
        assert far.chosen_mode == "MAKER"
        assert far.policy == POLICY_REST_DEFAULT
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
        # Unknown horizon is conservative: REST.
        unknown = _decide(
            q_lcb=HEALTHY["taker_all_in_cost"]
            + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD
            + 0.01,
            minutes_to_event_end=None,
        )
        assert unknown.chosen_mode == "MAKER"
        # Nesting relation: the fleeting window sits ABOVE the unconditional
        # event-end floor, else lane 5 is dead code.
        from src.strategy.live_inference.mode_consistent_ev import (
            TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END,
            TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES as _floor,
        )
        assert TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END > _floor

    def test_sub_fleeting_edge_rests(self):
        decision = _decide(
            q_lcb=HEALTHY["taker_all_in_cost"]
            + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD
            - 0.02,
            minutes_to_event_end=20 * 60.0,
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT

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


class TestConstantsProvenance:
    def test_deadline_is_measured_basis(self):
        """120 min comes from the KM curve (0.39 cumulative fill by 120 min,
        n=108 right-censored resting facts). Registry-tracked."""
        assert MAKER_REST_ESCALATION_DEADLINE_MINUTES == 120.0
        assert MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE == 0.39

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
        assert entry.basis_kind.value == "MEASURED"
        assert entry.value() == pytest.approx(2.0)  # hours
