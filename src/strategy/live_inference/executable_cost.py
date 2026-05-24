"""Native executable cost for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.contracts.execution_price import ExecutionPrice

Direction = Literal["buy_yes", "buy_no", "sell_yes", "sell_no"]


@dataclass(frozen=True)
class QuoteBook:
    yes_asks: tuple[tuple[float, float], ...]
    no_asks: tuple[tuple[float, float], ...]
    yes_bids: tuple[tuple[float, float], ...]
    no_bids: tuple[tuple[float, float], ...]
    fee_rate: float
    tick_size: float
    min_order_size: float
    neg_risk: bool


def native_executable_cost(book: QuoteBook, *, direction: Direction, shares: float) -> ExecutionPrice:
    if shares < book.min_order_size:
        raise ValueError("order below min_order_size")
    if direction == "buy_yes":
        price = _walk(book.yes_asks, shares, "YES ask")
    elif direction == "buy_no":
        price = _walk(book.no_asks, shares, "NO ask")
    elif direction == "sell_yes":
        price = _walk(book.yes_bids, shares, "YES bid")
    elif direction == "sell_no":
        price = _walk(book.no_bids, shares, "NO bid")
    else:
        raise ValueError(f"unsupported direction {direction!r}")
    execution_price = ExecutionPrice(
        value=price,
        price_type="ask" if direction.startswith("buy") else "bid",
        fee_deducted=False,
        currency="probability_units",
    ).with_taker_fee(book.fee_rate)
    execution_price.assert_kelly_safe()
    return execution_price


def reject_forbidden_cost_source(source: str) -> None:
    if source in {"midpoint", "display_probability", "last_trade_price", "no_complement"}:
        raise ValueError(f"{source} is forbidden as executable cost")


def _walk(levels: tuple[tuple[float, float], ...], shares: float, label: str) -> float:
    if not levels:
        raise ValueError(f"empty {label} book")
    remaining = shares
    cost = 0.0
    filled = 0.0
    for price, size in levels:
        take = min(remaining, size)
        cost += take * price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled < shares:
        raise ValueError("DEPTH_INSUFFICIENT")
    return cost / filled
