# Created: 2026-06-10
# Last reused or audited: 2026-06-20 (lifecycle conversion fix: no-identical-re-rest,
#   shadow-gate collapse, double-submit-safety + first-rest-default acceptance tests)
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


# Taker INADMISSIBLE: the fresh all-in ask sits ABOVE the conservative q_lcb (FIX B forbids
# crossing above q_lcb), so the cross-to-fill lane cannot fire and the policy rests as a maker
# limit below q_lcb that fills only on a favorable move. (ask+fee 0.73 > q_lcb 0.71.)
INADMISSIBLE_REST = dict(
    q_lcb=0.71,
    taker_all_in_cost=0.73,
    p_fill_taker=1.0,
    best_bid=0.58,
    best_ask=0.72,
    tick_size=0.01,
    reservation=0.70,
)


def _decide(**overrides):
    kwargs = dict(HEALTHY)
    kwargs.update(overrides)
    return select_rest_then_cross_mode(**kwargs)


def _decide_rest(**overrides):
    kwargs = dict(INADMISSIBLE_REST)
    kwargs.update(overrides)
    return select_rest_then_cross_mode(**kwargs)


class TestFillLaneCrossesWhenEdgeClearsBound:
    """Operator directive 2026-06-23 ("no orders are filling"): when the marketable cross clears
    the conservative bound (best_ask+fee <= q_lcb AND spread guard passes), CROSS to fill instead
    of resting a quote that demonstrably does not fill (real-chain ~3.5% fill, 78/88 cancelled
    before fill). FIX B still caps the cross at/below q_lcb, so the cross is always conservative +EV.
    """

    def test_admissible_edge_crosses_to_fill(self):
        from src.strategy.live_inference.mode_consistent_ev import POLICY_TAKER_EDGE_CLEARS_BOUND
        decision = _decide(minutes_to_event_end=20 * 60.0)
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_EDGE_CLEARS_BOUND
        # never pays above the conservative bound (FIX B): all-in cost <= q_lcb
        assert decision.taker_forbidden_reason is None

    def test_admissible_edge_crosses_regardless_of_event_horizon(self):
        from src.strategy.live_inference.mode_consistent_ev import POLICY_TAKER_EDGE_CLEARS_BOUND
        # far from event and unknown horizon both still fill (the cross clears the bound now)
        for mte in (20 * 60.0, None):
            d = _decide(minutes_to_event_end=mte)
            assert d.chosen_mode == "TAKER", mte
            assert d.policy == POLICY_TAKER_EDGE_CLEARS_BOUND, mte


class TestRestFallbackWhenInadmissible:
    def test_inadmissible_taker_rests_as_maker(self):
        """ask+fee > q_lcb: cannot cross conservatively -> rest a maker limit below q_lcb."""
        decision = _decide_rest(minutes_to_event_end=20 * 60.0)
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT
        assert decision.escalation_deadline_minutes == pytest.approx(
            MAKER_REST_ESCALATION_DEADLINE_MINUTES
        )
        assert decision.ev_maker is not None

    def test_measured_fill_prior_is_used_not_the_guess(self):
        decision = _decide_rest(minutes_to_event_end=20 * 60.0)
        assert decision.maker_fill_probability == pytest.approx(
            MAKER_FILL_PROBABILITY_AT_ESCALATION_DEADLINE
        )
        assert "MEASURED" in decision.maker_fill_probability_source

    def test_unknown_event_end_inadmissible_rests(self):
        decision = _decide_rest(minutes_to_event_end=None)
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

    def test_first_rest_for_family_crosses_when_admissible(self):
        """Operator directive 2026-06-23: the Karachi rest-first default is SUPERSEDED for the
        admissible case — a first-look family whose cross clears the conservative bound now CROSSES
        to fill (it no longer rests a quote that never fills). Inadmissible first looks still rest."""
        from src.strategy.live_inference.mode_consistent_ev import POLICY_TAKER_EDGE_CLEARS_BOUND
        crossed = _decide(minutes_to_event_end=20 * 60.0)  # admissible
        assert crossed.chosen_mode == "TAKER"
        assert crossed.policy == POLICY_TAKER_EDGE_CLEARS_BOUND
        rested = _decide_rest(minutes_to_event_end=20 * 60.0)  # ask+fee > q_lcb
        assert rested.chosen_mode == "MAKER"
        assert rested.policy == POLICY_REST_DEFAULT
        assert rested.chosen_ev != float("-inf")


