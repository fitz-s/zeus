# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator throughput-unlock directive 2026-06-10 (maker-quote lane
#   for buy_no with empty/illiquid NO ask) + FIX C mode-consistent EV
#   (src/strategy/live_inference/mode_consistent_ev.py) + the EXECUTABLE_NATIVE_ASK_MISSING
#   choke (event_reactor_adapter.py L1862, _execution_price_from_snapshot L11721,
#   market_scanner.py:2900 clob_no_ask_illiquid). "我们的系统本质上是maker制作的":
#   a maker refusing to QUOTE into an empty book is self-contradictory — RESTING a
#   NO bid at p is economically matched by YES buyers at 1-p (mint/merge).
"""Relationship tests (cross-module invariants, red-first) — maker-quote lane.

Modules at the seam:
  A = src.strategy.live_inference.mode_consistent_ev
        (complementary_maker_quote_reservation + select_mode_consistent_ev maker leg)
  B = src.engine.event_reactor_adapter._execution_price_from_snapshot
        (PRODUCES execution_price; previously raised -> None -> NATIVE_ASK_MISSING)
  C = src.engine.event_reactor_adapter._mode_consistent_ev_for_proof / proof gen

Invariant under test: a certified buy_no candidate whose NATIVE NO ask side is
empty/thin but whose COMPLEMENTARY YES book has a live bid is NOT dead. It becomes
a MAKER-QUOTE candidate:
  - execution_price exists (a bid-type, fee_deducted maker quote, NOT a taker ask),
  - execution_mode_intent == MAKER (taker impossible),
  - taker_forbidden_reason == NO_ASK_EMPTY,
  - maker limit <= min(reservation, 1 - yes_best_bid - tick)  (never crosses YES via mint),
  - edge is computed from the QUOTE price (a candidate whose reservation gives
    non-positive edge still rejects),
  - fail-closed: BOTH books empty/stale -> still non-executable.
"""
from __future__ import annotations

import json

import pytest

from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as era
from src.strategy.live_inference.mode_consistent_ev import (
    TAKER_FORBIDDEN_NO_ASK_EMPTY,
    complementary_maker_quote_reservation,
    select_mode_consistent_ev,
)


# ---------------------------------------------------------------------------
# Snapshot-row fixture (same shape S1/S3 tests use).
# ---------------------------------------------------------------------------
def _row(
    *,
    yes_asks=(("0.40", "1000"),),
    no_asks=(("0.55", "1000"),),
    yes_bids=(("0.39", "100"),),
    no_bids=(("0.19", "100"),),
    min_tick="0.01",
    min_order="5",
    fee_rate_fraction=0.0,
    snapshot_id="snap-mq",
    tradeability_status_json="{}",
):
    depth = {
        "YES": {
            "asks": [{"price": p, "size": s} for p, s in yes_asks],
            "bids": [{"price": p, "size": s} for p, s in yes_bids],
        },
        "NO": {
            "asks": [{"price": p, "size": s} for p, s in no_asks],
            "bids": [{"price": p, "size": s} for p, s in no_bids],
        },
    }
    return {
        "snapshot_id": snapshot_id,
        "condition_id": "condition-1",
        "yes_token_id": "yes-1",
        "no_token_id": "no-1",
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": min_tick,
        "min_order_size": min_order,
        "fee_details_json": json.dumps({"fee_rate_fraction": fee_rate_fraction}),
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": tradeability_status_json,
        "book_hash": "book-hash-mq",
    }


