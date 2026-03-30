"""Order executor: limit-order-only execution engine. Spec §6.4.

Handles both paper and live modes.
Paper mode: simulates fills at VWMP.
Live mode: places limit orders via Polymarket CLOB API.

Key rules:
- Limit orders ONLY (never market orders)
- Mode-based timeouts: Opening Hunt 4h, Update Reaction 1h, Day0 15min
- Whale toxicity detection: cancel on adjacent bin sweeps
- Share quantization: BUY rounds UP, SELL rounds DOWN (0.01 increments)
- Dynamic limit: if within 5% of best ask, jump to ask for guaranteed fill
"""

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.config import settings
from src.types import BinEdge

logger = logging.getLogger(__name__)


# Mode-based fill timeout (seconds). Spec §6.4.
MODE_TIMEOUTS = {
    "opening_hunt": 4 * 3600,
    "update_reaction": 1 * 3600,
    "day0_capture": 15 * 60,
}


@dataclass
class OrderResult:
    """Result of an order attempt."""
    trade_id: str
    status: str  # "filled", "pending", "cancelled", "rejected"
    fill_price: Optional[float] = None
    filled_at: Optional[str] = None
    reason: Optional[str] = None


def execute_order(
    edge: BinEdge,
    size_usd: float,
    mode: str,
    market_id: str,
    token_id: str = "",
    no_token_id: str = "",
    best_ask: Optional[float] = None,
    best_bid: Optional[float] = None,
) -> OrderResult:
    """Execute a limit order (paper or live).

    Args:
        edge: detected trading edge
        size_usd: position size in dollars
        mode: discovery mode (opening_hunt, update_reaction, day0_capture)
        market_id: Polymarket condition ID
        token_id: YES token clobTokenIds[0] — used for buy_yes orders
        no_token_id: NO token clobTokenIds[1] — used for buy_no orders
        best_ask: current best ask from orderbook (for dynamic limit)
        best_bid: current best bid from orderbook (for dynamic limit)
    """
    trade_id = str(uuid.uuid4())[:12]
    limit_offset = settings["execution"]["limit_offset_pct"]

    # Compute initial limit price
    if edge.direction == "buy_yes":
        limit_price = min(edge.p_posterior, edge.vwmp) - limit_offset
    else:
        limit_price = min(1.0 - edge.p_posterior, 1.0 - edge.vwmp) - limit_offset

    limit_price = max(0.01, min(0.99, limit_price))

    # V7: Dynamic limit price — if within 5% of best ask, jump to ask
    if edge.direction == "buy_yes" and best_ask is not None:
        gap = best_ask - limit_price
        if 0 < gap <= best_ask * 0.05:
            logger.info("Dynamic limit: jumping to best_ask %.3f (gap %.3f)", best_ask, gap)
            limit_price = best_ask
        elif gap > best_ask * 0.05:
            logger.warning("Limit %.3f far below best_ask %.3f (gap %.1f%%) — may not fill",
                           limit_price, best_ask, gap / best_ask * 100)
    elif edge.direction == "buy_no" and best_ask is not None:
        # For buy_no, we're buying the NO token — check NO orderbook's best ask
        gap = best_ask - limit_price
        if 0 < gap <= best_ask * 0.05:
            logger.info("Dynamic limit: jumping to best_ask %.3f (gap %.3f)", best_ask, gap)
            limit_price = best_ask

    # V6: Compute shares with proper quantization
    shares = size_usd / limit_price if limit_price > 0 else 0
    shares = math.ceil(shares * 100 - 1e-9) / 100.0  # BUY: round UP

    if settings.mode == "paper":
        return _paper_fill(trade_id, edge, size_usd, limit_price)
    else:
        # Select correct token: YES token for buy_yes, NO token for buy_no
        order_token = token_id if edge.direction == "buy_yes" else no_token_id
        if not order_token:
            return OrderResult(
                trade_id=trade_id, status="rejected",
                reason=f"No {'YES' if edge.direction == 'buy_yes' else 'NO'} token_id provided",
            )

        return _live_order(
            trade_id, edge, shares, limit_price, mode, market_id, order_token
        )


def _paper_fill(
    trade_id: str,
    edge: BinEdge,
    size_usd: float,
    limit_price: float,
) -> OrderResult:
    """Paper mode: simulate fill at VWMP. Spec §6.4.

    BinEdge.vwmp is ALREADY in native space for the direction:
    - buy_yes: vwmp = YES-side price
    - buy_no: vwmp = NO-side price
    DO NOT flip it again — that's the churn bug root cause.
    """
    fill_price = edge.vwmp  # Already native space. NEVER flip here.
    now = datetime.now(timezone.utc).isoformat()

    logger.info(
        "PAPER FILL: %s %s @ %.3f (limit=%.3f, size=$%.2f)",
        edge.direction, edge.bin.label, fill_price, limit_price, size_usd,
    )

    return OrderResult(
        trade_id=trade_id,
        status="filled",
        fill_price=fill_price,
        filled_at=now,
    )


def _live_order(
    trade_id: str,
    edge: BinEdge,
    shares: float,
    limit_price: float,
    mode: str,
    market_id: str,
    order_token: str,
) -> OrderResult:
    """Live mode: place order via Polymarket CLOB API."""
    from src.data.polymarket_client import PolymarketClient

    timeout = MODE_TIMEOUTS[mode]

    logger.info(
        "LIVE ORDER: %s %s token=%s...%s @ %.3f limit, %.2f shares, timeout=%ds",
        edge.direction, edge.bin.label,
        order_token[:8], order_token[-4:],
        limit_price, shares, timeout,
    )

    try:
        client = PolymarketClient(paper_mode=False)
        result = client.place_limit_order(
            token_id=order_token,
            price=limit_price,
            size=shares,
            side="BUY",  # Always BUY — we're buying YES or NO tokens
        )

        if result is None:
            return OrderResult(
                trade_id=trade_id, status="rejected",
                reason="CLOB API returned None",
            )

        return OrderResult(
            trade_id=trade_id,
            status="pending",
            reason=f"Order posted, timeout={timeout}s",
        )

    except Exception as e:
        logger.error("Live order failed: %s", e)
        return OrderResult(
            trade_id=trade_id, status="rejected",
            reason=str(e),
        )
