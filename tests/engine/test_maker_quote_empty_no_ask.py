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
