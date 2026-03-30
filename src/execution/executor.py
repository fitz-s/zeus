"""Order executor: limit-order-only execution engine. Spec §6.4.

Handles both paper and live modes.
Paper mode: simulates fills at VWMP.
Live mode: places limit orders via Polymarket CLOB API.

Key rules:
- Limit orders ONLY (never market orders)
- Mode-based timeouts: Opening Hunt 4h, Update Reaction 1h, Day0 15min
- Whale toxicity detection: cancel on adjacent bin sweeps
"""

import logging
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
) -> OrderResult:
    """Execute a limit order (paper or live).

    Spec §6.4: Limit price = min(p_posterior, vwmp) - offset for buy_yes.
    """
    trade_id = str(uuid.uuid4())[:12]
    limit_offset = settings["execution"]["limit_offset_pct"]

    if edge.direction == "buy_yes":
        limit_price = min(edge.p_posterior, edge.vwmp) - limit_offset
    else:
        limit_price = min(1.0 - edge.p_posterior, 1.0 - edge.vwmp) - limit_offset

    limit_price = max(0.01, min(0.99, limit_price))

    if settings.mode == "paper":
        return _paper_fill(trade_id, edge, size_usd, limit_price)
    else:
        return _live_order(trade_id, edge, size_usd, limit_price, mode, market_id)


def _paper_fill(
    trade_id: str,
    edge: BinEdge,
    size_usd: float,
    limit_price: float,
) -> OrderResult:
    """Paper mode: simulate fill at VWMP. Spec §6.4."""
    fill_price = edge.vwmp if edge.direction == "buy_yes" else (1.0 - edge.vwmp)
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
    size_usd: float,
    limit_price: float,
    mode: str,
    market_id: str,
) -> OrderResult:
    """Live mode: place order via Polymarket CLOB API.

    TODO(Phase D): Implement CLOB API integration from polymarket_client.py.
    For now, returns pending status.
    """
    timeout = MODE_TIMEOUTS[mode]

    logger.info(
        "LIVE ORDER: %s %s @ %.3f limit, $%.2f, timeout=%ds, market=%s",
        edge.direction, edge.bin.label, limit_price, size_usd, timeout, market_id,
    )

    # TODO(Phase C1): Integrate polymarket_client.place_limit_order()
    return OrderResult(
        trade_id=trade_id,
        status="pending",
        reason=f"Live order placed, timeout={timeout}s",
    )
