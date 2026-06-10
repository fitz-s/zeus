# Created: 2026-06-10
# Lifecycle: created=2026-06-10; last_reviewed=2026-06-10; last_reused=2026-06-10
# Purpose: Merge-blocking tests for PR#404 P0-1 — the day0 exposure cap as a
#          sizing-kernel feasible-region bound (never a post-hoc clamp).
# Reuse: Real proof -> curve -> kernel -> recapture-decision path (no sizing mocks);
#        harness mirrors tests/test_stake_min_order_not_edge_reversed.py.
# Last reused or audited: 2026-06-10
# Authority basis: /Users/leofitz/Downloads/pr404.md P0 merge blocker 1 (operator):
#   the post-hoc _apply_day0_exposure_cap could emit a stake below the SELECTED
#   market's REAL venue min order ('headroom < $1' is a wrong risk boundary —
#   min order notional = min_order_size x all-in price, $0.15..$30+), and the
#   downstream receipt/cost-basis/reservation surfaces would treat that stake
#   as sizing-proven. Required semantics: headroom <= 0 -> no-submit
#   DAY0_EXPOSURE_CAP_EXHAUSTED; headroom < real min order -> no-submit
#   DAY0_EXPOSURE_CAP_BELOW_MIN_ORDER; else re-optimize within
#   [min_order, min(headroom, other caps)] and REPRICE at the chosen stake;
#   invariant final + existing <= cap + epsilon.
"""RELATIONSHIP TESTS — the day0 family notional cap inside the sizing kernel.

Cross-module invariant (Module A = ΔU sizing kernel; Module B = the day0
exposure-cap risk bound): the cap participates in the kernel's feasible region
so the CHOSEN stake and its CHOSEN-STAKE EXECUTION PRICE are produced together
— a capped stake is never an unpriced after-the-fact number, and no emitted
stake can sit below the selected market's real venue min-order notional.
"""
from __future__ import annotations

import json

import pytest

from src.engine import event_reactor_adapter as era
from src.strategy.redecision import (
    SUBMIT_ABORT_STATES,
    CandidateLifecycleState,
    ReversalReason,
)
from src.types.market import Bin

_BIN_X = Bin(low=60.0, high=61.0, unit="F", label="60-61F")


def _snapshot_row(*, yes_asks, min_order="5", fee_rate_fraction=0.0):
    depth = {
        "YES": {
            "asks": [{"price": p, "size": s} for p, s in yes_asks],
            "bids": [{"price": "0.01", "size": "100"}],
        },
        "NO": {
            "asks": [{"price": "0.95", "size": "100000"}],
            "bids": [{"price": "0.40", "size": "100"}],
        },
    }
    return {
        "snapshot_id": "snap",
        "condition_id": "cond-1",
        "yes_token_id": "yes-1",
        "no_token_id": "no-1",
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.01",
        "min_order_size": min_order,
        "fee_details_json": json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": "bh",
    }


def _proof_from_row(*, direction, row, token_id, q_posterior, q_lcb_5pct, bin_obj=_BIN_X):
    from src.events.candidate_binding import MarketTopologyCandidate

    ep, _pf, _c = era._execution_price_from_snapshot(
        row, selected_token_id=token_id, direction=direction
    )
    return era._CandidateProof(
        candidate=MarketTopologyCandidate(
            city="paris",
            target_date="2026-06-10",
            metric="tmax",
            condition_id=str(row.get("condition_id") or ""),
            yes_token_id=str(row.get("yes_token_id") or ""),
            no_token_id=str(row.get("no_token_id") or ""),
            bin=bin_obj,
        ),
        token_id=token_id,
        direction=direction,
        row=row,
        executable_snapshot_id=str(row.get("snapshot_id") or ""),
        execution_price=ep,
        q_posterior=q_posterior,
        q_lcb_5pct=q_lcb_5pct,
        c_cost_95pct=None,
        p_fill_lcb=1.0,
        trade_score=1.0,
        p_value=0.01,
        passed_prefilter=True,
        native_quote_available=True,
        p_cal_vector_hash="ch",
        p_live_vector_hash="lh",
        missing_reason=None,
    )


def _strong_edge_proof(*, min_order="5", yes_asks=(("0.30", "10000000"),)):
    """A strong-edge candidate whose unconstrained fractional-Kelly stake is
    LARGE (q_lcb 0.85 vs ask 0.30) so the day0 headroom is the binding bound."""
    row = _snapshot_row(yes_asks=yes_asks, min_order=min_order)
    return _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.90, q_lcb_5pct=0.85,
    )


def _kernel(proof, *, bankroll=900.0, mult=0.125, headroom=None):
    return era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=proof,
        all_proofs=(proof,),
        extra_exposure_by_bin_id={},
        bankroll_usd=bankroll,
        kelly_multiplier=mult,
        day0_headroom_usd=headroom,
    )


def _recapture(proof, *, bankroll=900.0, mult=0.125, headroom=None):
    return era._evaluate_submit_recapture_for_selected(
        family_key="fam",
        selected_proof=proof,
        all_proofs=(proof,),
        extra_exposure_by_bin_id={},
        bankroll_usd=bankroll,
        kelly_multiplier=mult,
        forecast_still_current=True,
        day0_headroom_usd=headroom,
    )