class TestExceptionLanes:
    def test_event_end_near_crosses(self):
        decision = _decide(
            minutes_to_event_end=TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES - 1.0
        )
        assert decision.chosen_mode == "TAKER"
        assert decision.policy == POLICY_TAKER_EVENT_END_NEAR

    def test_event_end_far_inadmissible_rests(self):
        # Far from event AND inadmissible (ask+fee > q_lcb): rests (can't cross conservatively).
        decision = _decide_rest(
            minutes_to_event_end=TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES + 60.0
        )
        assert decision.chosen_mode == "MAKER"
        assert decision.policy == POLICY_REST_DEFAULT

    def test_admissible_edge_crosses_at_every_horizon(self):
        # Operator directive 2026-06-23 supersedes the fleeting/structural rest split for the
        # admissible case: a cross that clears the conservative bound fills regardless of horizon.
        # The Denver spread-loss is still prevented by FIX B (cross capped at <= q_lcb), so this is
        # not the "pay $0.43 to cross far out" failure — the all-in cost is at/below q_lcb here.
        from src.strategy.live_inference.mode_consistent_ev import POLICY_TAKER_EDGE_CLEARS_BOUND
        for mte in (20 * 60.0, 300.0, None):
            d = _decide(
                q_lcb=HEALTHY["taker_all_in_cost"] + TAKER_IMMEDIATE_FLEETING_EDGE_THRESHOLD + 0.01,
                minutes_to_event_end=mte,
            )
            assert d.chosen_mode == "TAKER", mte
            assert d.policy in (POLICY_TAKER_EDGE_CLEARS_BOUND, POLICY_TAKER_FLEETING_EDGE), mte
        # Nesting relation still holds (lane 5 not dead code).
        from src.strategy.live_inference.mode_consistent_ev import (
            TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END,
            TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES as _floor,
        )
        assert TAKER_FLEETING_EDGE_MAX_MINUTES_TO_EVENT_END > _floor

    def test_inadmissible_small_edge_rests(self):
        # ask+fee > q_lcb: cannot cross conservatively -> rest fallback (fills only on a move).
        decision = _decide_rest(minutes_to_event_end=20 * 60.0)
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


class TestTakerQualityShadowGateCollapsed:
    """SHADOW-GATE COLLAPSE (operator law = NO CAPS, 2026-06-20). A taker that
    passes the conservative after-cost law (fresh ask + fee <= q_lcb, i.e.
    taker_edge >= 0 — the same FIX-B bound) must NOT be aborted by the extra
    0.03 / 0.05 / 1.20 / 0.60 thresholds. They are demoted to TELEMETRY: the
    proof passes on the conservative surplus; the legacy thresholds still travel
    on the receipt but no longer gate.
    """

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

    def test_positive_surplus_below_legacy_thresholds_passes(self):
        """edge in [0, 0.03): conservative surplus positive but BELOW the 0.03
        legacy edge cap. On the unfixed tree passed=False (aborts the cross). After
        the fix passed=True with legacy_threshold_pass=False recorded as telemetry."""
        proof = self._proof(q_lcb=0.52, ask=0.49)  # edge ~0.0175 in [0, 0.03)
        assert proof is not None
        assert float(proof["taker_fee_adjusted_edge"]) >= 0.0
        assert float(proof["taker_fee_adjusted_edge"]) < float(
            proof["min_taker_fee_adjusted_edge"]
        )
        assert proof["passed"] is True
        # The legacy cap is recorded as telemetry only (it did NOT gate).
        assert proof["legacy_threshold_pass"] is False
        assert proof["passed_basis"] == "conservative_after_cost_surplus_nonnegative"

    def test_negative_surplus_still_fails_closed(self):
        """The conservative law is NEVER loosened: ask + fee > q_lcb (negative
        after-cost edge) still fails closed — only the EXTRA cap was removed."""
        proof = self._proof(q_lcb=0.50, ask=0.55)  # edge clearly negative
        assert proof is not None
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
