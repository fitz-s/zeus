# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL PR G §6
"""Pure orderbook fill simulator — no DB writes, no live execution imports.

Fee convention (Polymarket taker, per docs.polymarket.com/trading/fees):
    fee_per_share = fee_rate * p * (1 - p)

where p is the AVERAGE FILL PRICE across all consumed levels.  The fee
is expressed in shares: effective_filled_size = filled_size - fees (i.e.
the buyer nets fewer shares after fees; the seller nets less proceeds).
avg_price in SimulatedFill is the GROSS price before fees.  Callers that
need the all-in cost compute: all_in_price = avg_price + fee_per_share.

BUY  walks the ASK book ascending (lowest ask first).
SELL walks the BID book descending (highest bid first).
Midpoint is NEVER used.

Order-type semantics:
    FOK  — Fill-Or-Kill: all-or-nothing.  If the full requested_size
           cannot be filled at/within limit_price → CANCELLED, filled_size=0.
    FAK  — Fill-And-Kill: fill what the book offers at/within limit, cancel
           remainder.  May produce PARTIAL fill.
    GTC  — Good-Till-Cancel: fill available now, remainder rests.  Reports
           remainder without calling it CANCELLED.
    GTD  — Good-Till-Date: same as GTC for simulation purposes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Sequence


FillStatus = Literal["FILLED", "PARTIAL", "CANCELLED", "REJECTED"]
OrderType = Literal["FOK", "FAK", "GTC", "GTD"]
Side = Literal["buy", "sell"]

# Level format: (price, size) both as Decimal
Level = tuple[Decimal, Decimal]


@dataclass(frozen=True)
class SimulatedFill:
    """Result of simulate_fill().

    Attributes:
        side: "buy" or "sell".
        requested_size: shares requested.
        filled_size: shares actually filled (gross, before fees).
        avg_price: gross volume-weighted average fill price.
            None when filled_size == 0.
        fees: fee charge in share-equivalents using Polymarket formula.
            Applied to avg_price; zero when filled_size == 0.
        cancelled_remainder: unfilled shares disposed as cancelled
            (FOK full cancel, or FAK residual).  0 for GTC/GTD.
        fill_status: FILLED / PARTIAL / CANCELLED / REJECTED.
        levels_consumed: number of price levels touched.
        reason: human-readable string explaining status; empty for FILLED.
    """

    side: Side
    requested_size: Decimal
    filled_size: Decimal
    avg_price: Decimal | None
    fees: Decimal
    cancelled_remainder: Decimal
    fill_status: FillStatus
    levels_consumed: int
    reason: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _as_decimal(value: object, name: str) -> Decimal:
    """Convert float/int/str/Decimal to Decimal; raise ValueError on failure."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{name}: cannot convert {value!r} to Decimal") from exc


def _parse_levels(raw: Sequence[object], side_label: str) -> list[Level]:
    """Parse a raw bid or ask list into sorted (price, size) tuples.

    Accepts both dict-style {"price": p, "size": s} and list-style [p, s].
    Levels with non-positive size are skipped.
    BID side sorted descending (best bid first).
    ASK side sorted ascending (best ask first).
    """
    levels: list[Level] = []
    for entry in raw:
        if isinstance(entry, dict):
            price_raw = entry.get("price")
            size_raw = entry.get("size")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            price_raw, size_raw = entry[0], entry[1]
        else:
            raise ValueError(f"{side_label}: malformed level {entry!r}")
        price = _as_decimal(price_raw, f"{side_label}.price")
        size = _as_decimal(size_raw, f"{side_label}.size")
        if size <= Decimal("0"):
            continue
        levels.append((price, size))

    descending = side_label.startswith("bid")
    levels.sort(key=lambda lv: lv[0], reverse=descending)
    return levels


def _tick_aligned(price: Decimal, tick_size: Decimal) -> bool:
    """Return True when price is an integer multiple of tick_size."""
    if tick_size <= Decimal("0"):
        return True  # degenerate — never reject
    remainder = price % tick_size
    # Allow float-rounding noise up to 1e-9
    return remainder < Decimal("1e-9") or (tick_size - remainder) < Decimal("1e-9")