# ===========================================================================
# Pure-module invariants (A) — complementary_maker_quote_reservation
# ===========================================================================
class TestComplementaryReservation:
    def test_bounds_by_complement_bid(self):
        # yes_best_bid 0.39, tick 0.01 -> complement cap = 1 - 0.39 - 0.01 = 0.60.
        # belief q_lcb 0.85 -> reservation capped at 0.60 (the non-crossing bound).
        r = complementary_maker_quote_reservation(
            direction="buy_no", q_lcb=0.85, complement_best_bid=0.39, tick_size=0.01
        )
        assert r == pytest.approx(0.60)

    def test_belief_binds_when_below_complement_cap(self):
        # belief 0.30 < complement cap 0.60 -> reservation is the belief minus penalty.
        r = complementary_maker_quote_reservation(
            direction="buy_no", q_lcb=0.30, complement_best_bid=0.39, tick_size=0.01,
            penalty=0.01,
        )
        assert r == pytest.approx(0.29)

    def test_no_complement_bid_uses_belief_alone(self):
        r = complementary_maker_quote_reservation(
            direction="buy_no", q_lcb=0.50, complement_best_bid=None, tick_size=0.01
        )
        assert r == pytest.approx(0.50)

    def test_complement_too_rich_leaves_no_room(self):
        # yes_best_bid 0.999 -> cap = 1 - 0.999 - 0.01 < 0 -> no admissible quote.
        assert (
            complementary_maker_quote_reservation(
                direction="buy_no", q_lcb=0.50, complement_best_bid=0.999, tick_size=0.01
            )
            is None
        )

    def test_non_positive_belief_no_quote(self):
        assert (
            complementary_maker_quote_reservation(
                direction="buy_no", q_lcb=0.0, complement_best_bid=0.39, tick_size=0.01
            )
            is None
        )


# ===========================================================================
# (a) certified buy_no + empty NO ask + YES bid -> MAKER proof, bounded limit
# ===========================================================================
def test_empty_no_ask_with_yes_bid_prices_maker_quote():
    """B: a buy_no proof whose NO ask is empty but YES bid is live is PRICED as a
    maker quote (execution_price exists), not routed to NATIVE_ASK_MISSING."""
    row = _row(no_asks=(), yes_bids=(("0.39", "100"),))
    ep, p_fill, c95 = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    assert isinstance(ep, ExecutionPrice)
    # The quote price is a maker bid bounded by 1 - yes_best_bid - tick = 0.60.
    assert ep.value <= 0.60 + 1e-9
    assert ep.value > 0.0
    # The all-in cost (c95) is the QUOTE price, NOT a fictitious ask + tick walk.
    assert c95 == pytest.approx(ep.value)
    # Kelly-safe: a maker quote pays zero taker fee, so the limit IS the all-in cost.
    ep.assert_kelly_safe()


def test_empty_no_ask_proof_is_maker_intent_taker_forbidden():
    """C: the generated proof carries execution_mode_intent=MAKER and
    taker_forbidden_reason=NO_ASK_EMPTY (taker is structurally impossible)."""
    row = _row(no_asks=(), yes_bids=(("0.39", "100"),))
    ep, _p, c95 = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    mode_ev = era._mode_consistent_ev_for_proof(
        row=row,
        direction="buy_no",
        q_lcb=0.85,
        execution_price=ep,
        c_cost_95pct=c95,
        p_fill_lcb=_p_or(0.0, _p),
    )
    assert mode_ev is not None
    assert mode_ev.chosen_mode == "MAKER"
    assert mode_ev.taker_forbidden_reason is not None
    assert TAKER_FORBIDDEN_NO_ASK_EMPTY in str(mode_ev.taker_forbidden_reason)
    # The maker limit never crosses the YES side via mint: limit <= 1 - 0.39 - tick.
    assert mode_ev.maker_limit_price is not None
    assert mode_ev.maker_limit_price <= 0.60 + 1e-9


def _p_or(default, value):
    return value if isinstance(value, (int, float)) else default


# ===========================================================================
# (b) gate emits NATIVE_ASK_MISSING ONLY when NO complementary liquidity exists
# ===========================================================================
def test_no_ask_and_no_yes_bid_still_native_ask_missing():
    """Fail-closed: empty NO ask AND empty YES bid (no complementary liquidity at
    all) -> still unpriced (the maker lane has nothing to quote behind)."""
    row = _row(no_asks=(), yes_bids=(), yes_asks=(), no_bids=())
    with pytest.raises(ValueError):
        era._execution_price_from_snapshot(
            row, selected_token_id="no-1", direction="buy_no"
        )


