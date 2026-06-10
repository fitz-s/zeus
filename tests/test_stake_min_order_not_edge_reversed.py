# Created: 2026-06-09
# Last reused or audited: 2026-06-09
# Authority basis: /tmp/edge_reversed_audit.md (2026-06-09 REGRESSION_BUG verdict) +
#   "bin selection.md" §5.2/§5.3 (fractional-Kelly haircut after the ΔU optimizer) +
#   §13 (no-trade gate) + §7 reversal taxonomy + operator directive 2026-06-09
#   (false EDGE_REVERSED from post-optimizer haircut vs venue min order; antibody =
#   distinct SUBMIT_ABORTED_BELOW_MIN_ORDER reason + min-order-aware stake floor).
"""RELATIONSHIP TESTS — the post-optimizer fractional-Kelly haircut vs the venue floor.

THE CROSS-MODULE INVARIANT under test (Module A = the ΔU optimizer's chosen stake
after the fractional-Kelly haircut; Module B = the venue min-order floor + the abort
taxonomy): a candidate with GENUINE positive robust edge at the venue minimum must
NEVER be recorded as EDGE_REVERSED merely because the ×kelly_multiplier haircut shrank
the chosen stake below the venue min order. The property that must hold across that
boundary:

    EDGE_REVERSED  <=>  ΔU <= 0 at EVERY admissible stake INCLUDING the min order.

The regression (audit /tmp/edge_reversed_audit.md): the haircut stake fell below min
order, ``_chosen_stake_execution_price`` raised "below min order" ValueError, the
generic except mapped it to (0.0, None), and GATE 3 emitted a FALSE EDGE_REVERSED —
killing every surviving candidate (140 EDGE_REVERSED rows in one evening) even though
the after-fee edge at min order was +3.5..+14.0 cents/share.

The fix (antibody): the sizing kernel is min-order-aware. When ΔU at the min-order
notional is strictly positive AND min order is within the bankroll-cap guard, the stake
is BUMPED to min order (the fractional-Kelly risk intent is preserved — a sub-$1 min
order on a ~$900 wallet is << 2% of bankroll) and the floor is recorded in provenance.
Otherwise a DISTINCT ``SUBMIT_ABORTED_BELOW_MIN_ORDER`` decision is emitted — never
EDGE_REVERSED, so the regret ledger records the true cause.

These tests are written against the REAL proof -> candidate -> curve -> score -> kernel
-> decision path (no mocks of the sizing math), so they assert the property across the
actual module boundary, not a re-implementation of it.
"""
from __future__ import annotations

import json
from decimal import Decimal

from src.engine import event_reactor_adapter as era
from src.strategy.redecision import (
    CandidateLifecycleState,
    ReversalReason,
    SUBMIT_ABORT_STATES,
)
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Harness: a single-bin YES family driven through the real snapshot-row path.
# Prices are tick-aligned to 0.01 (Hidden #16 curve invariant). A CHEAP top ask
# (0.03) with a razor-thin q_lcb produces a tiny full-Kelly optimal stake, so the
# ×0.125 haircut lands BELOW the venue min order — exactly the audit's regime.
# ---------------------------------------------------------------------------
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


def _kernel(proof, *, bankroll, mult, floor_out=None):
    return era._robust_marginal_utility_stake_and_price(
        family_key="fam",
        selected_proof=proof,
        all_proofs=(proof,),
        extra_exposure_by_bin_id={},
        bankroll_usd=bankroll,
        kelly_multiplier=mult,
        stake_floor_out=floor_out,
    )


def _recapture(proof, *, bankroll, mult, floor_out=None):
    return era._evaluate_submit_recapture_for_selected(
        family_key="fam",
        selected_proof=proof,
        all_proofs=(proof,),
        extra_exposure_by_bin_id={},
        bankroll_usd=bankroll,
        kelly_multiplier=mult,
        forecast_still_current=True,
        stake_floor_out=floor_out,
    )


# ===========================================================================
# 1. POSITIVE EDGE, haircut < min order, ΔU(min) > 0 -> BUMP to min_order, NOT
#    EDGE_REVERSED. The order proceeds at the venue minimum; the floor is recorded.
# ===========================================================================
def test_positive_edge_below_min_order_bumps_to_min_order_not_edge_reversed():
    """A cheap-bin candidate (ask 0.03, razor q_lcb 0.031) whose ×0.125 haircut stake
    falls below the venue min order (5 shares = $0.15) but whose ROBUST ΔU at min order
    is strictly positive, with min order << 2% of a $900 bankroll: the kernel BUMPS the
    stake to min order and records stake_floor=VENUE_MIN_ORDER. It does NOT zero the
    stake (which would trip the false EDGE_REVERSED)."""
    row = _snapshot_row(yes_asks=(("0.03", "10000000"),), min_order="5")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.05, q_lcb_5pct=0.031,
    )
    floor: dict[str, object] = {}
    stake, price = _kernel(proof, bankroll=900.0, mult=0.125, floor_out=floor)

    # min order notional = 5 shares * 0.03 all-in = $0.15 (fee_rate 0).
    assert stake == 0.15, f"stake must be bumped to the $0.15 venue min order, got {stake}"
    assert price is not None, "a bumped order at min order is priced (it is fillable)"
    assert floor.get("stake_floor") == "VENUE_MIN_ORDER"
    assert floor.get("stake_floor_min_order_usd") == 0.15
    assert float(floor.get("stake_floor_delta_u_at_min_order")) > 0.0, (
        "the bump is licensed by a STRICTLY POSITIVE robust ΔU at min order"
    )


