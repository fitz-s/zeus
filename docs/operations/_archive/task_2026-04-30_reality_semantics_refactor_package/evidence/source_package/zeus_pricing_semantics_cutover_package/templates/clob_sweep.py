"""Illustrative CLOB sweep skeleton only. Adapt to Zeus types/contracts.

Important: fee_rate here is an input supplied by market fee metadata. Do not
hardcode a production fee rate. A 0.02 or 0.05 value may be used only in tests
or when proven by `getClobMarketInfo(conditionID)` / market metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence


Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class PriceLevel:
    price: Decimal
    shares: Decimal


@dataclass(frozen=True)
class SizeSpec:
    kind: Literal["shares", "notional_usd"]
    value: Decimal


@dataclass(frozen=True)
class ClobSweepResult:
    side: Side
    requested: SizeSpec
    filled_shares: Decimal
    gross_cash: Decimal
    fee_cash: Decimal
    all_in_price: Decimal
    depth_status: Literal["FILLED", "PARTIAL", "EMPTY_BOOK"]
    levels_used: tuple[PriceLevel, ...]


def _fee_for_level(shares: Decimal, price: Decimal, fee_rate: Decimal) -> Decimal:
    return shares * fee_rate * price * (Decimal("1") - price)


def simulate_clob_sweep(
    *,
    bids: Sequence[PriceLevel],
    asks: Sequence[PriceLevel],
    side: Side,
    target: SizeSpec,
    fee_rate: Decimal,
) -> ClobSweepResult:
    if target.value <= 0:
        raise ValueError("target size must be positive")
    if fee_rate < 0:
        raise ValueError("fee_rate must be non-negative")

    book = sorted(asks, key=lambda x: x.price) if side == "BUY" else sorted(bids, key=lambda x: x.price, reverse=True)
    if not book:
        return ClobSweepResult(side, target, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("Infinity"), "EMPTY_BOOK", ())

    remaining = target.value
    filled = Decimal("0")
    gross = Decimal("0")
    fees = Decimal("0")
    used: list[PriceLevel] = []

    for level in book:
        if level.price <= 0 or level.price >= 1:
            raise ValueError(f"invalid CLOB price {level.price}")
        if level.shares <= 0:
            continue

        if target.kind == "shares":
            fill_shares = min(level.shares, remaining)
            remaining -= fill_shares
        else:
            # target.kind == notional_usd. Fill by pre-fee notional budget.
            max_shares_at_level = remaining / level.price
            fill_shares = min(level.shares, max_shares_at_level)
            remaining -= fill_shares * level.price

        if fill_shares <= 0:
            continue

        level_cash = fill_shares * level.price
        level_fee = _fee_for_level(fill_shares, level.price, fee_rate)
        filled += fill_shares
        gross += level_cash
        fees += level_fee
        used.append(PriceLevel(price=level.price, shares=fill_shares))

        if remaining <= Decimal("0"):
            break

    if filled <= 0:
        return ClobSweepResult(side, target, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("Infinity"), "EMPTY_BOOK", tuple(used))

    if side == "BUY":
        all_in = (gross + fees) / filled
    else:
        all_in = (gross - fees) / filled

    status = "FILLED" if remaining <= Decimal("0.0000000001") else "PARTIAL"
    return ClobSweepResult(side, target, filled, gross, fees, all_in, status, tuple(used))
