# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §12.C + §5.3 + §5.4 + §14.3 + Hidden #6/#15/#16
#                  + operator directive 2026-06-08
"""Relationship tests for ExecutableCostCurve (spec Phase 3, §14.3).

These are RELATIONSHIP tests, not function tests: they assert cross-module /
cross-boundary properties that must hold when a depth-walked cost curve flows
into the Kelly sizing boundary.

The properties under test (spec §12.C + Hidden #6/#15/#16):

  C.2 test_fee_adjusted_price_lowers_kelly
        Adding the Polymarket p(1-p) fee RAISES the all-in cost c, which
        LOWERS the Kelly stake fraction (q - c)/(1 - c). Encodes the
        relationship: fee_model -> avg_cost -> ExecutionPrice -> kelly_size.

  C.3 test_depth_curve_worse_price_lowers_size (monotonicity, Hidden #6):
        A thinner / worse second level makes avg_cost at a LARGER stake
        strictly higher than at a small stake, and marginal_cost is
        non-decreasing in stake. The scalar top-of-book VWMP hides this
        convex curve; the curve must expose it so Kelly does not overbet
        into thin depth.

  TYPED-BOUNDARY (spec keeps ExecutionPrice as the scalar Kelly boundary):
        avg_cost emits a TYPED, fee-adjusted, Kelly-safe ExecutionPrice in
        probability_units — never a raw float. This is the antibody that
        makes "raw float reaches Kelly" unconstructable through this path.
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
from src.contracts.execution_price import ExecutionPrice
from src.strategy.kelly import kelly_size


# --------------------------------------------------------------------------
# Fixtures: two books that share an identical top level but differ in depth.
# --------------------------------------------------------------------------

def _curve(levels, *, fee_rate="0.05", token_id="tok-yes"):
    """Build a BUY-side YES cost curve from (price, size) decimal-string pairs."""
    return ExecutableCostCurve(
        token_id=token_id,
        side="YES",
        snapshot_id="snap-1",
        book_hash="hash-1",
        levels=tuple(
            BookLevel(price=Decimal(p), size=Decimal(s)) for p, s in levels
        ),
        fee_model=FeeModel(fee_rate=Decimal(fee_rate)),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=timedelta(seconds=2),
    )


# Deep book: top level holds plenty of depth.
DEEP_BOOK = [("0.40", "1000"), ("0.41", "1000")]

# Thin book: identical top price, but the top level is shallow so a larger
# stake must walk into the worse 0.55 level.
THIN_BOOK = [("0.40", "10"), ("0.55", "1000")]


# --------------------------------------------------------------------------
# C.3 — Hidden #6: depth/monotonicity relationship.
# --------------------------------------------------------------------------

def test_depth_curve_worse_price_lowers_size():
    """A thinner second level makes avg_cost at a larger size strictly higher.

    Relationship: the same nominal top-of-book price (0.40) yields a HIGHER
    realised all-in cost on the thin book once stake outgrows the top level,
    so the Kelly stake fraction at that size is strictly lower. This is the
    convex curve the scalar VWMP hides (Hidden #6).
    """
    thin = _curve(THIN_BOOK, fee_rate="0")  # isolate depth effect from fee
    deep = _curve(DEEP_BOOK, fee_rate="0")

    small_stake = Decimal("2")    # fits inside the 10-share top level on thin book
    large_stake = Decimal("100")  # must walk into the 0.55 level on thin book

    thin_small = Decimal(str(thin.avg_cost(small_stake).value))
    thin_large = Decimal(str(thin.avg_cost(large_stake).value))
    deep_large = Decimal(str(deep.avg_cost(large_stake).value))

    # On the thin book, walking deeper strictly raises the average cost.
    assert thin_large > thin_small
    # The deep book never crosses into a worse level, so its large-stake cost
    # stays at the top price while the thin book's does not.
    assert thin_large > deep_large

    # And the higher cost lowers the Kelly stake fraction for a fixed belief.
    q = 0.70
    f_thin = kelly_size(q, thin.avg_cost(large_stake), bankroll=1000.0, kelly_mult=1.0)
    f_deep = kelly_size(q, deep.avg_cost(large_stake), bankroll=1000.0, kelly_mult=1.0)
    assert f_thin < f_deep


def test_avg_cost_monotone_non_decreasing_in_stake():
    """avg_cost(s) is monotone non-decreasing in stake for BUY (Hidden #6)."""
    curve = _curve(THIN_BOOK, fee_rate="0")
    stakes = [Decimal(s) for s in ("1", "2", "4", "8", "16", "32", "64", "128")]
    prev = Decimal("-1")
    for s in stakes:
        c = Decimal(str(curve.avg_cost(s).value))
        assert c >= prev, f"avg_cost decreased at stake={s}: {c} < {prev}"
        prev = c


def test_marginal_cost_monotone_non_decreasing_in_stake():
    """marginal_cost(s) is monotone non-decreasing in stake for BUY (Hidden #6)."""
    curve = _curve(THIN_BOOK, fee_rate="0")
    stakes = [Decimal(s) for s in ("1", "2", "4", "8", "16", "32", "64", "128")]
    prev = Decimal("-1")
    for s in stakes:
        m = curve.marginal_cost(s)
        assert m >= prev, f"marginal_cost decreased at stake={s}: {m} < {prev}"
        prev = m


def test_marginal_cost_at_least_avg_cost_on_convex_book():
    """On a strictly convex book, marginal_cost(s) >= avg_cost(s).

    The marginal (next-dollar) cost prices against the deepest level touched,
    while avg_cost blends in the cheaper earlier levels — so marginal must
    weakly dominate the average once depth is walked. This is the property
    that makes overbetting into thin depth visible to the optimizer.
    """
    curve = _curve(THIN_BOOK, fee_rate="0")
    large_stake = Decimal("100")
    avg = Decimal(str(curve.avg_cost(large_stake).value))
    marg = curve.marginal_cost(large_stake)
    assert marg >= avg


# --------------------------------------------------------------------------
# Share-parameterized pricing — avg_cost_for_shares (S1 share<->USD fix).
#
# The candidate-proof path prices the venue min-order QUANTITY (in shares), not a
# USD stake. avg_cost_for_shares walks an exact share count so the buggy
# shares -> USD (at top price) -> shares round-trip never underfills a thin top
# level. These pin the share-walk math and its divergence from the USD path.
# --------------------------------------------------------------------------

# Top level holds only 2 shares; min_order_size will be set to 5, so a 5-share
# order must walk into the deeper 0.42 level.
THIN_TOP_BOOK = [("0.40", "2"), ("0.42", "1000")]


def _curve_min_order(levels, *, min_order, fee_rate="0"):
    return ExecutableCostCurve(
        token_id="tok-yes",
        side="YES",
        snapshot_id="snap-1",
        book_hash="hash-1",
        levels=tuple(BookLevel(price=Decimal(p), size=Decimal(s)) for p, s in levels),
        fee_model=FeeModel(fee_rate=Decimal(fee_rate)),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal(min_order),
        quote_ttl=timedelta(seconds=2),
    )


def test_avg_cost_for_shares_walks_exact_share_count_through_thin_top():
    """Pricing 5 shares when the top level holds only 2 walks into the deeper level.

    (2*0.40 + 3*0.42) / 5 = 0.412. The share walk fills the FULL 5 shares; it does
    not underfill the way a top-price USD-notional conversion would.
    """
    curve = _curve_min_order(THIN_TOP_BOOK, min_order="5", fee_rate="0")
    price = curve.avg_cost_for_shares(Decimal("5"))
    assert isinstance(price, ExecutionPrice)
    price.assert_kelly_safe()
    assert float(price.value) == pytest.approx(0.412, abs=1e-12)


def test_top_price_usd_notional_would_underfill_but_share_walk_does_not():
    """The OLD shares->USD-at-top-price conversion underfills; the share walk fixes it.

    Reproduces the bug's mechanism directly at the contract level: a USD stake set
    to (min_order_size shares * top all-in price) buys FEWER than min_order_size
    shares on a thin top level and trips the §13 min-order gate; the same min-order
    quantity priced by avg_cost_for_shares fills exactly and prices cleanly.
    """
    curve = _curve_min_order(THIN_TOP_BOOK, min_order="5", fee_rate="0")

    # The buggy notional: 5 shares * top all-in price (0.40) = 2.00 USD.
    top_all_in = curve.fee_model.all_in_price(curve.levels[0].price)
    buggy_notional = curve.min_order_size * top_all_in
    assert buggy_notional == Decimal("2.00")
    # avg_cost(2.00 USD) underfills (buys ~4.857 shares < 5) and fails closed.
    with pytest.raises(ValueError):
        curve.avg_cost(buggy_notional)

    # The share-parameterized walk fills the full 5 shares and prices it.
    price = curve.avg_cost_for_shares(curve.min_order_size)
    assert float(price.value) == pytest.approx(0.412, abs=1e-12)


def test_avg_cost_for_shares_monotone_non_decreasing():
    """avg_cost_for_shares(n) is non-decreasing in share count for a BUY (Hidden #6)."""
    curve = _curve_min_order(THIN_TOP_BOOK, min_order="1", fee_rate="0")
    counts = [Decimal(n) for n in ("1", "2", "3", "5", "10", "50", "200")]
    prev = Decimal("-1")
    for n in counts:
        c = Decimal(str(curve.avg_cost_for_shares(n).value))
        assert c >= prev, f"avg_cost_for_shares decreased at shares={n}: {c} < {prev}"
        prev = c


def test_avg_cost_for_shares_below_min_order_fails_closed():
    """Requesting fewer than min_order_size shares fails closed (§13)."""
    curve = _curve_min_order(THIN_TOP_BOOK, min_order="5", fee_rate="0")
    with pytest.raises(ValueError):
        curve.avg_cost_for_shares(Decimal("4"))


def test_avg_cost_for_shares_above_total_depth_fails_closed():
    """Requesting more shares than total ask depth fails closed (depth exhausted, §13)."""
    # Total depth = 2 + 4 = 6 shares; ask for 10.
    curve = _curve_min_order([("0.40", "2"), ("0.42", "4")], min_order="1", fee_rate="0")
    with pytest.raises(ValueError):
        curve.avg_cost_for_shares(Decimal("10"))


# --------------------------------------------------------------------------
# C.2 — fee raises c, lowering Kelly.
# --------------------------------------------------------------------------

def test_fee_adjusted_price_lowers_kelly():
    """The p(1-p) fee raises the all-in cost and lowers the Kelly stake.

    Relationship: fee_model -> avg_cost -> ExecutionPrice -> kelly_size.
    Same belief q, same book; the only difference is fee_rate. A nonzero fee
    must produce a STRICTLY higher all-in cost and a STRICTLY smaller Kelly
    stake (Hidden #15 — fee drift must move the cost monotonically).
    """
    no_fee = _curve(DEEP_BOOK, fee_rate="0")
    with_fee = _curve(DEEP_BOOK, fee_rate="0.05")

    stake = Decimal("50")
    c_no_fee = no_fee.avg_cost(stake)
    c_with_fee = with_fee.avg_cost(stake)

    assert float(c_with_fee.value) > float(c_no_fee.value)

    q = 0.70
    f_no_fee = kelly_size(q, c_no_fee, bankroll=1000.0, kelly_mult=1.0)
    f_with_fee = kelly_size(q, c_with_fee, bankroll=1000.0, kelly_mult=1.0)
    assert f_with_fee < f_no_fee
    assert f_with_fee > 0.0  # still a positive-edge trade, just smaller


# --------------------------------------------------------------------------
# Typed boundary: avg_cost emits a Kelly-safe ExecutionPrice, never a float.
# --------------------------------------------------------------------------

def test_avg_cost_emits_typed_kelly_safe_execution_price():
    """avg_cost returns a typed, fee-adjusted, Kelly-safe ExecutionPrice.

    Spec keeps ExecutionPrice as the scalar Kelly boundary at the chosen
    stake. The returned object must pass assert_kelly_safe() so a raw float
    can never enter Kelly through the cost-curve path (the antibody).
    """
    curve = _curve(DEEP_BOOK, fee_rate="0.05")
    price = curve.avg_cost(Decimal("50"))
    assert isinstance(price, ExecutionPrice)
    assert price.price_type == "fee_adjusted"
    assert price.fee_deducted is True
    assert price.currency == "probability_units"
    # Must not raise — this is the relationship that lets kelly_size accept it.
    price.assert_kelly_safe()
    kelly_size(0.70, price, bankroll=1000.0, kelly_mult=1.0)  # accepted, no raise


def test_zero_fee_curve_is_not_double_fee_adjusted():
    """A fee_rate=0 curve still emits a fee_adjusted/fee_deducted price.

    The all-in cost simply has a zero fee component; the typed boundary still
    declares fee_deducted=True so Kelly does not re-apply a fee downstream
    (which would understate size — the inverse of Hidden #15).
    """
    curve = _curve(DEEP_BOOK, fee_rate="0")
    price = curve.avg_cost(Decimal("10"))
    assert price.fee_deducted is True
    assert price.price_type == "fee_adjusted"


# --------------------------------------------------------------------------
# max_fillable: how much stake fills at or below a limit price (Hidden #16).
# --------------------------------------------------------------------------

def test_max_fillable_respects_limit_price():
    """max_fillable(limit) returns stake fillable at all-in cost <= limit.

    On the thin book, a limit just above the top all-in price admits only the
    shallow top level; raising the limit above the 0.55 level admits the deep
    level too. The relationship: limit price gates executable stake.
    """
    curve = _curve(THIN_BOOK, fee_rate="0")
    # Limit between 0.40 and 0.55: only the 10-share top level is fillable.
    tight = curve.max_fillable(Decimal("0.45"))
    # Limit above 0.55: both levels fillable.
    loose = curve.max_fillable(Decimal("0.60"))
    assert loose > tight
    assert tight > Decimal("0")


def test_max_fillable_below_best_ask_is_zero():
    """A limit below the best all-in ask fills nothing."""
    curve = _curve(THIN_BOOK, fee_rate="0")
    assert curve.max_fillable(Decimal("0.30")) == Decimal("0")


# --------------------------------------------------------------------------
# Construction / fail-closed guards.
# --------------------------------------------------------------------------

def test_avg_cost_above_depth_fails_closed():
    """Requesting more stake than the book can fill raises (no silent VWMP).

    Hidden #6 / §13 no-trade gate: "Optimal stake above allowed depth" must
    fail closed rather than fabricate a fill price from exhausted depth.
    """
    curve = _curve(THIN_BOOK, fee_rate="0")
    # Thin book holds 10 sh @0.40 + 1000 sh @0.55 = max ~554 USD notional.
    with pytest.raises(ValueError):
        curve.avg_cost(Decimal("100000"))


def test_below_min_order_size_fails_closed():
    """A stake whose share count is below min_order_size fails closed."""
    curve = _curve(DEEP_BOOK, fee_rate="0")  # min_order_size=1 share
    # 0.0001 USD at 0.40 = 0.00025 shares < 1 share min order.
    with pytest.raises(ValueError):
        curve.avg_cost(Decimal("0.0001"))


def test_levels_must_be_tick_aligned():
    """A level price off the min_tick grid fails construction (Hidden #16)."""
    with pytest.raises(ValueError):
        ExecutableCostCurve(
            token_id="tok",
            side="YES",
            snapshot_id="s",
            book_hash="h",
            levels=(BookLevel(price=Decimal("0.405"), size=Decimal("100")),),
            fee_model=FeeModel(fee_rate=Decimal("0.05")),
            min_tick=Decimal("0.01"),
            min_order_size=Decimal("1"),
            quote_ttl=timedelta(seconds=2),
        )


def test_empty_book_fails_closed():
    """An empty levels tuple cannot construct a curve (no executable ask)."""
    with pytest.raises(ValueError):
        ExecutableCostCurve(
            token_id="tok",
            side="YES",
            snapshot_id="s",
            book_hash="h",
            levels=(),
            fee_model=FeeModel(fee_rate=Decimal("0.05")),
            min_tick=Decimal("0.01"),
            min_order_size=Decimal("1"),
            quote_ttl=timedelta(seconds=2),
        )


def test_maker_resting_avg_cost_drops_taker_fee():
    """RELATIONSHIP: a maker-resting projection pays zero taker fee.

    A resting post_only/GTC maker order pays NO taker fee (Fee Structure V2:
    maker fee = 0). avg_cost(maker_resting=True) must therefore price the SAME
    book strictly cheaper than the taker projection by exactly the p(1-p) fee,
    and equal the raw bare-price walk. Charging the taker fee on a maker-resting
    cost overstates cost and rejects positive-edge maker entries.
    """
    # Single deep level at p=0.50 where the fee is maximal.
    curve = _curve([("0.50", "100")], fee_rate="0.05")
    stake = Decimal("10")

    taker = float(curve.avg_cost(stake, maker_resting=False).value)
    maker = float(curve.avg_cost(stake, maker_resting=True).value)

    # Taker pays p + fee*p*(1-p) = 0.50 + 0.05*0.25 = 0.5125.
    assert taker == pytest.approx(0.5125)
    # Maker pays the bare price only: 0.50.
    assert maker == pytest.approx(0.50)
    assert maker < taker
    # The difference is exactly the taker fee per share at p=0.50.
    assert (taker - maker) == pytest.approx(0.05 * 0.5 * 0.5)


def test_maker_resting_default_is_taker():
    """avg_cost defaults to the taker (full-fee) projection."""
    curve = _curve([("0.50", "100")], fee_rate="0.05")
    stake = Decimal("10")
    default = Decimal(str(curve.avg_cost(stake).value))
    taker = Decimal(str(curve.avg_cost(stake, maker_resting=False).value))
    assert default == taker