def test_positive_edge_below_min_order_decision_is_submit_not_edge_reversed():
    """The same candidate driven through the full submit-recapture decision body: the
    decision MAY SUBMIT (the bumped min-order stake clears price/edge), and the receipt
    provenance carries stake_floor=VENUE_MIN_ORDER. It is NEVER an abort state — and in
    particular never SUBMIT_ABORTED_EDGE_REVERSED."""
    row = _snapshot_row(yes_asks=(("0.03", "10000000"),), min_order="5")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.05, q_lcb_5pct=0.031,
    )
    floor: dict[str, object] = {}
    decision, stake, price = _recapture(proof, bankroll=900.0, mult=0.125, floor_out=floor)

    assert decision.state not in SUBMIT_ABORT_STATES, (
        f"a positive-edge bumped candidate must not abort; got {decision.state}"
    )
    assert decision.state != CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    assert decision.may_submit is True
    assert stake == 0.15
    assert price is not None
    assert floor.get("stake_floor") == "VENUE_MIN_ORDER"


# ===========================================================================
# 2. POSITIVE EDGE but the bump fails the bankroll cap -> SUBMIT_ABORTED_BELOW_MIN_ORDER,
#    NEVER EDGE_REVERSED. (ΔU(min) > 0, but min_order_usd > 2% of bankroll.)
# ===========================================================================
def test_below_min_order_when_min_order_exceeds_bankroll_cap_is_distinct_reason():
    """min order = 1000 shares * 0.03 = $30, which exceeds 2% of a $900 bankroll ($18).
    The robust ΔU at min order is still POSITIVE, but the bump is not admissible within
    the bankroll cap. The kernel raises _StakeBelowMinOrder and the decision body emits
    a DISTINCT SUBMIT_ABORTED_BELOW_MIN_ORDER with ReversalReason.MIN_ORDER — NOT
    EDGE_REVERSED. The regret ledger must record the true (sizing) cause."""
    row = _snapshot_row(yes_asks=(("0.03", "100000000"),), min_order="1000")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.08, q_lcb_5pct=0.05,
    )
    decision, stake, price = _recapture(proof, bankroll=900.0, mult=0.125)

    assert decision.state == CandidateLifecycleState.SUBMIT_ABORTED_BELOW_MIN_ORDER
    assert decision.state != CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    assert decision.reversal_reason == ReversalReason.MIN_ORDER
    assert decision.may_submit is False
    assert stake == 0.0 and price is None
    # The reason maps to a distinct receipt string (the regret ledger taxonomy).
    assert (
        era._SUBMIT_ABORT_RECEIPT_REASON[decision.state]
        == "SUBMIT_ABORTED_BELOW_MIN_ORDER"
    )


def test_kernel_raises_distinct_exception_for_bankroll_cap_fail():
    """At the kernel boundary the bankroll-cap-fail case raises the DISTINCT
    _StakeBelowMinOrder, never a bare ValueError (which the live body would mislabel
    KELLY_PROOF_MISSING) and never a silent (0.0, None) (which GATE 3 would mislabel
    EDGE_REVERSED)."""
    import pytest

    row = _snapshot_row(yes_asks=(("0.03", "100000000"),), min_order="1000")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.08, q_lcb_5pct=0.05,
    )
    with pytest.raises(era._StakeBelowMinOrder):
        _kernel(proof, bankroll=900.0, mult=0.125)


# ===========================================================================
# 3. TRULY REVERSED EDGE at all stakes -> still EDGE_REVERSED (regression guard).
#    q_lcb (0.02) below the all-in cost (0.03): no positive-ΔU stake at ANY size.
# ===========================================================================
def test_truly_reversed_edge_is_still_edge_reversed():
    """A candidate whose robust q_lcb (0.02) is BELOW the all-in cost (0.03) has no
    positive-ΔU stake at ANY admissible size, including min order. It must STILL abort
    as EDGE_REVERSED — the fix narrows EDGE_REVERSED to its true meaning, it does not
    weaken the genuine no-edge gate."""
    row = _snapshot_row(yes_asks=(("0.03", "10000000"),), min_order="5")
    proof = _proof_from_row(
        direction="buy_yes", row=row, token_id="yes-1",
        q_posterior=0.03, q_lcb_5pct=0.02,
    )
    floor: dict[str, object] = {}
    stake, price = _kernel(proof, bankroll=900.0, mult=0.125, floor_out=floor)
    assert stake == 0.0 and price is None
    assert floor == {}, "no stake floor is recorded on a genuine no-edge candidate"

    decision, dstake, dprice = _recapture(proof, bankroll=900.0, mult=0.125)
    assert decision.state == CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    assert decision.reversal_reason == ReversalReason.EDGE
    assert dstake == 0.0 and dprice is None