def test_liquid_no_ask_unchanged_taker_path():
    """Regression: a liquid NO ask still prices via the TAKER depth-walk
    (byte-identical to legacy), NOT the maker-quote lane."""
    row = _row(no_asks=(("0.55", "1000"),), yes_bids=(("0.39", "100"),))
    ep, _p, c95 = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    # The taker price walks the 0.55 NO ask (not a 1 - yes_bid maker quote).
    assert ep.value == pytest.approx(0.55)
    # c95 = ask + tick (the taker depth-walk cost), distinct from the quote price.
    assert c95 == pytest.approx(0.56)


# ===========================================================================
# (c) edge computed from QUOTE price: non-positive edge still rejects
# ===========================================================================
def test_quote_edge_non_positive_still_rejects():
    """The maker EV is computed from the resting LIMIT price as cost (never a
    fictitious cheap ask). A candidate whose belief (q_lcb) does not clear the
    actual resting limit + penalty has non-positive maker EV.

    The resting limit here is bid-improving on the NO bid (0.19 + tick = 0.20),
    capped by the complementary YES bound (1 - 0.39 - tick = 0.60). The binding
    cost is therefore 0.20. A belief at/below 0.20 cannot produce a positive edge.
    """
    row = _row(no_asks=(), no_bids=(("0.19", "100"),), yes_bids=(("0.39", "100"),))
    ep, _p, c95 = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    # belief == the resting limit (0.20): raw edge zero, then penalty + adverse
    # haircut push the maker EV strictly non-positive (the quote price is the cost,
    # no fictitious cheap ask manufactures a phantom edge).
    mode_ev = era._mode_consistent_ev_for_proof(
        row=row,
        direction="buy_no",
        q_lcb=0.20,
        execution_price=ep,
        c_cost_95pct=c95,
        p_fill_lcb=0.0,
    )
    assert mode_ev is not None
    assert mode_ev.chosen_ev <= 0.0 + 1e-9


# ===========================================================================
# (d) thin-ask depth variant: a partial ask below min-order composes to a quote
# ===========================================================================
def test_thin_no_ask_depth_exhausted_falls_to_maker_quote():
    """A NO ask with depth below the venue min order (taker depth-exhausted) but a
    live YES bid composes into a maker quote rather than a NATIVE_ASK_MISSING."""
    # min_order 5; only 1 share of NO ask depth -> taker depth-walk raises.
    row = _row(no_asks=(("0.55", "1"),), min_order="5", yes_bids=(("0.39", "100"),))
    ep, _p, c95 = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    assert isinstance(ep, ExecutionPrice)
    # Quote bounded behind the existing partial ask AND the complementary YES bid.
    assert ep.value <= 0.60 + 1e-9
    assert c95 == pytest.approx(ep.value)


# ===========================================================================
# (e) SIBLING-ROW resolution — live-v12 root cause fix
# ===========================================================================
def _no_outcome_row(*, tradeability_status_json=None):
    """Mimics what the DB returns for the NO-outcome snapshot: single-token CLOB
    /book response for the NO token.  yes_bids is empty because the YES book was
    NOT fetched for this row (single-token endpoint returns own side only)."""
    if tradeability_status_json is None:
        tradeability_status_json = '{"executable_allowed": false, "reason": "clob_no_ask_illiquid"}'
    depth = {
        "NO": {
            "asks": [],  # empty own ask — that's why tradeability_status=clob_no_ask_illiquid
            "bids": [{"price": "0.10", "size": "500"}],
        }
        # YES key absent: single-token CLOB /book; yes_bids will be ()
    }
    return {
        "snapshot_id": "snap-no-outcome",
        "condition_id": "condition-wb",
        "yes_token_id": "yes-wb",
        "no_token_id": "no-wb",
        "selected_outcome_token_id": "",
        "outcome_label": "",
        "min_tick_size": "0.01",
        "min_order_size": "5",
        "fee_details_json": '{"fee_rate_fraction": 0.0}',
        "neg_risk": 0,
        "orderbook_depth_json": json.dumps(depth),
        "tradeability_status_json": tradeability_status_json,
        "book_hash": "no-outcome-hash",
    }


