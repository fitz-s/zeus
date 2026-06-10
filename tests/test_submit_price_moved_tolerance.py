# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: "bin selection.md" §7 (price row, no UNBOUNDED chasing) +
#   §5 submit pseudocode + operator directive 2026-06-10 (bounded slippage
#   tolerance) + operator design input 2026-06-10 (maker rests at admitted price,
#   PRICE_MOVED is a taker-only protection) + iron rule (never weaken edge gate).
"""RELATIONSHIP tests for the bounded price-move tolerance on submit recapture.

Live false-abort class observed 2026-06-10: a sub-3¢ book drifting ±1 tick between
scoring and recapture aborted SUBMIT_ABORTED_PRICE_MOVED for a microscopic move,
even though (a) the order rests as a GTC maker at the admitted price (never chasing
the recaptured ask) and (b) GATE 3 (edge_lcb on the FRESH cost) still protects the
economics. These tests pin the cross-boundary properties of the fix:

  * MAKER (order_rests_at_admitted_price=True): a recaptured ask STRICTLY WORSE than
    admitted does NOT abort — the resting limit pays the admitted price; the entry
    proceeds with price_moved_within_tolerance provenance.
  * TAKER (order_rests_at_admitted_price=False): a micro-move within the bounded
    tolerance (one tick / 5% / 1¢ cap) proceeds; a move BEYOND the ceiling still
    aborts PRICE_MOVED (regression guard — no UNBOUNDED chasing).
  * IRON RULE: a tolerated/rested price move that flips the recaptured edge negative
    still aborts EDGE_REVERSED (never PRICE_MOVED, never a negative-edge submit).
  * ABSOLUTE CHASE CAP: on an expensive book the 1¢ cap binds before 5% relative.

The engine is the pure §7 state machine — no DB, no live wiring.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from src.contracts.executable_cost_curve import (
    BookLevel,
    ExecutableCostCurve,
    FeeModel,
)
from src.contracts.native_side_candidate import NativeSideCandidate
from src.strategy.probability_uncertainty import (
    probability_uncertainty_from_samples,
)
from src.strategy.redecision import (
    CandidateLifecycleState,
    RecaptureInputs,
    RedecisionEngine,
    ReversalReason,
)


# ---------------------------------------------------------------------------
# Builders (real peer contracts so the boundary is exercised).
# ---------------------------------------------------------------------------
def _curve(levels, *, side="YES", token_id="tok-yes", fee_rate="0", snapshot_id="snap-1", min_tick="0.001"):
    return ExecutableCostCurve(
        token_id=token_id,
        side=side,
        snapshot_id=snapshot_id,
        book_hash=f"hash-{snapshot_id}",
        levels=tuple(BookLevel(price=Decimal(p), size=Decimal(s)) for p, s in levels),
        fee_model=FeeModel(fee_rate=Decimal(fee_rate)),
        min_tick=Decimal(min_tick),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )


def _candidate(*, q_samples, curve):
    pu = probability_uncertainty_from_samples(q_samples)
    return NativeSideCandidate.tradeable(
        family_key="phl|2026-06-10|tmax",
        bin_id="bin-1",
        side="YES",
        token_id="tok-yes",
        condition_id="cond-1",
        q_point=pu.q_point,
        q_lcb=pu.q_lcb,
        probability_uncertainty=pu,
        executable_cost_curve=curve,
        forecast_snapshot_id="fsnap-1",
        market_snapshot_id="msnap-1",
        hypothesis_id="hyp-1",
    )


# A cheap-tail primary: q_lcb ~0.37 well above a ~0.025 all-in cost (edge ~ +34¢),
# matching the live Lucknow/Seoul/Singapore profile (cheap NO tails, large edge).
def _cheap_tail_primary(cost="0.025"):
    q_samples = [0.36, 0.37, 0.38, 0.37, 0.38, 0.39]
    return _candidate(q_samples=q_samples, curve=_curve([(cost, "1000")]))


# ---------------------------------------------------------------------------
# MAKER: a strictly-worse recapture rests at the admitted price (no abort).
# ---------------------------------------------------------------------------
def test_maker_micro_move_rests_at_admitted_price_no_abort():
    """order_rests_at_admitted_price=True: recaptured ask worse than admitted -> proceed.

    Relationship: a resting GTC maker order pays its OWN limit (the admitted price)
    and rests when the ask drifts away — it never chases. The recaptured ask being
    strictly worse than admitted is therefore NOT a price we pay, and PRICE_MOVED
    must NOT fire. The Singapore live case: admitted 0.010495, recaptured 0.010983
    (a ~0.05¢ adverse drift), edge still ≈ q_lcb - cost ≈ +36¢.
    """
    engine = RedecisionEngine()
    primary = _cheap_tail_primary(cost="0.011")
    # Fresh book priced strictly worse than the admitted ceiling.
    recaptured = _curve([("0.011", "1000")], snapshot_id="snap-2")

    result = engine.evaluate_submit_recapture(
        primary,
        RecaptureInputs(
            recaptured_cost_curve=recaptured,
            stake_usd=Decimal("10"),
            max_acceptable_price=Decimal("0.010495"),  # admitted < recaptured 0.011
            recaptured_q_lcb=primary.q_lcb,
            forecast_still_current=True,
            family_rank_reversed=False,
            order_rests_at_admitted_price=True,  # MAKER rests
        ),
    )

    assert result.may_submit
    assert result.state is CandidateLifecycleState.READY_TO_SUBMIT
    # Provenance: the recapture WAS worse than admitted but the maker rested.
    assert result.price_moved_within_tolerance is True
    assert result.admitted_price == 0.010495
    assert result.recaptured_all_in_cost is not None
    assert result.recaptured_all_in_cost > result.admitted_price


# ---------------------------------------------------------------------------
# TAKER: a micro-move within the bounded tolerance proceeds at recaptured price.
# ---------------------------------------------------------------------------
def test_taker_micro_move_within_tolerance_proceeds():
    """order_rests_at_admitted_price=False: a sub-tick adverse move proceeds (within tolerance).

    Relationship: a taker order pays the recaptured cost, so the ceiling applies —
    but with a bounded tolerance (one tick / 5% / 1¢). A move of one tick worse than
    admitted is within tolerance: the gate proceeds rather than false-aborting on
    stale-snapshot drift. The chosen price the intent carries is the recaptured cost.
    """
    engine = RedecisionEngine()
    primary = _cheap_tail_primary(cost="0.025")
    # admitted 0.025, recaptured 0.026 (one min_tick=0.001 worse) -> within tolerance.
    recaptured = _curve([("0.026", "1000")], snapshot_id="snap-2", min_tick="0.001")

    result = engine.evaluate_submit_recapture(
        primary,
        RecaptureInputs(
            recaptured_cost_curve=recaptured,
            stake_usd=Decimal("10"),
            max_acceptable_price=Decimal("0.025"),
            recaptured_q_lcb=primary.q_lcb,  # edge stays hugely positive
            forecast_still_current=True,
            family_rank_reversed=False,
            order_rests_at_admitted_price=False,  # TAKER crosses
        ),
    )

    assert result.may_submit
    assert result.state is CandidateLifecycleState.READY_TO_SUBMIT
    assert result.price_moved_within_tolerance is True
    # The chosen price the intent will carry is the RECAPTURED cost (we pay reality).
    assert result.recaptured_all_in_cost is not None
    assert abs(result.recaptured_all_in_cost - 0.026) < 1e-9


# ---------------------------------------------------------------------------
# TAKER: a move BEYOND the bounded tolerance still aborts PRICE_MOVED.
# ---------------------------------------------------------------------------
def test_taker_move_beyond_tolerance_aborts_price_moved():
    """A taker recapture beyond the bounded ceiling aborts PRICE_MOVED (regression guard).

    Relationship: the bound is NOT unconditional. A ~15% adverse move (admitted
    0.025, recaptured 0.030) exceeds max(one_tick 0.001, 5% = 0.00125) = 0.00125 and
    the 1¢ cap, so the ceiling 0.02625 is crossed -> PRICE_MOVED. §7 'no UNBOUNDED
    chasing' is preserved.
    """
    engine = RedecisionEngine()
    primary = _cheap_tail_primary(cost="0.025")
    recaptured = _curve([("0.030", "1000")], snapshot_id="snap-2", min_tick="0.001")

    result = engine.evaluate_submit_recapture(
        primary,
        RecaptureInputs(
            recaptured_cost_curve=recaptured,
            stake_usd=Decimal("10"),
            max_acceptable_price=Decimal("0.025"),
            recaptured_q_lcb=primary.q_lcb,
            forecast_still_current=True,
            family_rank_reversed=False,
            order_rests_at_admitted_price=False,
        ),
    )

    assert not result.may_submit
    assert result.state is CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED
    assert result.reversal_reason is ReversalReason.PRICE


# ---------------------------------------------------------------------------
# IRON RULE: a tolerated/rested move that flips the edge negative -> EDGE_REVERSED.
# ---------------------------------------------------------------------------
def test_tolerated_move_that_flips_edge_aborts_edge_reversed():
    """A within-ceiling price move whose recaptured edge <= 0 aborts EDGE_REVERSED.

    Relationship (CRITICAL INVARIANT): the price tolerance governs ONLY the price
    ceiling, never the edge sign. GATE 3 re-checks edge_lcb = q_lcb - recaptured_cost
    on the SAME recaptured cost the tolerance admitted. If q_lcb has fallen so the
    recaptured edge is nonpositive, the entry aborts EDGE_REVERSED — NOT PRICE_MOVED,
    and NEVER a negative-edge submit. Tested on BOTH maker and taker so the maker
    skip-ceiling path cannot smuggle a negative-edge entry through either.
    """
    engine = RedecisionEngine()
    primary = _cheap_tail_primary(cost="0.025")
    # Price moved within tolerance (0.026), but q_lcb collapsed below the cost.
    recaptured = _curve([("0.026", "1000")], snapshot_id="snap-2", min_tick="0.001")

    for rests in (False, True):
        result = engine.evaluate_submit_recapture(
            primary,
            RecaptureInputs(
                recaptured_cost_curve=recaptured,
                stake_usd=Decimal("10"),
                max_acceptable_price=Decimal("0.025"),
                recaptured_q_lcb=0.020,  # below the 0.026 recaptured cost -> edge < 0
                forecast_still_current=True,
                family_rank_reversed=False,
                order_rests_at_admitted_price=rests,
            ),
        )
        assert not result.may_submit, f"rests={rests}"
        assert result.state is CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED, (
            f"rests={rests}: tolerated/rested move with negative edge must abort "
            f"EDGE_REVERSED, got {result.state}"
        )
        assert result.reversal_reason is ReversalReason.EDGE


# ---------------------------------------------------------------------------
# ABSOLUTE CHASE CAP binds on expensive books (taker).
# ---------------------------------------------------------------------------
def test_absolute_cap_binds_on_expensive_book():
    """On an expensive book the 1¢ absolute cap binds before 5% relative.

    Relationship: admitted 0.50, recaptured 0.515. 5% relative = 0.025 > the 1¢ cap,
    so tolerance = min(max(one_tick, 0.025), 0.01) = 0.01; ceiling = 0.51. The
    recaptured 0.515 exceeds 0.51 -> PRICE_MOVED, EVEN THOUGH the edge is fine. The
    absolute cap prevents a relative band from licensing a large-dollar chase.
    """
    engine = RedecisionEngine()
    # Healthy primary with q_lcb well above 0.515 so it is NOT an edge reversal.
    primary = _candidate(
        q_samples=[0.78, 0.79, 0.80, 0.80, 0.81, 0.82],
        curve=_curve([("0.50", "1000")], min_tick="0.01"),
    )
    recaptured = _curve([("0.515", "1000")], snapshot_id="snap-2", min_tick="0.005")

    result = engine.evaluate_submit_recapture(
        primary,
        RecaptureInputs(
            recaptured_cost_curve=recaptured,
            stake_usd=Decimal("10"),
            max_acceptable_price=Decimal("0.50"),
            recaptured_q_lcb=primary.q_lcb,  # ~0.78, edge vs 0.515 strongly positive
            forecast_still_current=True,
            family_rank_reversed=False,
            order_rests_at_admitted_price=False,  # taker: cap must bind
        ),
    )

    assert not result.may_submit
    assert result.state is CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED
    assert result.reversal_reason is ReversalReason.PRICE
    # The recorded tolerance is exactly the 1¢ cap (proof the cap bound, not 5%).
    assert result.price_move_tolerance == 0.01


# ---------------------------------------------------------------------------
# A clean (non-adverse) recapture does not set the tolerance-consumed flag.
# ---------------------------------------------------------------------------
def test_clean_recapture_does_not_flag_tolerance():
    """A recapture at-or-better-than admitted leaves price_moved_within_tolerance False.

    Provenance hygiene: the flag means "a tolerance was actually consumed / the order
    rested through an adverse move". A clean fill (recaptured == admitted) is the
    normal path and must not be tagged, so settlement attribution measures only the
    genuinely-tolerated population.
    """
    engine = RedecisionEngine()
    primary = _cheap_tail_primary(cost="0.025")
    recaptured = _curve([("0.025", "1000")], snapshot_id="snap-2", min_tick="0.001")

    result = engine.evaluate_submit_recapture(
        primary,
        RecaptureInputs(
            recaptured_cost_curve=recaptured,
            stake_usd=Decimal("10"),
            max_acceptable_price=Decimal("0.025"),
            recaptured_q_lcb=primary.q_lcb,
            forecast_still_current=True,
            family_rank_reversed=False,
            order_rests_at_admitted_price=True,
        ),
    )

    assert result.may_submit
    assert result.price_moved_within_tolerance is False
