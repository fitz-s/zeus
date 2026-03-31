"""Exit lifecycle: state machine for live sell orders.

GOLDEN RULE: close_position() is ONLY called after CLOB confirms FILLED.
In paper mode, close_position() is called directly (no chain interaction).

State machine:
  "" → exit_intent → sell_placed → sell_pending → sell_filled (close_position)
                    ↘ retry_pending → (back to "" after cooldown for re-evaluation)
                    → backoff_exhausted (hold to settlement, stop retrying)
  exit_intent with no order = stranded by exception → recovered via check_pending_exits

This module owns all exit state transitions. CycleRunner calls it;
CycleRunner does not contain exit business logic.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.execution.collateral import check_sell_collateral
from src.execution.executor import place_sell_order
from src.state.portfolio import (
    Position,
    PortfolioState,
    close_position,
)

logger = logging.getLogger(__name__)

MAX_EXIT_RETRIES = 10
DEFAULT_COOLDOWN_SECONDS = 300  # 5 minutes between retries

# CLOB statuses that indicate a fill
FILL_STATUSES = frozenset({"MATCHED", "FILLED"})


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_exit_cooldown_active(position: Position) -> bool:
    """Check if position is in retry cooldown period."""
    if position.exit_state != "retry_pending":
        return False
    deadline = _parse_iso(position.next_exit_retry_at)
    if deadline is None:
        return False
    return _utcnow() < deadline


def execute_exit(
    portfolio: PortfolioState,
    position: Position,
    exit_reason: str,
    current_market_price: float,
    paper_mode: bool,
    clob=None,
    best_bid: Optional[float] = None,
) -> str:
    """Execute an exit decision. Returns outcome description.

    Paper mode: close immediately at market price.
    Live mode: place sell order, check fill, retry on failure.
    NEVER close a live position without confirmed fill.
    """
    if paper_mode:
        closed = close_position(portfolio, position.trade_id, current_market_price, exit_reason)
        if closed is not None:
            closed.exit_state = "sell_filled"
            return f"paper_exit: {exit_reason}"
        return "paper_exit_failed: position_not_found"

    # Live mode: sell order lifecycle
    return _execute_live_exit(
        portfolio, position, exit_reason,
        current_market_price, clob, best_bid,
    )


def _execute_live_exit(
    portfolio: PortfolioState,
    position: Position,
    exit_reason: str,
    current_market_price: float,
    clob,
    best_bid: Optional[float] = None,
) -> str:
    """Live exit: place sell, check fill, retry on failure."""

    # Pre-sell collateral check (fail-closed)
    can_sell, collateral_reason = check_sell_collateral(
        position.entry_price, position.effective_shares, clob,
    )
    if not can_sell:
        _mark_exit_retry(
            position,
            reason=f"{exit_reason} [COLLATERAL: {collateral_reason}]",
            error=collateral_reason or "",
        )
        return f"collateral_blocked: {collateral_reason}"

    # Cancel stale sell order before retry
    if position.last_exit_order_id and position.exit_retry_count > 0:
        try:
            clob.cancel_order(position.last_exit_order_id)
        except Exception as exc:
            logger.warning("Stale sell cancel failed for %s: %s",
                           position.trade_id, exc)

    # Determine the token to sell
    token_id = position.token_id if position.direction == "buy_yes" else position.no_token_id
    if not token_id:
        _mark_exit_retry(position, reason=f"{exit_reason} [NO_TOKEN_ID]", error="no_token_id")
        return "exit_blocked: no_token_id"

    position.exit_state = "exit_intent"

    try:
        sell_result = place_sell_order(
            token_id=token_id,
            shares=position.effective_shares,
            current_price=current_market_price,
            best_bid=best_bid,
        )

        if sell_result.get("error"):
            _mark_exit_retry(
                position,
                reason=f"{exit_reason} [SELL_ERROR: {sell_result['error']}]",
                error=sell_result["error"],
            )
            return f"sell_error: {sell_result['error']}"

        order_id = (
            sell_result.get("orderID")
            or sell_result.get("orderId")
            or sell_result.get("id")
            or ""
        )
        position.last_exit_order_id = order_id
        position.exit_state = "sell_placed"

        # Quick fill check (non-blocking — next cycle does full check)
        if order_id and clob:
            status = _check_order_fill(clob, order_id)
            if status in FILL_STATUSES:
                actual_price = _extract_fill_price(sell_result, current_market_price, best_bid)
                closed = close_position(portfolio, position.trade_id, actual_price, exit_reason)
                if closed is not None:
                    closed.exit_state = "sell_filled"
                return f"exit_filled: {exit_reason}"
            else:
                # Not filled yet — will be checked next cycle
                position.exit_state = "sell_pending"
                return f"sell_pending: order={order_id}, status={status}"

        position.exit_state = "sell_pending"
        return f"sell_placed: order={order_id}"

    except Exception as exc:
        # API error — retry next cycle, NEVER close
        _mark_exit_retry(
            position,
            reason=f"{exit_reason} [ERROR]",
            error=str(exc)[:500],
        )
        return f"sell_exception: {exc}"


def check_pending_exits(
    portfolio: PortfolioState,
    clob,
) -> dict:
    """Check fill status for positions with pending sell orders.

    Called at start of each cycle, before monitor phase.
    Returns: {"filled": int, "retried": int, "unchanged": int}
    """
    stats = {"filled": 0, "retried": 0, "unchanged": 0}

    for pos in list(portfolio.positions):
        if pos.exit_state not in ("sell_placed", "sell_pending", "exit_intent"):
            continue

        # exit_intent with no order ID = stranded from exception during place_sell_order
        if pos.exit_state == "exit_intent":
            _mark_exit_retry(pos, reason="STRANDED_EXIT_INTENT", error="exception_during_sell")
            stats["retried"] += 1
            continue

        if not pos.last_exit_order_id:
            _mark_exit_retry(pos, reason="SELL_NO_ORDER_ID", error="no_order_id")
            stats["retried"] += 1
            continue

        status = _check_order_fill(clob, pos.last_exit_order_id)
        if status in FILL_STATUSES:
            # Filled! Close the position.
            actual_price = pos.last_monitor_market_price or pos.entry_price
            exit_reason = pos.exit_reason or "DEFERRED_SELL_FILL"
            closed = close_position(portfolio, pos.trade_id, actual_price, exit_reason)
            if closed is not None:
                closed.exit_state = "sell_filled"
            stats["filled"] += 1
        elif status in ("CANCELLED", "CANCELED", "EXPIRED", "REJECTED"):
            _mark_exit_retry(pos, reason=f"SELL_{status}", error=status)
            stats["retried"] += 1
        elif status == "":
            # Empty status = CLOB outage or API error. Don't stall forever.
            # After 3 consecutive unknown statuses, trigger retry to avoid
            # permanent stall.
            pos.exit_retry_count += 1
            if pos.exit_retry_count >= 3:
                _mark_exit_retry(pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
                stats["retried"] += 1
            else:
                stats["unchanged"] += 1
        else:
            stats["unchanged"] += 1

    return stats


def check_pending_retries(position: Position) -> bool:
    """Check if a retry-pending position's cooldown has expired.

    Returns True if position is ready for a new exit attempt.
    """
    if position.exit_state == "backoff_exhausted":
        return False  # Hold to settlement, stop retrying

    if position.exit_state != "retry_pending":
        return False

    if is_exit_cooldown_active(position):
        return False  # Still cooling down

    # Cooldown expired — position is eligible for exit re-evaluation
    position.exit_state = ""  # Reset to allow new exit attempt
    return True


def _check_order_fill(clob, order_id: str) -> str:
    """Check CLOB order status. Returns normalized status string."""
    try:
        payload = clob.get_order_status(order_id)
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload.upper()
        if isinstance(payload, dict):
            status = payload.get("status") or payload.get("state") or payload.get("orderStatus")
            return str(status).upper() if status else ""
        return ""
    except Exception as exc:
        logger.warning("Order fill check failed for %s: %s", order_id, exc)
        return ""


def _extract_fill_price(
    sell_result: dict,
    current_market_price: float,
    best_bid: Optional[float] = None,
) -> float:
    """Extract actual fill price from sell result. Fall back to best_bid or market."""
    for key in ("avgPrice", "avg_price", "price"):
        if key in sell_result and sell_result[key] not in (None, ""):
            try:
                return float(sell_result[key])
            except (TypeError, ValueError):
                continue
    return best_bid if best_bid is not None else current_market_price


def _mark_exit_retry(
    position: Position,
    reason: str,
    error: str = "",
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> None:
    """Transition position to retry_pending with exponential backoff."""
    position.exit_retry_count += 1
    position.last_exit_error = error[:500]

    if position.exit_retry_count >= MAX_EXIT_RETRIES:
        position.exit_state = "backoff_exhausted"
        logger.warning(
            "EXIT BACKOFF EXHAUSTED %s: %s (after %d retries). Holding to settlement.",
            position.trade_id, reason, position.exit_retry_count,
        )
        return

    # Exponential cooldown: 5min, 10min, 20min, ... capped at 60min
    actual_cooldown = min(cooldown_seconds * (2 ** (position.exit_retry_count - 1)), 3600)
    position.exit_state = "retry_pending"
    position.next_exit_retry_at = (
        _utcnow() + timedelta(seconds=actual_cooldown)
    ).isoformat()

    logger.warning(
        "EXIT RETRY %s: %s (attempt %d, next retry %s)",
        position.trade_id, reason, position.exit_retry_count,
        position.next_exit_retry_at,
    )