# ===========================================================================
# 1. THE OPERATOR'S BOUNDARY: headroom $20, REAL venue min order $30
#    (100 shares x $0.30 all-in). Must be a first-class no-submit — the old
#    post-clamp would have emitted a $20 stake BELOW the venue floor.
# ===========================================================================

def test_headroom_above_one_dollar_but_below_real_min_order_is_no_submit():
    proof = _strong_edge_proof(min_order="100")  # 100 * 0.30 = $30 min notional
    decision, stake, price = _recapture(proof, headroom=20.0)

    assert decision.may_submit is False
    assert decision.state is CandidateLifecycleState.SUBMIT_ABORTED_DAY0_CAP_BELOW_MIN_ORDER
    assert decision.state in SUBMIT_ABORT_STATES
    assert decision.reversal_reason is ReversalReason.DAY0_CAP
    assert stake == 0.0 and price is None
    # receipt reason taxonomy (operator-named)
    assert era._SUBMIT_ABORT_RECEIPT_REASON[decision.state] == "DAY0_EXPOSURE_CAP_BELOW_MIN_ORDER"


def test_kernel_raises_distinct_below_min_order_cap_error():
    proof = _strong_edge_proof(min_order="100")
    with pytest.raises(era._Day0CapBelowMinOrder):
        _kernel(proof, headroom=20.0)


# ===========================================================================
# 2. Exhausted headroom -> DAY0_EXPOSURE_CAP_EXHAUSTED (never an edge verdict).
# ===========================================================================

def test_exhausted_headroom_is_first_class_no_submit():
    proof = _strong_edge_proof()
    decision, stake, price = _recapture(proof, headroom=0.0)

    assert decision.may_submit is False
    assert decision.state is CandidateLifecycleState.SUBMIT_ABORTED_DAY0_CAP_EXHAUSTED
    assert decision.reversal_reason is ReversalReason.DAY0_CAP
    assert decision.state != CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    assert stake == 0.0 and price is None
    assert era._SUBMIT_ABORT_RECEIPT_REASON[decision.state] == "DAY0_EXPOSURE_CAP_EXHAUSTED"


# ===========================================================================
# 3. Binding headroom: stake optimized WITHIN the cap and REPRICED at the
#    chosen stake (operator: 'cap 改变 stake 时必须重新计算 execution price').
#    Two-level book makes the avg cost stake-dependent, so a capped (smaller)
#    stake MUST price cheaper than the uncapped one — proof the price was
#    recomputed at the final stake, not carried from the pre-cap stake.
# ===========================================================================

def test_binding_headroom_caps_stake_and_reprices_at_chosen_stake():
    asks = (("0.30", "50"), ("0.50", "100000"))  # $15 at 0.30, then 0.50
    # KERNEL level (sizing+pricing, before the price-ceiling gate): the
    # uncapped stake walks the book into the 0.50 level; the capped ($10)
    # stake fills entirely at 0.30. A cheaper capped avg cost proves the
    # price was RECOMPUTED at the final (capped) stake, never carried over
    # from the pre-cap depth walk.
    stake_u, price_u = _kernel(_strong_edge_proof(yes_asks=asks), headroom=None)
    assert stake_u > 10.0 and price_u is not None

    stake_c, price_c = _kernel(_strong_edge_proof(yes_asks=asks), headroom=10.0)
    assert stake_c == pytest.approx(10.0)  # the headroom bound binds
    assert price_c is not None
    assert price_c.value < price_u.value, (
        "capped stake must be REPRICED on its own depth walk (cheaper avg cost), "
        f"got capped={price_c.value} uncapped={price_u.value}"
    )

    # FULL recapture-decision path: the capped stake prices within the admitted
    # band (0.30 top ask) and submits with the SAME chosen-stake price the
    # kernel produced — receipt price and final stake are one artifact.
    decision, stake_r, price_r = _recapture(_strong_edge_proof(yes_asks=asks), headroom=10.0)
    assert decision.may_submit is True
    assert stake_r == pytest.approx(10.0)
    assert price_r is not None and price_r.value == pytest.approx(price_c.value)


def test_cap_invariant_final_plus_existing_never_exceeds_cap():
    """final_notional + existing_family_notional <= cap + epsilon, for a sweep
    of headrooms (the kernel asserts it internally; this pins it externally)."""
    for headroom in (1.0, 5.0, 10.0, 25.0, 100.0):
        proof = _strong_edge_proof(min_order="1")  # min notional $0.30
        decision, stake, _price = _recapture(proof, headroom=headroom)
        if decision.may_submit:
            assert stake <= headroom + 1e-6


# ===========================================================================
# 4. Non-binding headroom and the forecast lane (headroom=None) are untouched.
# ===========================================================================

def test_non_binding_headroom_matches_uncapped_sizing():
    p1 = _strong_edge_proof()
    p2 = _strong_edge_proof()
    _d1, stake_uncapped, price_uncapped = _recapture(p1, headroom=None)
    _d2, stake_capped, price_capped = _recapture(p2, headroom=10_000.0)
    assert stake_capped == pytest.approx(stake_uncapped)
    assert (price_capped is None) == (price_uncapped is None)
    if price_capped is not None:
        assert price_capped.value == pytest.approx(price_uncapped.value)
