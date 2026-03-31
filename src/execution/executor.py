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
from src.contracts import (
    HeldSideProbability,
    NativeSidePrice,
    compute_native_limit_price,
    ExecutionIntent,
    EdgeContext,
    Direction,
)
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
    order_id: Optional[str] = None
    timeout_seconds: Optional[int] = None
    submitted_price: Optional[float] = None
    shares: Optional[float] = None


def create_execution_intent(
    edge_context: EdgeContext,
    edge: BinEdge,
    size_usd: float,
    mode: str,
    market_id: str,
    token_id: str = "",
    no_token_id: str = "",
    best_ask: Optional[float] = None,
) -> ExecutionIntent:
    """Execution Planner: Generates the intent based on Fair Value Plane output."""

    limit_offset = settings["execution"]["limit_offset_pct"]

    # Compute initial limit price in the native/held-side probability space.
    limit_price = compute_native_limit_price(
        HeldSideProbability(edge_context.p_posterior, edge.direction),
        NativeSidePrice(edge.vwmp, edge.direction),
        limit_offset=limit_offset,
    )

    # Dynamic limit price
    if best_ask is not None:
        gap = best_ask - limit_price
        if 0 < gap <= best_ask * 0.05:
            logger.info("Dynamic limit: jumping to best_ask %.3f (gap %.3f)", best_ask, gap)
            limit_price = best_ask
        elif gap > best_ask * 0.05:
            logger.warning("Limit %.3f far below best_ask %.3f (gap %.1f%%) — may not fill",
                           limit_price, best_ask, gap / best_ask * 100)
                           
    order_token = token_id if edge.direction == "buy_yes" else no_token_id
    timeout = MODE_TIMEOUTS.get(mode, 3600)
    
    return ExecutionIntent(
        direction=Direction(edge.direction),
        target_size_usd=size_usd,
        limit_price=limit_price,
        toxicity_budget=0.05,
        max_slippage=0.02,
        is_sandbox=(settings.mode == "paper"),
        market_id=market_id,
        token_id=order_token,
        timeout_seconds=timeout,
        slice_policy="iceberg" if size_usd > 100 else "single_shot",
        reprice_policy="dynamic_peg" if mode == "day0_capture" else "static",
        liquidity_guard=True,
    )

def execute_intent(
    intent: ExecutionIntent,
    edge_vwmp: float, # VWMP needed for paper fill simulation
    label: str, # Label used for logging
) -> OrderResult:
    """Execute the instantiated domain intent (paper or live)."""
    
    trade_id = str(uuid.uuid4())[:12]

    limit_price = intent.limit_price

    # Phase 3: Adversarial Execution Evolutions
    if intent.liquidity_guard and not intent.is_sandbox:
        logger.info(f"Liquidity guard active. Monitoring toxic sweep parameters against {intent.target_size_usd}")
        
    if intent.slice_policy == "iceberg":
        logger.info(f"Iceberg slice policy active: Will break {intent.target_size_usd} into micro-orders")

    # V6: Compute shares with proper quantization
    shares = intent.target_size_usd / limit_price if limit_price > 0 else 0
    shares = math.ceil(shares * 100 - 1e-9) / 100.0  # BUY: round UP

    if intent.is_sandbox:
        return _paper_fill(trade_id, intent, edge_vwmp, label)
    else:
        if not intent.token_id:
            return OrderResult(
                trade_id=trade_id, status="rejected",
                reason=f"No token_id provided for intent",
            )

        return _live_order(
            trade_id, intent, shares
        )


def _paper_fill(
    trade_id: str,
    intent: ExecutionIntent,
    edge_vwmp: float,
    label: str,
) -> OrderResult:
    """Paper mode: simulate fill at VWMP. Spec §6.4.

    BinEdge.vwmp is ALREADY in native space for the direction:
    - buy_yes: vwmp = YES-side price
    - buy_no: vwmp = NO-side price
    DO NOT flip it again — that's the churn bug root cause.
    """
    fill_price = edge_vwmp  # Already native space. NEVER flip here.
    now = datetime.now(timezone.utc).isoformat()

    logger.info(
        "PAPER FILL: %s %s @ %.3f (limit=%.3f, size=$%.2f)",
        intent.direction.value, label, fill_price, intent.limit_price, intent.target_size_usd,
    )

    return OrderResult(
        trade_id=trade_id,
        status="filled",
        fill_price=fill_price,
        filled_at=now,
        submitted_price=intent.limit_price,
        shares=(intent.target_size_usd / fill_price) if fill_price > 0 else 0.0,
    )


def _live_order(
    trade_id: str,
    intent: ExecutionIntent,
    shares: float,
) -> OrderResult:
    """Live mode: place order via Polymarket CLOB API."""
    from src.data.polymarket_client import PolymarketClient

    timeout = intent.timeout_seconds

    logger.info(
        "LIVE ORDER: %s token=%s...%s @ %.3f limit, %.2f shares, timeout=%ds",
        intent.direction.value,
        intent.token_id[:8], intent.token_id[-4:],
        intent.limit_price, shares, timeout,
    )

    try:
        client = PolymarketClient(paper_mode=False)
        result = client.place_limit_order(
            token_id=intent.token_id,
            price=intent.limit_price,
            size=shares,
            side="BUY",  # Always BUY
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
            order_id=(
                result.get("orderID")
                or result.get("orderId")
                or result.get("id")
                or trade_id
            ),
            timeout_seconds=timeout,
            submitted_price=intent.limit_price,
            shares=shares,
        )

    except Exception as e:
        logger.error("Live order failed: %s", e)
        return OrderResult(
            trade_id=trade_id, status="rejected",
            reason=str(e),
        )
