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

import math
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
            "fee_adjusted"       — Price with taker fee applied (output of with_taker_fee())
        fee_deducted: True if taker fee has already been subtracted from value.
            At the Kelly boundary, fee_deducted must be True or the caller must
            explicitly acknowledge they will adjust downstream.
        currency: Unit of value.
            "usd"                — Dollar value
            "probability_units"  — [0, 1] probability space
    """

    value: float
    price_type: Literal["vwmp", "ask", "bid", "implied_probability", "fee_adjusted"]
    fee_deducted: bool
    currency: Literal["usd", "probability_units"]

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise ValueError(
                f"ExecutionPrice.value must be finite, got {self.value}"
            )
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

    def with_taker_fee(self, fee_rate: float = 0.05) -> "ExecutionPrice":
        """Return new ExecutionPrice with Polymarket taker fee applied.

        Delegates to polymarket_fee(): fee = fee_rate × p × (1-p).
        NOT a flat percentage — fee is highest at p=0.50 and near-zero at extremes.
        """
        if self.fee_deducted:
            raise ExecutionPriceContractError(
                "with_taker_fee() called on already fee-adjusted price "
                f"(value={self.value}, price_type='{self.price_type}'). "
                "Double fee application would cause systematic undersizing."
            )
        fee = polymarket_fee(self.value, fee_rate)
        return ExecutionPrice(
            value=self.value + fee,
            price_type="fee_adjusted",
            fee_deducted=True,
            currency=self.currency,
        )

    @classmethod
    def schema_packet(cls) -> dict:
        """Return typed schema for K2/K3 consumption contracts."""
        return {
            "type": "ExecutionPrice",
            "required_fields": ["value", "price_type", "fee_deducted", "currency"],
        }

    # ------------------------------------------------------------------
    # Wave 2 (2026-05-27, INV-38): numeric dunders so consumers that
    # historically read a bare float (e.g. cycle_runtime/evaluator/replay
    # arithmetic + comparison sites) keep working after BinEdge.entry_price
    # is typed. Comparisons + arithmetic operate on ``self.value`` only —
    # the returned scalar is a plain float because it is a DERIVED numeric
    # value that has lost provenance (e.g. price + fee, price > 0.99).
    # The TYPE CONTRACT (price_type / fee_deducted / currency) is still
    # enforced via ``isinstance(x, ExecutionPrice)`` + ``assert_kelly_safe``
    # at the Kelly boundary; these dunders ONLY make numeric ergonomics
    # transparent. Do NOT add operators that return ExecutionPrice — that
    # would re-introduce silent type laundering (D5 defect).
    # ------------------------------------------------------------------

    def __float__(self) -> float:
        return float(self.value)

    def __round__(self, ndigits: int = 0) -> float:
        return round(float(self.value), ndigits)

    def __format__(self, format_spec: str) -> str:
        if format_spec == "":
            return str(self)
        return format(float(self.value), format_spec)

    def _other_value(self, other) -> float | None:
        if isinstance(other, ExecutionPrice):
            return float(other.value)
        if isinstance(other, (int, float)):
            return float(other)
        return None

    def __lt__(self, other) -> bool:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) < v

    def __le__(self, other) -> bool:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) <= v

    def __gt__(self, other) -> bool:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) > v

    def __ge__(self, other) -> bool:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) >= v

    def __add__(self, other) -> float:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) + v

    def __radd__(self, other) -> float:
        return self.__add__(other)

    def __sub__(self, other) -> float:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) - v

    def __rsub__(self, other) -> float:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return v - float(self.value)

    def __mul__(self, other) -> float:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) * v

    def __rmul__(self, other) -> float:
        return self.__mul__(other)

    def __truediv__(self, other) -> float:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return float(self.value) / v

    def __rtruediv__(self, other) -> float:
        v = self._other_value(other)
        if v is None:
            return NotImplemented
        return v / float(self.value)

    def __bool__(self) -> bool:
        return float(self.value) != 0.0

    # Equality is FULL-FIELD ExecutionPrice identity only. We deliberately do
    # NOT compare equal to a bare float (PR #348 operator review, Blocker 1):
    #   - hash/eq invariant: ``a == b`` requires ``hash(a) == hash(b)``. A
    #     float ``0.5`` hashes as ``hash(0.5)`` while this object hashes the
    #     full provenance tuple, so EP-equals-float + tuple-hash would corrupt
    #     any set/dict that mixes the two.
    #   - transitivity: two ExecutionPrices with the same ``value`` but
    #     different provenance would BOTH equal ``0.5`` yet not equal each
    #     other — a == c, b == c, a != b.
    # Numeric ergonomics for legacy float readers stay available via
    # ``__float__`` / ordering / arithmetic dunders; equality is the one
    # operator that MUST preserve provenance. Callers that genuinely want a
    # scalar comparison use ``float(ep) == x`` or ``ep.value == x`` explicitly.
    # __hash__ matches the dataclass default field tuple so frozen-dataclass
    # set/dict semantics remain intact and consistent with __eq__.
    def __eq__(self, other) -> bool:
        if isinstance(other, ExecutionPrice):
            return (
                self.value == other.value
                and self.price_type == other.price_type
                and self.fee_deducted == other.fee_deducted
                and self.currency == other.currency
            )
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.value, self.price_type, self.fee_deducted, self.currency))


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
    if not math.isfinite(price) or not math.isfinite(fee_rate):
        raise ValueError(
            f"polymarket_fee requires finite inputs, got price={price}, fee_rate={fee_rate}"
        )
    if price <= 0.0 or price >= 1.0:
        raise ValueError(
            f"polymarket_fee requires price in (0, 1), got {price}"
        )
    return fee_rate * price * (1.0 - price)
