"""Native executable cost helpers for EDLI v1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from src.contracts.execution_price import ExecutionPrice
from src.contracts.execution_price import polymarket_fee
from src.contracts.executable_market_snapshot_v2 import (
    ExecutableMarketSnapshotV2,
    fee_rate_fraction_from_details,
)

Direction = Literal["buy_yes", "buy_no", "sell_yes", "sell_no"]


class ExecutableCostError(ValueError):
    pass


@dataclass(frozen=True)
class QuoteLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class NativeQuoteBook:
    yes_asks: tuple[QuoteLevel, ...]
    no_asks: tuple[QuoteLevel, ...]
    yes_bids: tuple[QuoteLevel, ...]
    no_bids: tuple[QuoteLevel, ...]
    min_tick_size: Decimal
    min_order_size: Decimal
    fee_rate: float
    neg_risk: bool


def executable_cost(book: NativeQuoteBook, *, direction: Direction, shares: Decimal) -> ExecutionPrice:
    if shares < book.min_order_size:
        raise ExecutableCostError("min order size blocks executable cost")
    levels = _levels_for_direction(book, direction)
    price_type = "ask" if direction.startswith("buy_") else "bid"
    average = _book_walk_average(levels, shares, tick=book.min_tick_size)
    raw = ExecutionPrice(float(average), price_type=price_type, fee_deducted=False, currency="probability_units")
    if direction.startswith("buy_"):
        return raw.with_taker_fee(book.fee_rate)
    fee = polymarket_fee(raw.value, book.fee_rate)
    return ExecutionPrice(
        max(raw.value - fee, 0.0),
        price_type="fee_adjusted",
        fee_deducted=True,
        currency=raw.currency,
    )


def assert_not_midpoint_cost(*, used_midpoint: bool) -> None:
    if used_midpoint:
        raise ExecutableCostError("midpoint is forbidden as executable cost")


def assert_not_last_trade_cost(*, used_last_trade: bool) -> None:
    if used_last_trade:
        raise ExecutableCostError("last_trade_price is forbidden as executable cost")


def assert_not_no_complement_cost(*, used_yes_complement: bool) -> None:
    if used_yes_complement:
        raise ExecutableCostError("c_no = 1 - yes_price is forbidden as executable cost")


def assert_neg_risk_matches(book: NativeQuoteBook, *, expected_neg_risk: bool) -> None:
    if book.neg_risk != expected_neg_risk:
        raise ExecutableCostError("negRisk mismatch blocks executable cost")


def quote_book_from_depth_json(
    *,
    yes_depth_json: str,
    no_depth_json: str,
    min_tick_size: str,
    min_order_size: str,
    fee_rate: float,
    neg_risk: bool,
) -> NativeQuoteBook:
    yes_depth = json.loads(yes_depth_json)
    no_depth = json.loads(no_depth_json)
    return NativeQuoteBook(
        yes_asks=_parse_levels(yes_depth.get("asks", ())),
        no_asks=_parse_levels(no_depth.get("asks", ())),
        yes_bids=_parse_levels(yes_depth.get("bids", ())),
        no_bids=_parse_levels(no_depth.get("bids", ())),
        min_tick_size=Decimal(min_tick_size),
        min_order_size=Decimal(min_order_size),
        fee_rate=fee_rate,
        neg_risk=neg_risk,
    )


def quote_book_from_executable_snapshot(snapshot: ExecutableMarketSnapshotV2) -> NativeQuoteBook:
    """Build native YES/NO quote book from ExecutableMarketSnapshotV2 facts."""

    depth = json.loads(snapshot.orderbook_depth_jsonb or "{}")
    yes_depth = _depth_for_token_or_label(depth, token_id=snapshot.yes_token_id, label="YES")
    no_depth = _depth_for_token_or_label(depth, token_id=snapshot.no_token_id, label="NO")
    if yes_depth is None or no_depth is None:
        raise ExecutableCostError(
            "ExecutableMarketSnapshotV2 orderbook_depth_jsonb must contain native YES and NO depth"
        )
    return NativeQuoteBook(
        yes_asks=_parse_levels(yes_depth.get("asks", ())),
        no_asks=_parse_levels(no_depth.get("asks", ())),
        yes_bids=_parse_levels(yes_depth.get("bids", ())),
        no_bids=_parse_levels(no_depth.get("bids", ())),
        min_tick_size=snapshot.min_tick_size,
        min_order_size=snapshot.min_order_size,
        fee_rate=fee_rate_fraction_from_details(snapshot.fee_details),
        neg_risk=snapshot.neg_risk,
    )


def _levels_for_direction(book: NativeQuoteBook, direction: Direction) -> tuple[QuoteLevel, ...]:
    if direction == "buy_yes":
        return book.yes_asks
    if direction == "buy_no":
        return book.no_asks
    if direction == "sell_yes":
        return book.yes_bids
    if direction == "sell_no":
        return book.no_bids
    raise ExecutableCostError(f"unsupported direction: {direction}")


def _book_walk_average(levels: tuple[QuoteLevel, ...], shares: Decimal, *, tick: Decimal) -> Decimal:
    remaining = shares
    cost = Decimal("0")
    for level in levels:
        _assert_tick_compatible(level.price, tick)
        take = min(remaining, level.size)
        cost += take * level.price
        remaining -= take
        if remaining <= 0:
            return cost / shares
    raise ExecutableCostError("NO_DEPTH")


def _assert_tick_compatible(price: Decimal, tick: Decimal) -> None:
    units = price / tick
    if units != units.to_integral_value():
        raise ExecutableCostError("tick size mismatch blocks executable cost")


def _parse_levels(raw_levels: object) -> tuple[QuoteLevel, ...]:
    levels = []
    for raw in raw_levels:
        if isinstance(raw, dict):
            price = raw["price"]
            size = raw["size"]
        else:
            price, size = raw
        levels.append(QuoteLevel(Decimal(str(price)), Decimal(str(size))))
    return tuple(levels)


def _depth_for_token_or_label(depth: object, *, token_id: str, label: str) -> dict[str, object] | None:
    if not isinstance(depth, dict):
        return None
    for key in (token_id, label, label.lower()):
        value = depth.get(key)
        if isinstance(value, dict):
            return value
    for key in ("tokens", "outcomes", "books"):
        value = depth.get(key)
        if isinstance(value, dict):
            nested = _depth_for_token_or_label(value, token_id=token_id, label=label)
            if nested is not None:
                return nested
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                if str(item.get("asset_id") or item.get("token_id") or "") == token_id:
                    return item
                if str(item.get("outcome") or item.get("outcome_label") or "").upper() == label:
                    return item
    return None