def _polymarket_fee(avg_price: Decimal, fee_rate: Decimal, filled_size: Decimal) -> Decimal:
    """Polymarket price-dependent taker fee expressed in share-equivalents.

    fee_per_share = fee_rate * p * (1 - p)
    total_fees    = fee_per_share * filled_size
    """
    fee_per_share = fee_rate * avg_price * (Decimal("1") - avg_price)
    return fee_per_share * filled_size


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simulate_fill(
    *,
    side: Side,
    order_type: OrderType,
    limit_price: float | Decimal,
    requested_size: float | Decimal,
    bids: Sequence[object],
    asks: Sequence[object],
    tick_size: float | Decimal,
    min_order_size: float | Decimal,
    fee_rate: float | Decimal,
    orderbook_hash: str | None = None,
    expected_hash: str | None = None,
    market_resolved: bool = False,
) -> SimulatedFill:
    """Simulate a limit order fill against a static orderbook snapshot.

    Parameters
    ----------
    side:
        "buy" (walks asks ascending) or "sell" (walks bids descending).
    order_type:
        FOK | FAK | GTC | GTD — see module docstring.
    limit_price:
        Worst acceptable price.  BUY: do not pay above limit.
        SELL: do not accept below limit.  Must be tick-aligned.
    requested_size:
        Shares to fill.  Must be >= min_order_size.
    bids / asks:
        Raw orderbook sides.  Each entry is dict {"price":…,"size":…}
        or list/tuple [price, size].
    tick_size:
        Minimum price increment.  limit_price is checked for alignment.
    min_order_size:
        Reject fills below this threshold.
    fee_rate:
        Polymarket taker fee rate in [0, 1).
    orderbook_hash:
        Hash of the snapshot actually used (optional).
    expected_hash:
        If provided and != orderbook_hash → REJECTED (stale book guard).
    market_resolved:
        True → REJECTED immediately; cannot trade a resolved market.

    Returns
    -------
    SimulatedFill
    """
    # -- Normalise inputs --------------------------------------------------
    limit = _as_decimal(limit_price, "limit_price")
    requested = _as_decimal(requested_size, "requested_size")
    tick = _as_decimal(tick_size, "tick_size")
    min_sz = _as_decimal(min_order_size, "min_order_size")
    rate = _as_decimal(fee_rate, "fee_rate")

    def _reject(reason: str) -> SimulatedFill:
        return SimulatedFill(
            side=side,
            requested_size=requested,
            filled_size=Decimal("0"),
            avg_price=None,
            fees=Decimal("0"),
            cancelled_remainder=Decimal("0"),
            fill_status="REJECTED",
            levels_consumed=0,
            reason=reason,
        )

    def _cancel(reason: str, fill_size: Decimal = Decimal("0")) -> SimulatedFill:
        """CANCELLED with no fill (FOK) or remaining after limit breach."""
        return SimulatedFill(
            side=side,
            requested_size=requested,
            filled_size=Decimal("0"),
            avg_price=None,
            fees=Decimal("0"),
            cancelled_remainder=requested,
            fill_status="CANCELLED",
            levels_consumed=0,
            reason=reason,
        )

    # Guard: resolved market
    if market_resolved:
        return _reject("market_resolved")

    # Guard: orderbook hash mismatch
    if expected_hash is not None and orderbook_hash != expected_hash:
        return _reject("orderbook_hash_mismatch")

    # Guard: basic input validation
    if side not in ("buy", "sell"):
        return _reject(f"invalid_side:{side!r}")
    if order_type not in ("FOK", "FAK", "GTC", "GTD"):
        return _reject(f"invalid_order_type:{order_type!r}")
    if requested <= Decimal("0"):
        return _reject("requested_size_must_be_positive")
    if not math.isfinite(float(limit)) or limit <= Decimal("0") or limit >= Decimal("1"):
        return _reject(f"limit_price_out_of_range:{limit}")
    if rate < Decimal("0") or rate >= Decimal("1"):
        return _reject(f"fee_rate_out_of_range:{rate}")

    # Guard: min_order_size
    if requested < min_sz:
        return _reject(f"below_min_order_size:requested={requested},min={min_sz}")

    # Guard: tick alignment
    if tick > Decimal("0") and not _tick_aligned(limit, tick):
        return _reject(f"limit_price_not_tick_aligned:limit={limit},tick={tick}")

    # -- Select and sort book side ----------------------------------------
    if side == "buy":
        levels = _parse_levels(asks, "asks")
    else:
        levels = _parse_levels(bids, "bids")

    if not levels:
        if order_type == "FOK":
            return _cancel("empty_book_fok_cancelled")
        # FAK / GTC / GTD with empty book
        return SimulatedFill(
            side=side,
            requested_size=requested,
            filled_size=Decimal("0"),
            avg_price=None,
            fees=Decimal("0"),
            cancelled_remainder=requested if order_type == "FAK" else Decimal("0"),
            fill_status="PARTIAL" if order_type == "FAK" else "PARTIAL",
            levels_consumed=0,
            reason="empty_book",
        )

    # -- Walk levels -------------------------------------------------------
    remaining = requested
    gross_notional = Decimal("0")
    filled_size = Decimal("0")
    levels_consumed = 0

    for price, level_size in levels:
        # Limit gate: BUY won't pay above limit; SELL won't accept below
        if side == "buy" and price > limit:
            break
        if side == "sell" and price < limit:
            break

        take = min(level_size, remaining)
        if take <= Decimal("0"):
            break

        filled_size += take
        gross_notional += take * price
        levels_consumed += 1
        remaining -= take
        if remaining <= Decimal("0"):
            remaining = Decimal("0")
            break

    # -- Compute avg price and fees ----------------------------------------
    avg_price: Decimal | None
    fees = Decimal("0")
    if filled_size > Decimal("0"):
        avg_price = gross_notional / filled_size
        fees = _polymarket_fee(avg_price, rate, filled_size)
    else:
        avg_price = None

    # -- Apply order type semantics ----------------------------------------
    if order_type == "FOK":
        if remaining > Decimal("0"):
            # Full fill impossible — cancel everything
            return SimulatedFill(
                side=side,
                requested_size=requested,
                filled_size=Decimal("0"),
                avg_price=None,
                fees=Decimal("0"),
                cancelled_remainder=requested,
                fill_status="CANCELLED",
                levels_consumed=0,
                reason="fok_no_full_fill",
            )
        # Full fill
        fill_status: FillStatus = "FILLED"
        cancelled_remainder = Decimal("0")
        final_reason = ""

    elif order_type == "FAK":
        if remaining > Decimal("0"):
            fill_status = "PARTIAL"
            cancelled_remainder = remaining
            final_reason = f"fak_partial:filled={filled_size},cancelled={remaining}"
        else:
            fill_status = "FILLED"
            cancelled_remainder = Decimal("0")
            final_reason = ""

    else:
        # GTC / GTD: remainder rests (not cancelled)
        cancelled_remainder = Decimal("0")
        if remaining > Decimal("0"):
            fill_status = "PARTIAL"
            final_reason = f"gtc_partial:filled={filled_size},resting={remaining}"
        else:
            fill_status = "FILLED"
            final_reason = ""

    # Guard: filled size below min_order_size (e.g. partial fill too small)
    if fill_status in ("FILLED", "PARTIAL") and filled_size < min_sz:
        return _reject(
            f"fill_below_min_order_size:filled={filled_size},min={min_sz}"
        )

    return SimulatedFill(
        side=side,
        requested_size=requested,
        filled_size=filled_size,
        avg_price=avg_price,
        fees=fees,
        cancelled_remainder=cancelled_remainder,
        fill_status=fill_status,
        levels_consumed=levels_consumed,
        reason=final_reason,
    )
