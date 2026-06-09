# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §12.E (re-decision relationship tests) +
#   §7 (state machine + reversal table + hysteresis) + §3 (reversal types) +
#   §5 submit-recapture pseudocode + §9 Hidden #7 (fallback is WATCH-only) +
#   §11 Phase 5 / §14.9-14.10 + §13 no-trade gates + operator directive 2026-06-08.
"""RELATIONSHIP tests for the Phase-5 re-decision / reversal engine (spec §12.E).

These are RELATIONSHIP tests, not function tests: each asserts a cross-boundary
property that must hold when a recaptured executable curve / a new forecast
distribution / a fallback ordering flows into the candidate lifecycle state
machine. The invariant under test is "given what changed at the boundary, what
terminal/abort state does the candidate land in, and is churn suppressed?".

Mapped to spec §12.E:
  E.1 test_price_jump_abort            -> SUBMIT_ABORTED_PRICE_MOVED
  E.2 test_edge_reversal_abort         -> SUBMIT_ABORTED_EDGE_REVERSED
  E.3 test_forecast_reversal_switches_watch_candidate (re-rank in WATCH)
  E.5 test_fallback_rank_change_requires_full_rerank (Hidden #7)
  + test_hysteresis_prevents_churn     (§7 hysteresis: η_switch AND T_no_churn)

The engine is a PURE state machine (default-off / shadow): no DB, no live
wiring, no import side effects.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

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
    HysteresisPolicy,
    RecaptureInputs,
    RedecisionEngine,
    ReversalReason,
)


# ---------------------------------------------------------------------------
# Builders: real peer contracts so the tests exercise the true boundary.
# ---------------------------------------------------------------------------
def _curve(levels, *, side="YES", token_id="tok-yes", fee_rate="0", snapshot_id="snap-1"):
    return ExecutableCostCurve(
        token_id=token_id,
        side=side,
        snapshot_id=snapshot_id,
        book_hash=f"hash-{snapshot_id}",
        levels=tuple(BookLevel(price=Decimal(p), size=Decimal(s)) for p, s in levels),
        fee_model=FeeModel(fee_rate=Decimal(fee_rate)),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )


def _candidate(
    *,
    bin_id="bin-1",
    side="YES",
    token_id="tok-yes",
    q_samples,
    curve,
    family_key="phl|2026-06-09|tmax",
    market_snapshot_id="msnap-1",
    hypothesis_id="hyp-1",
):
    pu = probability_uncertainty_from_samples(q_samples)
    return NativeSideCandidate.tradeable(
        family_key=family_key,
        bin_id=bin_id,
        side=side,
        token_id=token_id,
        condition_id="cond-1",
        q_point=pu.q_point,
        q_lcb=pu.q_lcb,
        probability_uncertainty=pu,
        executable_cost_curve=curve,
        forecast_snapshot_id="fsnap-1",
        market_snapshot_id=market_snapshot_id,
        hypothesis_id=hypothesis_id,
    )


# A primary candidate that, at decision time, is healthy: q~0.60 well above the
# ~0.40 all-in cost, so robust marginal utility is positive.
def _healthy_primary():
    # Tight YES samples around 0.60 so q_lcb stays well above cost.
    q_samples = [0.58, 0.59, 0.60, 0.60, 0.61, 0.62]
    curve = _curve([("0.40", "1000")])
    return _candidate(q_samples=q_samples, curve=curve)


# ---------------------------------------------------------------------------
# E.1 — price jump abort (recaptured all-in cost exceeds max_acceptable).
# ---------------------------------------------------------------------------
def test_price_jump_abort():
    """Recaptured all-in cost > max_acceptable_price -> SUBMIT_ABORTED_PRICE_MOVED.

    Relationship: a fresh snapshot whose ask has jumped from 0.40 to 0.70
    crosses the candidate's max_acceptable_price. The submit-recapture gate is
    mandatory fail-closed (§5 submit pseudocode / §7 'price move' row): the
    engine must NOT submit; it aborts with PRICE_MOVED.
    """
    engine = RedecisionEngine()
    primary = _healthy_primary()
    stake = Decimal("100")  # fillable on the 1000-share book at any price
    max_acceptable = Decimal("0.45")  # decision-time price was ~0.40

    # Fresh book: ask jumped to 0.70 all-in (> 0.45 max acceptable).
    recaptured = _curve([("0.70", "1000")], snapshot_id="snap-2")

    result = engine.evaluate_submit_recapture(
        primary,
        RecaptureInputs(
            recaptured_cost_curve=recaptured,
            stake_usd=stake,
            max_acceptable_price=max_acceptable,
            recaptured_q_lcb=primary.q_lcb,  # forecast unchanged
            forecast_still_current=True,
            family_rank_reversed=False,
        ),
    )

    assert result.state is CandidateLifecycleState.SUBMIT_ABORTED_PRICE_MOVED
    assert result.reversal_reason is ReversalReason.PRICE
    assert not result.may_submit


# ---------------------------------------------------------------------------
# E.2 — edge reversal abort (edge_lcb <= 0 at recapture -> abort).
# ---------------------------------------------------------------------------
def test_edge_reversal_abort():
    """edge_lcb <= 0 at recapture -> SUBMIT_ABORTED_EDGE_REVERSED.

    Relationship: even when price is within max_acceptable, if the recaptured
    robust edge (q_lcb - all_in_cost) has crossed to <= 0 the candidate's
    utility is nonpositive. §7 'edge move' row: abort if edge_lcb <= 0. The
    price gate alone would not catch this — edge can reverse via q falling while
    price holds.
    """
    engine = RedecisionEngine()
    primary = _healthy_primary()
    stake = Decimal("100")

    # Price still ~0.40 (within max_acceptable 0.45), but recaptured q_lcb has
    # collapsed below the cost -> edge_lcb = q_lcb - cost <= 0.
    recaptured = _curve([("0.40", "1000")], snapshot_id="snap-2")

    result = engine.evaluate_submit_recapture(
        primary,
        RecaptureInputs(
            recaptured_cost_curve=recaptured,
            stake_usd=stake,
            max_acceptable_price=Decimal("0.45"),
            recaptured_q_lcb=0.35,  # below the ~0.40 all-in cost -> edge <= 0
            forecast_still_current=True,
            family_rank_reversed=False,
        ),
    )

    assert result.state is CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    assert result.reversal_reason is ReversalReason.EDGE
    assert not result.may_submit


# ---------------------------------------------------------------------------
# E.3 — forecast reversal re-ranks the WATCH primary.
# ---------------------------------------------------------------------------
def test_forecast_reversal_switches_watch_candidate():
    """A new forecast distribution re-ranks the WATCH primary (§7 'forecast update').

    Relationship: a forecast update that moves the new candidate's robust
    marginal utility ABOVE the current primary by more than η_switch (and past
    the no-churn window) re-ranks the WATCH set so the new candidate becomes
    primary. This is the q-move / forecast-update row of the §7 transition table
    flowing through to family-rank order while the candidate is still in WATCH
    (not yet submitted).
    """
    engine = RedecisionEngine(
        hysteresis=HysteresisPolicy(eta_switch=0.01, t_no_churn=timedelta(seconds=30))
    )

    old_primary = _healthy_primary()  # bin-1
    # Decision-time utilities: old primary clearly ahead.
    decision = engine.rank_watch_set(
        {old_primary.bin_id: 0.05, "bin-2": 0.02},
        primary_bin_id=old_primary.bin_id,
        now_seconds=0.0,
        last_switch_seconds=-1000.0,  # no recent switch -> churn window satisfied
    )
    assert decision.primary_bin_id == old_primary.bin_id

    # Forecast update: bin-2 jumps well above old primary (delta >> eta_switch),
    # churn window long expired.
    reranked = engine.rank_watch_set(
        {old_primary.bin_id: 0.05, "bin-2": 0.20},
        primary_bin_id=old_primary.bin_id,
        now_seconds=100.0,
        last_switch_seconds=0.0,
        trigger=ReversalReason.FORECAST,
    )

    assert reranked.primary_bin_id == "bin-2"
    assert reranked.switched
    assert reranked.reversal_reason is ReversalReason.FORECAST


# ---------------------------------------------------------------------------
# E.5 — fallback cannot auto-submit on primary failure (Hidden #7).
# ---------------------------------------------------------------------------
def test_fallback_rank_change_requires_full_rerank():
    """A fallback candidate cannot auto-submit when the primary aborts (Hidden #7).

    Relationship: when the primary fails submit recapture, the fallback is a
    WATCH-only candidate. It must NOT inherit the primary's submit authority; it
    can only become primary by passing a FULL re-rank (fresh capture +
    probability + FDR + risk + family re-rank). The engine must refuse to mark a
    fallback READY_TO_SUBMIT directly from a primary abort.
    """
    engine = RedecisionEngine()
    fallback = _candidate(
        bin_id="bin-2",
        token_id="tok-yes-2",
        q_samples=[0.55, 0.56, 0.57, 0.58],
        curve=_curve([("0.40", "1000")], token_id="tok-yes-2"),
        hypothesis_id="hyp-2",
    )

    # Primary just aborted; engine is asked to promote the fallback to submit.
    promotion = engine.promote_fallback_on_primary_abort(fallback)

    # Hidden #7: a fallback is WATCH-only; it is NOT auto-submittable.
    assert promotion.state is CandidateLifecycleState.WATCH
    assert not promotion.may_submit
    assert promotion.requires_full_rerank

    # And the engine must reject any attempt to push a WATCH fallback straight to
    # SUBMIT_RECAPTURE_REQUIRED without the intervening full re-rank.
    with pytest.raises(ValueError, match="full re-rank"):
        engine.require_submit_recapture(fallback, became_primary_via_rerank=False)

    # After a full re-rank that genuinely makes it primary, recapture is allowed.
    ready = engine.require_submit_recapture(fallback, became_primary_via_rerank=True)
    assert ready.state is CandidateLifecycleState.SUBMIT_RECAPTURE_REQUIRED


# ---------------------------------------------------------------------------
# Hysteresis — sub-η_switch delta and sub-T_no_churn both suppress the switch.
# ---------------------------------------------------------------------------
def test_hysteresis_prevents_churn():
    """A sub-η_switch utility delta does NOT switch; below T_no_churn does NOT switch.

    §7 hysteresis (BOTH conditions required to switch):
        ΔU_new > ΔU_old + η_switch   AND   (t - t_last_switch) > T_no_churn

    Two independent failure paths, each must block the switch:
      (a) the new candidate beats the primary but by LESS than η_switch
          (noise-level edge) -> no switch even though the churn window passed.
      (b) the new candidate beats the primary by MORE than η_switch but the
          no-churn window has NOT expired -> no switch (anti flip-flop).
    """
    engine = RedecisionEngine(
        hysteresis=HysteresisPolicy(eta_switch=0.05, t_no_churn=timedelta(seconds=30))
    )

    # (a) sub-η_switch delta: new 0.12 vs old 0.10 -> delta 0.02 < η_switch 0.05.
    #     Churn window satisfied (1000s since last switch). Must NOT switch.
    sub_eta = engine.rank_watch_set(
        {"bin-1": 0.10, "bin-2": 0.12},
        primary_bin_id="bin-1",
        now_seconds=1000.0,
        last_switch_seconds=0.0,
    )
    assert sub_eta.primary_bin_id == "bin-1"
    assert not sub_eta.switched

    # (b) delta large (0.30 vs 0.10 -> 0.20 > η_switch) but only 5s since the
    #     last switch (< T_no_churn 30s). Must NOT switch (no-churn window).
    in_churn = engine.rank_watch_set(
        {"bin-1": 0.10, "bin-2": 0.30},
        primary_bin_id="bin-1",
        now_seconds=5.0,
        last_switch_seconds=0.0,
    )
    assert in_churn.primary_bin_id == "bin-1"
    assert not in_churn.switched

    # Control: same large delta AFTER the churn window expires -> switches.
    after_churn = engine.rank_watch_set(
        {"bin-1": 0.10, "bin-2": 0.30},
        primary_bin_id="bin-1",
        now_seconds=100.0,
        last_switch_seconds=0.0,
    )
    assert after_churn.primary_bin_id == "bin-2"
    assert after_churn.switched
