"""Realized fill typed record — T5.d.

A ``RealizedFill`` bundles the three elements that describe what
actually happened when an intent crossed the CLOB seam: the price
executed against, the price expected at intent construction, and the
resulting slippage. Each element is its own typed contract
(``ExecutionPrice`` from T5.a, ``SlippageBps`` from T5.d) so the
record cannot drift into the "bag of bare floats" anti-pattern that
makes Kelly attribution and slippage budgeting impossible to audit.

See: docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md T5.d
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from src.contracts.execution_price import ExecutionPrice
from src.contracts.slippage_bps import SlippageBps


@dataclass(frozen=True)
class RealizedFill:
    """Typed record of a completed fill against the CLOB.

    Attributes:
        execution_price: Actual price the fill cleared at (post-fee if
            ``fee_deducted=True`` on the ExecutionPrice).
        expected_price: The intent's target price at intent
            construction. Same currency as ``execution_price`` —
            mismatched currencies are rejected at construction.
        slippage: Signed-via-direction basis-point deviation between
            ``execution_price`` and ``expected_price``. Produced by
            ``SlippageBps.from_prices`` so direction semantics align
            with ``side``.
        side: ``"buy"`` or ``"sell"`` — encodes how slippage direction
            should be interpreted (buy-side adverse = paid more;
            sell-side adverse = received less).
        shares: Non-negative finite share count that the fill cleared.
        trade_id: Identifier for the originating intent / order.
    """

    execution_price: ExecutionPrice
    expected_price: ExecutionPrice
    slippage: SlippageBps
    side: Literal["buy", "sell"]
    shares: float
    trade_id: str

    def __post_init__(self) -> None:
        if self.execution_price.currency != self.expected_price.currency:
            raise ValueError(
                f"RealizedFill currency mismatch: execution="
                f"{self.execution_price.currency!r} vs expected="
                f"{self.expected_price.currency!r}"
            )
        if self.side not in ("buy", "sell"):
            raise ValueError(
                f"RealizedFill.side must be 'buy' or 'sell', got {self.side!r}"
            )
        if not math.isfinite(self.shares):
            raise ValueError(
                f"RealizedFill.shares must be finite, got {self.shares}"
            )
        if self.shares <= 0.0:
            raise ValueError(
                f"RealizedFill.shares must be > 0, got {self.shares}"
            )
        if not self.trade_id:
            raise ValueError(
                "RealizedFill.trade_id must not be empty"
            )

    @classmethod
    def from_prices(
        cls,
        *,
        execution_price: ExecutionPrice,
        expected_price: ExecutionPrice,
        side: Literal["buy", "sell"],
        shares: float,
        trade_id: str,
    ) -> "RealizedFill":
        """Build a RealizedFill by deriving slippage from price pair.

        Preferred factory — avoids requiring callers to hand-construct
        a SlippageBps that could drift from the two prices. All
        validation (currency match, finite shares, side, trade_id
        presence) runs at the RealizedFill __post_init__ seam.
        """
        slippage = SlippageBps.from_prices(
            actual=execution_price,
            expected=expected_price,
            side=side,
        )
        return cls(
            execution_price=execution_price,
            expected_price=expected_price,
            slippage=slippage,
            side=side,
            shares=shares,
            trade_id=trade_id,
        )
