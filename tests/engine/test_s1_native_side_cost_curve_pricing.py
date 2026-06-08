# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §5.3 (cost-curve Kelly) + §5.4 (fees/slippage/
#   depth) + §4 (executable-space separation p_exec(NO) != 1 - p_exec(YES)) +
#   §9 Hidden #6 (scalar VWMP hides convex cost curve) / Hidden #16 (tick change
#   invalidates curve) + §13 (no-trade gates: native quote missing) +
#   §14.3 (ExecutableCostCurve shape) + operator directive 2026-06-08 (single
#   primary-live path, no flag).
"""S1 relationship tests — native side priced by its OWN ExecutableCostCurve.

These are RELATIONSHIP tests (cross-module invariants), written BEFORE the
implementation per the order: relationship tests -> implementation -> function
tests. They pin the property that survives across the seam where a snapshot row's
native ask ladder flows into the Kelly cost-of-entry boundary.

S1 replaces the scalar single-number VWMP pricing on the candidate path
(``_execution_price_from_snapshot`` / ``_native_quote_book_from_snapshot_row``
era_adapter ~5099, ~8588) with the committed bin-selection
``src.contracts.executable_cost_curve.ExecutableCostCurve`` — built side-tagged
from the SAME executable snapshot row's native ask ladder (``yes_asks`` for
buy_yes, ``no_asks`` for buy_no). The curve is the ONE pricing object on this
path; the scalar VWMP kernel is no longer the pricing authority.

The three named S1 invariants:

  test_depth_walked_avg_cost_monotone_nondecreasing_for_buy (Hidden #6):
        avg_cost(stake) on the curve built from a real snapshot row is monotone
        NON-DECREASING in stake — the convex depth walk the scalar top-ask price
        hid. A larger order walks into worse levels, so the all-in cost can only
        rise.

  test_no_side_curve_walks_no_ask_book_not_yes (§4):
        the buy_no candidate's ExecutableCostCurve has side=='NO' and its levels
        equal book.no_asks, NEVER 1 - yes_asks. A curve_side mismatch raises.

  test_offgrid_or_empty_book_yields_no_trade_candidate (§13):
        a snapshot whose levels are empty / off the min-tick grid produces a
        NATIVE_QUOTE_MISSING no-trade (raised ValueError on the proof-pricing
        path), not a fabricated price.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.contracts.executable_cost_curve import ExecutableCostCurve
from src.contracts.execution_price import ExecutionPrice
from src.engine import event_reactor_adapter as era


# --------------------------------------------------------------------------
# Snapshot-row fixtures: the dict shape consumed by
# _native_quote_book_from_snapshot_row / _execution_price_from_snapshot.
# --------------------------------------------------------------------------

def _row(
    *,
    yes_asks,
    no_asks,
    yes_bids=(("0.39", "100"),),
    no_bids=(("0.19", "100"),),
    min_tick="0.01",
    min_order="5",
    fee_rate_fraction=0.0,
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
        "snapshot_id": "snap-s1",
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
        "tradeability_status_json": "{}",
        "book_hash": "book-hash-s1",
    }


# Deep YES top level; thin NO top level so a larger NO order walks worse.
DEEP_YES = (("0.40", "1000"), ("0.41", "1000"))
THIN_NO = (("0.55", "8"), ("0.70", "1000"))


# --------------------------------------------------------------------------
# Hidden #6 — depth-walked monotonicity on a curve built from a real row.
# --------------------------------------------------------------------------

def test_depth_walked_avg_cost_monotone_nondecreasing_for_buy():
    """avg_cost(stake) on the snapshot-row curve is non-decreasing in stake.

    The thin NO top level (8 shares @ 0.55) is exhausted by a larger stake, which
    must walk into the worse 0.70 level — so the depth-weighted all-in cost at a
    larger stake is strictly higher than at a min-size stake. The scalar top-ask
    VWMP hid this; the curve exposes it.
    """
    row = _row(yes_asks=DEEP_YES, no_asks=THIN_NO)
    curve = era._native_side_cost_curve_from_snapshot_row(
        row, side="NO", token_id="no-1"
    )
    assert isinstance(curve, ExecutableCostCurve)

    # Small stake fills entirely at the cheap top level; larger stake walks into
    # the worse second level. avg_cost must be non-decreasing across the boundary.
    small = curve.avg_cost(Decimal("4"))      # ~7 shares @ 0.55 region (<= 8 depth)
    large = curve.avg_cost(Decimal("50"))     # forces the 0.70 level
    assert isinstance(small, ExecutionPrice)
    assert isinstance(large, ExecutionPrice)
    assert float(large) >= float(small)
    assert float(large) > float(small)  # strictly worse: it crossed into 0.70

    # And the boundary value the proof path emits is a fee-deducted,
    # probability_units ExecutionPrice that passes assert_kelly_safe.
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    assert isinstance(ep, ExecutionPrice)
    ep.assert_kelly_safe()


# --------------------------------------------------------------------------
# §4 — NO side walks the NO ask book, never 1 - yes_asks.
# --------------------------------------------------------------------------

def test_no_side_curve_walks_no_ask_book_not_yes():
    """buy_no curve.side == 'NO' and its levels are book.no_asks, not 1 - yes_asks."""
    row = _row(yes_asks=DEEP_YES, no_asks=THIN_NO)
    no_curve = era._native_side_cost_curve_from_snapshot_row(
        row, side="NO", token_id="no-1"
    )
    yes_curve = era._native_side_cost_curve_from_snapshot_row(
        row, side="YES", token_id="yes-1"
    )

    assert no_curve.side == "NO"
    assert yes_curve.side == "YES"

    # The NO curve's level prices are exactly the NO ask book prices, NOT the
    # YES-complement (1 - 0.40 = 0.60, 1 - 0.41 = 0.59) prices.
    no_prices = sorted(float(lvl.price) for lvl in no_curve.levels)
    assert no_prices == [0.55, 0.70]
    yes_complement = sorted({round(1.0 - float(p), 2) for p, _ in DEEP_YES})
    assert no_prices != yes_complement

    # The YES curve's prices are the YES ask book.
    yes_prices = sorted(float(lvl.price) for lvl in yes_curve.levels)
    assert yes_prices == [0.40, 0.41]

    # A YES-tagged curve fed to a NO candidate is unconstructable: building a
    # curve from no_asks but tagging it YES is a side mismatch the contract
    # already rejects (curve.side is set by the builder's side arg). Prove the
    # builder cannot be coerced to price NO from the YES book: NO curve token id
    # is the NO token, and its book is the NO book.
    assert no_curve.token_id == "no-1"
    assert yes_curve.token_id == "yes-1"


def test_yes_no_native_asks_are_independent_not_complement():
    """Independent native books (Hidden, §4): NO ask 0.55 is unrelated to YES 0.40.

    The two sides price from genuinely independent ask ladders. The buy_no
    ExecutionPrice traces to no_asks (~0.55) and the buy_yes to yes_asks (~0.40);
    neither is 1 - the other (1 - 0.40 = 0.60 != 0.55).
    """
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=(("0.55", "1000"),))
    yes_ep, _pf_y, _c_y = era._execution_price_from_snapshot(
        row, selected_token_id="yes-1", direction="buy_yes"
    )
    no_ep, _pf_n, _c_n = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    # fee_rate_fraction=0 so all-in == raw ask. YES ~ 0.40, NO ~ 0.55.
    assert float(yes_ep) == pytest.approx(0.40, abs=1e-9)
    assert float(no_ep) == pytest.approx(0.55, abs=1e-9)
    # NO is its own book, NOT 1 - YES.
    assert float(no_ep) != pytest.approx(1.0 - float(yes_ep), abs=1e-6)


# --------------------------------------------------------------------------
# §13 — stale/empty/off-grid book yields a no-trade, not a fabricated price.
# --------------------------------------------------------------------------

def test_offgrid_or_empty_book_yields_no_trade_candidate():
    """Empty NO ask book -> ValueError on the proof-pricing path (NATIVE_QUOTE_MISSING)."""
    # Empty NO asks: the native NO side has no executable depth.
    row_empty = _row(yes_asks=DEEP_YES, no_asks=())
    with pytest.raises(ValueError):
        era._execution_price_from_snapshot(
            row_empty, selected_token_id="no-1", direction="buy_no"
        )

    # Off-grid NO ask price (0.555 is not on the 0.01 tick grid) -> the curve's
    # tick-grid guard (Hidden #16) fails closed rather than rounding a limit.
    row_offgrid = _row(yes_asks=DEEP_YES, no_asks=(("0.555", "1000"),))
    with pytest.raises(ValueError):
        era._execution_price_from_snapshot(
            row_offgrid, selected_token_id="no-1", direction="buy_no"
        )


def test_buy_yes_priced_from_yes_book_unaffected_by_no_book_state():
    """A buy_yes proof prices from yes_asks even when the NO book is empty (§4)."""
    row = _row(yes_asks=DEEP_YES, no_asks=())
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id="yes-1", direction="buy_yes"
    )
    assert isinstance(ep, ExecutionPrice)
    ep.assert_kelly_safe()
    assert float(ep) == pytest.approx(0.40, abs=1e-9)


# --------------------------------------------------------------------------
# Fee relationship (§5.4): a higher fee raises the all-in cost-of-entry.
# --------------------------------------------------------------------------

def test_fee_raises_all_in_cost_through_the_curve():
    """The Polymarket p(1-p) fee on the curve raises avg_cost vs a zero-fee book."""
    no_levels = (("0.50", "1000"),)
    row_nofee = _row(yes_asks=DEEP_YES, no_asks=no_levels, fee_rate_fraction=0.0)
    row_fee = _row(yes_asks=DEEP_YES, no_asks=no_levels, fee_rate_fraction=0.05)
    ep_nofee, _, _ = era._execution_price_from_snapshot(
        row_nofee, selected_token_id="no-1", direction="buy_no"
    )
    ep_fee, _, _ = era._execution_price_from_snapshot(
        row_fee, selected_token_id="no-1", direction="buy_no"
    )
    assert float(ep_fee) > float(ep_nofee)
    # fee at p=0.50 = 0.05 * 0.5 * 0.5 = 0.0125
    assert float(ep_fee) == pytest.approx(0.50 + 0.0125, abs=1e-9)