def test_sibling_row_complement_bid_prices_maker_quote():
    """Relationship test for the live-v12 None-return bug:

    Root cause: the NO-outcome snapshot row has yes_bids=() (single-token CLOB
    /book only carries NO side).  _complementary_best_bid_for_direction returned
    None -> _maker_quote_execution_price_from_snapshot returned None -> fell back
    to EXECUTABLE_NATIVE_ASK_MISSING.

    Fix: _execution_price_from_snapshot accepts complementary_top_bid (sourced
    from the sibling YES-outcome row's orderbook_top_bid scalar in
    _generate_candidate_proofs) and threads it to the maker-quote helper.

    This test exercises the full path: NO-outcome row (yes_bids empty) + sibling
    YES bid as a scalar -> maker quote is priced correctly.
    """
    row = _no_outcome_row()
    # Sibling YES-outcome row supplies the YES best bid as a scalar (orderbook_top_bid).
    sibling_yes_bid = 0.85  # Wellington ≤8°C class: extreme favorite, yes_bid ~0.85+
    ep, p_fill, c95 = era._execution_price_from_snapshot(
        row,
        selected_token_id="no-wb",
        direction="buy_no",
        complementary_top_bid=sibling_yes_bid,
    )
    assert isinstance(ep, ExecutionPrice)
    # Complementary non-crossing cap: 1 - 0.85 - 0.01 = 0.14
    assert ep.value <= 0.14 + 1e-9
    assert ep.value > 0.0
    assert ep.price_type == "bid"
    assert c95 == pytest.approx(ep.value)


def test_sibling_row_absent_complement_bid_fails_closed():
    """Without the sibling complement (no yes_bids in row, no complementary_top_bid
    passed), the maker lane has nothing to quote behind -> fail-closed as before."""
    row = _no_outcome_row()
    with pytest.raises(ValueError):
        era._execution_price_from_snapshot(
            row,
            selected_token_id="no-wb",
            direction="buy_no",
            # No complementary_top_bid -> comp_best_bid remains None -> None -> raise
        )


# ===========================================================================
# (f) extreme-favorite complementary book (yes_bid close to 1 - tick)
# ===========================================================================
def test_extreme_favorite_yes_bid_0_95_still_prices():
    """yes_bid=0.95, tick=0.01 -> cap = 1 - 0.95 - 0.01 = 0.04.
    tick_round_down(0.04, 0.01) = 0.04 > 0.0 -> maker quote priced at 0.04.
    Sao Paulo ≤15°C (lcb=0.986) class: YES bidders at 0.95, small but positive room."""
    row = _no_outcome_row()
    ep, _p, c95 = era._execution_price_from_snapshot(
        row,
        selected_token_id="no-wb",
        direction="buy_no",
        complementary_top_bid=0.95,
    )
    assert isinstance(ep, ExecutionPrice)
    assert ep.value == pytest.approx(0.04)
    assert ep.price_type == "bid"


def test_extreme_favorite_yes_bid_0_99_fails_closed():
    """yes_bid=0.99, tick=0.01 -> cap = 1 - 0.99 - 0.01 = 0.00.
    tick_round_down(0.00, 0.01) = 0 -> fails quote > 0.0 -> None -> raise.
    No room to quote without crossing YES via mint."""
    row = _no_outcome_row()
    with pytest.raises(ValueError):
        era._execution_price_from_snapshot(
            row,
            selected_token_id="no-wb",
            direction="buy_no",
            complementary_top_bid=0.99,
        )
