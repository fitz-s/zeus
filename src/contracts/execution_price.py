"""Execution price contract — D3 resolution.

D3 gap: market_analysis.py:141 sets entry_price = p_market[i], which is an implied
probability, not a VWMP+fee execution price. Kelly's formula:
  f* = (p_posterior - entry_price) / (1 - entry_price)
uses this bare float, but Polymarket execution price = ask + taker fee (5%) +
slippage. Kelly systematically oversizes because it treats implied probability as
cost-of-entry.

Resolution: entry_price at the Kelly boundary must be typed ExecutionPrice.
Bare floats at this seam are INV-12 violations.

See: docs/zeus_FINAL_spec.md §P9.3 D3
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ExecutionPrice:
    """Typed price value that declares its kind, fee status, and currency.

    Resolves D3: prevents implied probability from silently masquerading as an
    execution cost at the Kelly boundary, causing systematic oversizing.

    Attributes:
        value: The numeric price value. Must be >= 0.
        price_type: What this value represents.
            "vwmp"                — Volume-Weighted Micro-Price (bid/ask/size blend)
            "ask"                 — Best ask (raw, before fee)
            "bid"                 — Best bid
            "implied_probability" — Raw market probability (NOT suitable for Kelly cost)
        fee_deducted: True if taker fee has already been subtracted from value.
            At the Kelly boundary, fee_deducted must be True or the caller must
            explicitly acknowledge they will adjust downstream.
        currency: Unit of value.
            "usd"                — Dollar value
            "probability_units"  — [0, 1] probability space
    """

    value: float
    price_type: Literal["vwmp", "ask", "bid", "implied_probability"]
    fee_deducted: bool
    currency: Literal["usd", "probability_units"]

    def __post_init__(self) -> None:
        if self.value < 0.0:
            raise ValueError(
                f"ExecutionPrice.value must be >= 0, got {self.value}"
            )
        if self.currency == "probability_units" and self.value > 1.0:
            raise ValueError(
                f"ExecutionPrice in probability_units must be <= 1.0, got {self.value}"
            )

    def assert_kelly_safe(self) -> None:
        """Raise ExecutionPriceContractError if this price is unsafe for Kelly sizing.

        Safe at Kelly boundary requires:
        1. price_type is NOT "implied_probability" (that is not a cost, it's an estimate)
        2. fee_deducted=True (Kelly must see the true all-in cost)
        3. currency="probability_units" (Kelly operates in probability space)
        """
        errors = []
        if self.price_type == "implied_probability":
            errors.append(
                "price_type='implied_probability' cannot be used as Kelly entry cost. "
                "Use VWMP or ask price instead."
            )
        if not self.fee_deducted:
            errors.append(
                "fee_deducted=False at Kelly boundary. Kelly will oversize because "
                "taker fee (~5%) is not included in the all-in cost."
            )
        if self.currency != "probability_units":
            errors.append(
                f"currency='{self.currency}' at Kelly boundary. "
                "Kelly formula requires probability_units."
            )
        if errors:
            raise ExecutionPriceContractError(
                "ExecutionPrice fails Kelly safety check (INV-12 violation):\n"
                + "\n".join(f"  • {e}" for e in errors)
            )


class ExecutionPriceContractError(Exception):
    """Raised when an ExecutionPrice is used unsafely at the Kelly sizing boundary.
    This is the D3 / INV-12 runtime contract violation.
    """


def polymarket_fee(price: float, fee_rate: float = 0.05) -> float:
    """Compute Polymarket price-dependent taker fee per share.

    Formula from docs.polymarket.com/trading/fees:
        fee_per_share = fee_rate × p × (1 - p)

    At p=0.90: fee = 0.05 × 0.90 × 0.10 = 0.0045 (0.45%), NOT flat 5%.
    At p=0.50: fee = 0.05 × 0.50 × 0.50 = 0.0125 (1.25%).

    P9-D3: Replaces incorrect flat 5% assumption in FeeGuard / §P10.6.
    """
    if price <= 0.0 or price >= 1.0:
        return 0.0
    return fee_rate * price * (1.0 - price)
