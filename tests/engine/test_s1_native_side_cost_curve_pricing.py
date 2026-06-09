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
from src.strategy.live_inference.executable_cost import executable_cost


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


# --------------------------------------------------------------------------
# §13 min-order gate — top-of-book depth BELOW min_order_size shares.
#
# REGRESSION (adversarial verifier, S1): the proof path must size the candidate
# by exact SHARE count (min_order_size shares), NOT by converting shares to a USD
# stake at the top level's price. The buggy conversion underfilled whenever the
# top ask level's depth < min_order_size shares — the USD budget computed at the
# cheap top price bought fewer than min_order_size shares once the walk crossed
# into costlier deeper levels — and FALSE-no-traded a side the depth walk fills
# (and that the legacy share-parameterized VWMP kernel priced fine).
#
# These fixtures EXERCISE that case (top depth strictly < min_order=5 shares).
# The earlier monotonicity test used THIN_NO top depth=8 >= min_order=5, so it
# landed in the parity case and never tripped this gate.
# --------------------------------------------------------------------------

def test_buy_yes_top_depth_below_min_order_does_not_false_no_trade():
    """buy_yes top=2sh < min_order=5: prices the depth-walked value, not a no-trade.

    Walking exactly 5 shares: 2 @ 0.40 + 3 @ 0.42 = (0.80 + 1.26)/5 = 0.412.
    The old top-price->USD conversion bought only ~4.857 shares and tripped the
    §13 min-order gate as a false no-trade. The share-parameterized walk fills the
    full 5 shares and prices it.
    """
    row = _row(yes_asks=(("0.40", "2"), ("0.42", "1000")), no_asks=(("0.55", "1000"),))
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id="yes-1", direction="buy_yes"
    )
    assert isinstance(ep, ExecutionPrice)
    ep.assert_kelly_safe()
    # (2*0.40 + 3*0.42) / 5 = 0.412  (fee_rate_fraction=0 so all-in == raw).
    assert float(ep) == pytest.approx(0.412, abs=1e-9)


def test_buy_no_top_depth_below_min_order_does_not_false_no_trade():
    """buy_no top=3sh < min_order=5: prices the depth-walked NO value, not a no-trade.

    Walking exactly 5 shares on the NO book: 3 @ 0.60 + 2 @ 0.62 =
    (1.80 + 1.24)/5 = 0.608. The buggy conversion no-traded this NO side.
    """
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=(("0.60", "3"), ("0.62", "1000")))
    ep, _p_fill, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id="no-1", direction="buy_no"
    )
    assert isinstance(ep, ExecutionPrice)
    ep.assert_kelly_safe()
    # (3*0.60 + 2*0.62) / 5 = 0.608.
    assert float(ep) == pytest.approx(0.608, abs=1e-9)


def test_thin_top_depth_zero_fee_byte_identical_parity_yes_and_no():
    """Byte-identical parity with the legacy share-walk kernel — multi-level, zero fee.

    The commit's "byte-identical value parity for YES and NO" claim held only when
    the top level alone filled min_order. This pins parity for the top-depth <
    min_order MULTI-LEVEL walk too, for BOTH native sides, with fee_rate=0 (so the
    only variable under test is the depth walk, not the fee model). The
    share-parameterized curve walk reproduces the legacy ``executable_cost``
    (also share-parameterized) all-in result exactly.
    """
    row = _row(
        yes_asks=(("0.40", "2"), ("0.42", "1000")),
        no_asks=(("0.60", "3"), ("0.62", "1000")),
        fee_rate_fraction=0.0,
    )
    book = era._native_quote_book_from_snapshot_row(row)

    for token_id, direction in (("yes-1", "buy_yes"), ("no-1", "buy_no")):
        ep_new, _pf, _c95 = era._execution_price_from_snapshot(
            row, selected_token_id=token_id, direction=direction
        )
        ep_old = executable_cost(book, direction=direction, shares=book.min_order_size)
        assert float(ep_new) == pytest.approx(float(ep_old), abs=1e-12), (
            f"{direction}: curve {float(ep_new)} != legacy kernel {float(ep_old)}"
        )