# ===========================================================================
# 4. TAXONOMY — the new abort state is first-class and distinct (assertable),
#    and BELOW_MIN_ORDER is NOT EDGE_REVERSED (the antibody invariant).
# ===========================================================================
def test_below_min_order_is_first_class_abort_state_distinct_from_edge_reversed():
    assert (
        CandidateLifecycleState.SUBMIT_ABORTED_BELOW_MIN_ORDER in SUBMIT_ABORT_STATES
    )
    assert (
        CandidateLifecycleState.SUBMIT_ABORTED_BELOW_MIN_ORDER
        != CandidateLifecycleState.SUBMIT_ABORTED_EDGE_REVERSED
    )
    # The receipt-reason map covers EXACTLY the abort states (one taxonomy, no orphan).
    assert set(era._SUBMIT_ABORT_RECEIPT_REASON) == set(SUBMIT_ABORT_STATES)


# ===========================================================================
# 5. SECONDARY FIX — the single-token CLOB depth-JSON format parses to the FULL
#    level count, best-price FIRST (the audit's secondary defect).
# ===========================================================================
def test_single_token_depth_json_parses_full_book_best_first():
    """The materializer stores a SINGLE token's raw CLOB /book as orderbook_depth_json:
    ``{"asks":[...], "asset_id":"<token>", "bids":[...]}`` — asks/bids at the TOP level,
    token as asset_id. Pre-fix this matched nothing and the curve degraded to a 1-level
    fallback. Now _depth_for_token_or_label recognizes it and returns the FULL book,
    CANONICALLY SORTED best-price-first (asks ascending, bids descending) so the depth
    walk consumes the best level first regardless of the source array order."""
    # CLOB-native arrays are best-price-LAST: asks here are worst (0.60) first.
    book = {
        "asset_id": "tok-1",
        "asks": [
            {"price": "0.60", "size": "100"},
            {"price": "0.40", "size": "50"},
            {"price": "0.50", "size": "80"},
        ],
        "bids": [
            {"price": "0.30", "size": "10"},
            {"price": "0.38", "size": "20"},
        ],
    }
    out = era._depth_for_token_or_label(book, token_id="tok-1", label="YES")
    assert out is not None, "the single-token CLOB format must be recognized"

    ask_prices = [a["price"] for a in out["asks"]]
    bid_prices = [b["price"] for b in out["bids"]]
    # FULL level count (not the 1-level fallback).
    assert len(ask_prices) == 3 and len(bid_prices) == 2
    # Best-price FIRST: asks ascending (cheapest first), bids descending (highest first).
    assert ask_prices == ["0.40", "0.50", "0.60"]
    assert bid_prices == ["0.38", "0.30"]


def test_single_token_depth_json_token_mismatch_falls_through():
    """A single-token book whose asset_id does NOT match the queried token must fall
    through to None (so the explicit min-order fallback still runs for the right token)
    — the new branch must not hijack a different token's lookup."""
    book = {
        "asset_id": "tok-1",
        "asks": [{"price": "0.40", "size": "50"}],
        "bids": [{"price": "0.30", "size": "10"}],
    }
    assert era._depth_for_token_or_label(book, token_id="other-token", label="YES") is None


def test_single_token_depth_json_feeds_full_depth_into_native_quote_book():
    """End-to-end at the book builder: a snapshot row whose orderbook_depth_json is the
    single-token CLOB format yields a NativeQuoteBook with the FULL multi-level ask
    ladder (best-first), not the 1-level explicit fallback. This widens the executable
    depth the ΔU optimizer sees (the secondary defect's downstream effect)."""
    depth = {
        "asset_id": "yes-1",
        "asks": [
            {"price": "0.05", "size": "30"},
            {"price": "0.03", "size": "20"},
            {"price": "0.04", "size": "25"},
        ],
        "bids": [{"price": "0.02", "size": "10"}],
    }
    row = {
        "snapshot_id": "snap",
        "condition_id": "cond-1",
        "yes_token_id": "yes-1",
        "no_token_id": "no-1",
        "selected_outcome_token_id": "yes-1",
        "orderbook_top_ask": "0.03",
        "depth_at_best_ask": "20",
        "min_tick_size": "0.01",
        "min_order_size": "5",
        "fee_details_json": json.dumps({"fee_rate_fraction": 0.0}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": "{}",
        "book_hash": "bh",
    }
    book = era._native_quote_book_from_snapshot_row(row)
    # Full 3-level ladder, best (cheapest) first — NOT the single-level fallback.
    ask_prices = [str(level.price) for level in book.yes_asks]
    assert ask_prices == ["0.03", "0.04", "0.05"], (
        f"the full sorted ask ladder must reach the curve, got {ask_prices}"
    )
