# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: PR-4 B2 alpha-gap antibody — prevent cost-quantity confusion
#   at the alpha_gap seam. Four cost-like floats exist in EDLI receipts:
#     c_fee_adjusted  — executable-snapshot ask+fee (the price you PAY, market_price axis)
#     c_cost_95pct    — trade-score stress-edge (Kelly worst-case, NOT the market price)
#     entry_cost_mean — mean fill cost (BinEdge, market_analysis)
#     c_95pct         — alias for c_cost_95pct in older code
#   alpha_gap = q_live - c_fee_adjusted (edge vs executable market price).
#   Passing c_cost_95pct (C95Price) where c_fee_adjusted (MarketPrice) is
#   expected must raise TypeError — the error category is unconstructable.
"""Newtype wrappers for cost-side quantities in the alpha-gap computation.

These are lightweight value-object newtypes, NOT subclasses of float. This
means a function typed as `market_price: MarketPrice` will raise TypeError at
runtime if passed a C95Price (or a bare float), making the confusion
  alpha_gap = q_live - c_cost_95pct   (WRONG: stress-edge, not market price)
  alpha_gap = q_live - c_fee_adjusted (RIGHT: executable market price)
unconstructable at the boundary that matters.

Usage::

    from src.types.market_price import MarketPrice, C95Price

    market_price = MarketPrice(receipt.c_fee_adjusted)
    gap = q_live - market_price.value   # correct axis

    # This would raise TypeError — C95Price is NOT MarketPrice:
    # alpha_gap_fn(q_live, C95Price(receipt.c_cost_95pct))
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketPrice:
    """Executable-snapshot fee-adjusted ask price (c_fee_adjusted).

    This is the price you actually PAY to enter a position: the executable
    snapshot ask + taker fee.  It is the correct cost reference for
    alpha_gap = q_live - market_price.

    NOT interchangeable with C95Price (the Kelly stress-edge worst-case cost).
    """

    value: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.value <= 1.0):
            raise ValueError(
                f"MarketPrice must be in [0, 1] probability units, got {self.value}"
            )


@dataclass(frozen=True)
class C95Price:
    """95th-percentile stress-edge cost (c_cost_95pct).

    Used in the trade-score formula as the conservative cost upper-bound for
    Kelly sizing.  This is NOT the executable market price and must NEVER
    be used as the reference for alpha_gap.

    NOT interchangeable with MarketPrice (the executable ask+fee price).
    """

    value: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.value <= 1.0):
            raise ValueError(
                f"C95Price must be in [0, 1] probability units, got {self.value}"
            )


def compute_alpha_gap_from_market_price(q_live: float, market_price: "MarketPrice") -> float:
    """Compute alpha_gap = q_live - market_price.value.

    Enforces at runtime that market_price is a MarketPrice instance (not a
    C95Price, bare float, or other cost type).  This makes the error category
      alpha_gap = q_live - c_cost_95pct  (wrong: stress-edge)
    unconstructable via this function.

    Args:
        q_live: The direction-adjusted posterior probability from the EDLI receipt.
        market_price: The executable-snapshot fee-adjusted price (c_fee_adjusted),
            wrapped in MarketPrice.

    Returns:
        The alpha_gap = q_live - market_price.value.

    Raises:
        TypeError: If market_price is not a MarketPrice instance.
    """
    if not isinstance(market_price, MarketPrice):
        raise TypeError(
            f"compute_alpha_gap_from_market_price requires a MarketPrice, "
            f"got {type(market_price).__name__}. "
            f"Use MarketPrice(c_fee_adjusted), NOT C95Price(c_cost_95pct)."
        )
    return q_live - market_price.value