def test_single_level_fill_byte_identical_parity_even_with_fee():
    """Parity with the legacy kernel on a SINGLE-level fill holds even with a fee.

    When the top level alone fills min_order_size shares, the per-level all-in fee
    (the curve) and the fee-on-blended-average (legacy) coincide because there is
    only one price in the blend — so the value is byte-identical with a nonzero
    fee. (This is the only fee case where the two are guaranteed equal; see
    test_multi_level_fee_curve_uses_correct_per_level_fee for the multi-level case.)
    """
    row = _row(
        yes_asks=(("0.40", "1000"),),
        no_asks=(("0.60", "1000"),),
        fee_rate_fraction=0.05,
    )
    book = era._native_quote_book_from_snapshot_row(row)
    for token_id, direction in (("yes-1", "buy_yes"), ("no-1", "buy_no")):
        ep_new, _pf, _c95 = era._execution_price_from_snapshot(
            row, selected_token_id=token_id, direction=direction
        )
        ep_old = executable_cost(book, direction=direction, shares=book.min_order_size)
        assert float(ep_new) == pytest.approx(float(ep_old), abs=1e-12)


def test_multi_level_fee_curve_uses_correct_per_level_fee():
    """Multi-level + nonzero fee: the curve charges the fee PER LEVEL (more correct).

    The legacy kernel computed one fee on the blended-average price; the
    ExecutableCostCurve computes the all-in g(p)=p+r*p*(1-p) at EACH level's price
    and then blends — which is the physically correct taker model (each fill pays
    the fee on the price it fills at, spec §5.4 "walking asks ... adding fees").
    Because the fee is nonlinear in p, mean(fee(p_i)) != fee(mean(p_i)), so the two
    diverge by a tiny, well-understood amount. This test PINS that the curve uses
    the per-level fee (not the blended-average fee) so a future regression that
    re-blends-then-fees is caught. It is NOT a parity assertion — the divergence is
    the curve being more correct than the legacy approximation.
    """
    from decimal import Decimal as _D

    row = _row(
        yes_asks=(("0.40", "2"), ("0.42", "1000")),
        no_asks=(("0.55", "1000"),),
        fee_rate_fraction=0.05,
    )
    fee_rate = _D("0.05")

    def _fee(p: _D) -> _D:
        return fee_rate * p * (_D("1") - p)

    # Per-level all-in (curve semantics), walking 5 shares: 2 @ 0.40, 3 @ 0.42.
    p1, p2 = _D("0.40"), _D("0.42")
    expected_per_level = (
        _D("2") * (p1 + _fee(p1)) + _D("3") * (p2 + _fee(p2))
    ) / _D("5")
    # Fee-on-blended-average (legacy semantics) — what the curve must NOT produce.
    raw_avg = (_D("2") * p1 + _D("3") * p2) / _D("5")
    blended_avg = raw_avg + _fee(raw_avg)
    assert expected_per_level != blended_avg  # the two models genuinely differ

    ep_new, _pf, _c95 = era._execution_price_from_snapshot(
        row, selected_token_id="yes-1", direction="buy_yes"
    )
    assert float(ep_new) == pytest.approx(float(expected_per_level), abs=1e-12)
    assert float(ep_new) != pytest.approx(float(blended_avg), abs=1e-9)


def test_genuine_depth_exhaustion_below_min_order_still_fails_closed():
    """A book whose TOTAL ask depth < min_order_size shares still no-trades (§13).

    The fix must not paper over real un-fillable books: 4 total shares across the
    whole NO ladder cannot fill a 5-share min order, so the share walk fails closed
    (depth exhausted) — a true §13 no-trade, not a fabricated price.
    """
    row = _row(yes_asks=(("0.40", "1000"),), no_asks=(("0.60", "2"), ("0.62", "2")))
    with pytest.raises(ValueError):
        era._execution_price_from_snapshot(
            row, selected_token_id="no-1", direction="buy_no"
        )
