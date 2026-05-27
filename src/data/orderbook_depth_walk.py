# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: architecture/market_cost_seam_executable_uncertainty_2026_05_27.md §Wave3 +
#                  Polymarket orderbook depth-walk reference (calculateMarketPrice equivalent).
"""Order-book depth-walk math.

Pre-Wave-3 Zeus took the top-of-book VWMP as the executable buy price for ANY
order size, regardless of whether the order size actually fitted in the top
level's depth. For orders bigger than `ask_size`, the system had no slippage
knowledge and silently underestimated the all-in fill cost.

This module is the pure-function math that walks the ASKS ladder level by level
until `target_shares` are filled and returns the depth-weighted average fill
price + slippage relative to the best ask. It does NOT touch the network or the
runtime DB. Wave 5 will consume it via ``EntryQuoteEvidence`` to provide a real
``cost_uncertainty`` input to the edge bootstrap.

Numerics:
  - All arithmetic in float (orderbook entries are floats post-normalisation
    in src/data/polymarket_client.py:get_orderbook).
  - The function is deterministic and side-effect-free; safe to call inside
    edge-scan + risk-eval hot paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DepthWalkResult:
    """Result of walking the asks ladder for a target share count.

    Attributes:
        fill_price_walk: depth-weighted average fill price across all consumed
            asks levels. Equal to best_ask when the top level holds enough
            depth to cover ``target_shares``.
        slippage_bps: ``(fill_price_walk - best_ask) / best_ask * 10000``.
            Zero or positive (buy slippage is always adverse upward).
        depth_walked_shares: actual share count consumed. May be less than
            ``target_shares`` if the orderbook does not hold enough depth.
        depth_sufficient: True iff the orderbook covered the full target_size.
        levels_walked: number of price levels consumed (1 = top-of-book only).
        best_ask: best (lowest) ask price at the top of the asks ladder.
    """

    fill_price_walk: float
    slippage_bps: float
    depth_walked_shares: float
    depth_sufficient: bool
    levels_walked: int
    best_ask: float


def walk_asks_for_target_shares(
    asks: Iterable[dict | tuple],
    target_shares: float,
) -> DepthWalkResult:
    """Walk a list of asks levels, consuming depth until ``target_shares`` are filled.

    Args:
        asks: iterable of ``{"price": float, "size": float}`` dicts OR
            ``(price, size)`` tuples. Will be sorted ascending by price.
        target_shares: total shares the order needs to fill.

    Returns:
        DepthWalkResult — see dataclass docstring.

    Raises:
        ValueError: ``target_shares`` non-positive, asks empty, or any
            price/size non-finite/non-positive.
    """
    if target_shares <= 0:
        raise ValueError(f"target_shares must be > 0, got {target_shares}")

    parsed: list[tuple[float, float]] = []
    for entry in asks:
        if isinstance(entry, dict):
            try:
                price = float(entry["price"])
                size = float(entry["size"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid asks entry {entry!r}: {exc}") from exc
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            price = float(entry[0])
            size = float(entry[1])
        else:
            raise ValueError(f"unsupported asks entry shape: {entry!r}")
        if not (price > 0 and price < 1):
            raise ValueError(f"asks price out of (0, 1) bounds: {price}")
        if not (size > 0):
            raise ValueError(f"asks size must be > 0, got {size}")
        parsed.append((price, size))

    if not parsed:
        raise ValueError("asks ladder is empty")

    parsed.sort(key=lambda level: level[0])
    best_ask = parsed[0][0]

    remaining = float(target_shares)
    total_cost = 0.0
    total_filled = 0.0
    levels_walked = 0
    for price, size in parsed:
        if remaining <= 0:
            break
        levels_walked += 1
        take = min(size, remaining)
        total_cost += take * price
        total_filled += take
        remaining -= take

    if total_filled <= 0:
        # Should be unreachable because target_shares > 0 + asks not empty,
        # but guard against floating-point eccentricities.
        raise ValueError("depth walk consumed zero shares — inputs are degenerate")

    fill_price_walk = total_cost / total_filled
    slippage_bps = (fill_price_walk - best_ask) / best_ask * 10_000.0 if best_ask > 0 else 0.0
    depth_sufficient = total_filled >= target_shares - 1e-9
    return DepthWalkResult(
        fill_price_walk=fill_price_walk,
        slippage_bps=slippage_bps,
        depth_walked_shares=total_filled,
        depth_sufficient=depth_sufficient,
        levels_walked=levels_walked,
        best_ask=best_ask,
    )
