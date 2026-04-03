"""Exit lifecycle: state machine for live sell orders.

GOLDEN RULE: confirmed sell fill creates economic close, not settlement.
Settlement remains a later harvester-owned transition.

State machine:
  "" → exit_intent → sell_placed → sell_pending → sell_filled (economically_closed)
                    ↘ retry_pending → (back to "" after cooldown for re-evaluation)
                    → backoff_exhausted (hold to settlement, stop retrying)
  exit_intent with no order = stranded by exception → recovered via check_pending_exits

This module owns all exit state transitions. CycleRunner calls it;
CycleRunner does not contain exit business logic.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.execution.collateral import check_sell_collateral
from src.execution.executor import OrderResult, create_exit_order_intent, execute_exit_order
from src.state.portfolio import (
    compute_economic_close,
    ExitContext,
    Position,
    PortfolioState,
    void_position,
)

logger = logging.getLogger(__name__)

MAX_EXIT_RETRIES = 10
DEFAULT_COOLDOWN_SECONDS = 300  # 5 minutes between retries

EXIT_EVENT_VOCABULARY = (
    "EXIT_INTENT",
    "EXIT_ORDER_POSTED",
    "EXIT_ORDER_FILLED",
    "EXIT_ORDER_VOIDED",
    "EXIT_ORDER_REJECTED",
)


@dataclass(frozen=True)
class ExitIntent:
    """Scaffolding contract for explicit exit intent at the engine/execution boundary."""

    trade_id: str
    reason: str
    token_id: str
    shares: float
    current_market_price: float
    best_bid: float | None
    paper_mode: bool


def place_sell_order(
    *,
    trade_id: str,
    token_id: str,
    shares: float,
    current_price: float,
    best_bid: float | None = None,
) -> OrderResult:
    """Thin compatibility adapter over the executor-level exit-order path."""

    return execute_exit_order(
        create_exit_order_intent(
            trade_id=trade_id,
            token_id=token_id,
            shares=shares,
            current_price=current_price,
            best_bid=best_bid,
        )
    )


# CLOB statuses that indicate a fill
FILL_STATUSES = frozenset({"MATCHED", "FILLED"})
VOID_STATUSES = frozenset({"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"})
EXIT_LIFECYCLE_OWNED_STATES = frozenset({"exit_intent", "sell_placed", "sell_pending", "retry_pending"})
EXIT_LIFECYCLE_RECOVERY_STATES = frozenset({"exit_intent", "retry_pending", "backoff_exhausted"})


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_exit_intent(position: Position, exit_context: ExitContext, *, paper_mode: bool) -> ExitIntent:
    """Build the explicit exit-intent contract before any execution behavior happens."""
    token_id = position.token_id if position.direction == "buy_yes" else position.no_token_id
    return ExitIntent(
        trade_id=position.trade_id,
        reason=exit_context.exit_reason,
        token_id=token_id,
        shares=position.effective_shares,
        current_market_price=float(exit_context.current_market_price) if exit_context.current_market_price is not None else 0.0,
        best_bid=exit_context.best_bid,
        paper_mode=paper_mode,
    )


def _validate_exit_intent(position: Position, exit_context: ExitContext, exit_intent: ExitIntent, *, paper_mode: bool) -> None:
    if exit_intent.trade_id != position.trade_id:
        raise ValueError("exit_intent trade_id mismatch")
    expected_token = position.token_id if position.direction == "buy_yes" else position.no_token_id
    if exit_intent.token_id != expected_token:
        raise ValueError("exit_intent token_id mismatch")
    if abs(exit_intent.shares - position.effective_shares) > 1e-9:
        raise ValueError("exit_intent shares mismatch")
    if exit_context.current_market_price is not None and abs(exit_intent.current_market_price - float(exit_context.current_market_price)) > 1e-9:
        raise ValueError("exit_intent current_market_price mismatch")
    if exit_intent.paper_mode is not paper_mode:
        raise ValueError("exit_intent paper_mode mismatch")


def is_exit_cooldown_active(position: Position) -> bool:
    """Check if position is in retry cooldown period."""
    if position.exit_state != "retry_pending":
        return False
    deadline = _parse_iso(position.next_exit_retry_at)
    if deadline is None:
        return False
    return _utcnow() < deadline


def handle_exit_pending_missing(portfolio: PortfolioState, position: Position) -> dict:
    """Own the `exit_pending_missing` escalation path for pending exits."""

    if position.chain_state != "exit_pending_missing":
        return {"action": "ignore", "position": None}
    if position.exit_state in EXIT_LIFECYCLE_RECOVERY_STATES:
        closed = void_position(portfolio, position.trade_id, "EXIT_CHAIN_MISSING_REVIEW_REQUIRED")
        return {"action": "closed", "position": closed}
    if position.exit_state in EXIT_LIFECYCLE_OWNED_STATES:
        return {"action": "skip", "position": None}
    return {"action": "ignore", "position": None}


def execute_exit(
    portfolio: PortfolioState,
    position: Position,
    exit_context: ExitContext,
    paper_mode: bool,
    clob=None,
    conn: sqlite3.Connection | None = None,
    exit_intent: ExitIntent | None = None,
) -> str:
    """Execute an exit decision. Returns outcome description.

    Paper mode: close immediately at market price.
    Live mode: place sell order, check fill, retry on failure.
    NEVER close a live position without confirmed fill.
    """
    if exit_context.current_market_price is None:
        if not paper_mode:
            retry_reason = f"{exit_context.exit_reason or 'EXIT'} [INCOMPLETE_CONTEXT]"
            _mark_exit_retry(position, reason=retry_reason, error="missing_current_market_price")
            return "exit_blocked: incomplete_context"
        return "paper_exit_failed: incomplete_context"

    exit_intent = exit_intent or build_exit_intent(position, exit_context, paper_mode=paper_mode)
    _validate_exit_intent(position, exit_context, exit_intent, paper_mode=paper_mode)

    if paper_mode:
        position.exit_state = "exit_intent"
        closed = compute_economic_close(
            portfolio,
            position.trade_id,
            exit_intent.current_market_price,
            exit_intent.reason,
        )
        if closed is not None:
            closed.exit_state = "sell_filled"
            return f"paper_exit: {exit_intent.reason}"
        return "paper_exit_failed: position_not_found"

    # Live mode: sell order lifecycle
    return _execute_live_exit(
        portfolio,
        position,
        exit_context,
        exit_intent,
        clob,
        conn=conn,
    )


def _execute_live_exit(
    portfolio: PortfolioState,
    position: Position,
    exit_context: ExitContext,
    exit_intent: ExitIntent,
    clob,
    *,
    conn: sqlite3.Connection | None,
) -> str:
    """Live exit: place sell, check fill, retry on failure."""
    if conn is not None:
        from src.state.db import log_exit_attempt_event, log_exit_fill_event, log_exit_retry_event
        from src.state.db import log_pending_exit_recovery_event

        log_pending_exit_recovery_event(
            conn,
            position,
            event_type="EXIT_INTENT",
            reason=exit_intent.reason,
            error="",
        )

    # Pre-sell collateral check (fail-closed)
    can_sell, collateral_reason = check_sell_collateral(
        position.entry_price, position.effective_shares, clob,
    )
    if not can_sell:
        retry_reason = f"{exit_context.exit_reason} [COLLATERAL: {collateral_reason}]"
        _mark_exit_retry(
            position,
            reason=retry_reason,
            error=collateral_reason or "",
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=retry_reason,
                error=collateral_reason or "",
            )
            log_exit_retry_event(conn, position, reason=retry_reason, error=collateral_reason or "")
        return f"collateral_blocked: {collateral_reason}"

    current_market_price = exit_intent.current_market_price
    best_bid = exit_intent.best_bid

    # Cancel stale sell order before retry
    if position.last_exit_order_id and position.exit_retry_count > 0:
        try:
            clob.cancel_order(position.last_exit_order_id)
        except Exception as exc:
            logger.warning("Stale sell cancel failed for %s: %s",
                           position.trade_id, exc)

    # Determine the token to sell
    token_id = exit_intent.token_id
    if not token_id:
        retry_reason = f"{exit_intent.reason} [NO_TOKEN_ID]"
        _mark_exit_retry(position, reason=retry_reason, error="no_token_id")
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=retry_reason,
                error="no_token_id",
            )
            log_exit_retry_event(conn, position, reason=retry_reason, error="no_token_id")
        return "exit_blocked: no_token_id"

    position.exit_state = "exit_intent"

    try:
        raw_sell_result = place_sell_order(
            trade_id=position.trade_id,
            token_id=token_id,
            shares=position.effective_shares,
            current_price=current_market_price,
            best_bid=best_bid,
        )
        sell_result = _coerce_sell_result(position.trade_id, raw_sell_result)

        if sell_result.status == "rejected":
            sell_error = sell_result.reason or "sell_rejected"
            retry_reason = f"{exit_context.exit_reason} [SELL_ERROR: {sell_error}]"
            _mark_exit_retry(
                position,
                reason=retry_reason,
                error=sell_error,
            )
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_REJECTED",
                    reason=retry_reason,
                    error=sell_error,
                )
                log_exit_retry_event(conn, position, reason=retry_reason, error=sell_error)
            return f"sell_error: {sell_error}"

        order_id = sell_result.external_order_id or sell_result.order_id or ""
        position.last_exit_order_id = order_id
        position.exit_state = "sell_placed"
        if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    position,
                    event_type="EXIT_ORDER_POSTED",
                    reason=exit_intent.reason,
                    error="",
                )
                log_exit_attempt_event(
                    conn,
                    position,
                    order_id=order_id,
                    status="placed",
                    current_market_price=current_market_price,
                    best_bid=best_bid,
                    shares=exit_intent.shares,
                    details={
                        "token_id": token_id,
                        "semantic_event": "EXIT_ORDER_POSTED",
                        "sell_result": _serialize_sell_result(sell_result),
                    },
                )

        # Quick fill check (non-blocking — next cycle does full check)
        if order_id and clob:
            status = _check_order_fill(clob, order_id)
            if status in FILL_STATUSES:
                actual_price = _extract_fill_price(sell_result, current_market_price, best_bid)
                closed = compute_economic_close(portfolio, position.trade_id, actual_price, exit_context.exit_reason)
                if closed is not None:
                    closed.exit_state = "sell_filled"
                    if conn is not None:
                        log_exit_fill_event(
                            conn,
                            closed,
                            order_id=order_id,
                            fill_price=actual_price,
                            current_market_price=current_market_price,
                            best_bid=best_bid,
                            timestamp=getattr(closed, "last_exit_at", None),
                        )
                return f"exit_filled: {exit_context.exit_reason}"
            else:
                # Not filled yet — will be checked next cycle
                position.exit_state = "sell_pending"
                if conn is not None:
                    log_exit_attempt_event(
                        conn,
                        position,
                        order_id=order_id,
                        status=status or "pending",
                        current_market_price=current_market_price,
                        best_bid=best_bid,
                        shares=exit_intent.shares,
                        details={"semantic_event": "EXIT_ORDER_POSTED"},
                    )
                return f"sell_pending: order={order_id}, status={status}"

        position.exit_state = "sell_pending"
        if conn is not None:
            log_exit_attempt_event(
                conn,
                position,
                order_id=order_id,
                status="pending",
                current_market_price=current_market_price,
                best_bid=best_bid,
                shares=exit_intent.shares,
                details={"semantic_event": "EXIT_ORDER_POSTED"},
            )
        return f"sell_placed: order={order_id}"

    except Exception as exc:
        # API error — retry next cycle, NEVER close
        retry_reason = f"{exit_context.exit_reason} [ERROR]"
        retry_error = str(exc)[:500]
        _mark_exit_retry(
            position,
            reason=retry_reason,
            error=retry_error,
        )
        if conn is not None:
            log_pending_exit_recovery_event(
                conn,
                position,
                event_type="EXIT_ORDER_REJECTED",
                reason=retry_reason,
                error=retry_error,
            )
            log_exit_retry_event(conn, position, reason=retry_reason, error=retry_error)
        return f"sell_exception: {exc}"


def check_pending_exits(
    portfolio: PortfolioState,
    clob,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Check fill status for positions with pending sell orders.

    Called at start of each cycle, before monitor phase.
    Returns: {"filled": int, "retried": int, "unchanged": int, "filled_positions": list[Position]}
    """
    if conn is not None:
        from src.state.db import (
            log_exit_fill_check_error_event,
            log_exit_fill_event,
            log_pending_exit_recovery_event,
            log_pending_exit_status_event,
            log_exit_retry_event,
        )

    stats = {"filled": 0, "retried": 0, "unchanged": 0, "filled_positions": []}

    for pos in list(portfolio.positions):
        if pos.exit_state not in ("sell_placed", "sell_pending", "exit_intent"):
            continue

        # exit_intent with no order ID = stranded from exception during place_sell_order
        if pos.exit_state == "exit_intent":
            if not pos.last_exit_error:
                stats["unchanged"] += 1
                continue
            _mark_exit_retry(pos, reason="STRANDED_EXIT_INTENT", error="exception_during_sell")
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    pos,
                    event_type="EXIT_INTENT_RECOVERED",
                    reason="STRANDED_EXIT_INTENT",
                    error="exception_during_sell",
                )
                log_exit_retry_event(conn, pos, reason="STRANDED_EXIT_INTENT", error="exception_during_sell")
            stats["retried"] += 1
            continue

        if not pos.last_exit_order_id:
            _mark_exit_retry(pos, reason="SELL_NO_ORDER_ID", error="no_order_id")
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    pos,
                    event_type="EXIT_ORDER_ID_MISSING",
                    reason="SELL_NO_ORDER_ID",
                    error="no_order_id",
                )
                log_exit_retry_event(conn, pos, reason="SELL_NO_ORDER_ID", error="no_order_id")
            stats["retried"] += 1
            continue

        status = _check_order_fill(clob, pos.last_exit_order_id)
        if conn is not None:
            if status:
                log_pending_exit_status_event(conn, pos, status=status)
            else:
                log_exit_fill_check_error_event(conn, pos, order_id=pos.last_exit_order_id)

        if status in FILL_STATUSES:
            # Filled! Close the position.
            actual_price = pos.last_monitor_market_price or pos.entry_price
            exit_reason = pos.exit_reason or "DEFERRED_SELL_FILL"
            closed = compute_economic_close(portfolio, pos.trade_id, actual_price, exit_reason)
            if closed is not None:
                closed.exit_state = "sell_filled"
                stats["filled_positions"].append(closed)
                if conn is not None:
                    log_exit_fill_event(
                        conn,
                        closed,
                        order_id=pos.last_exit_order_id,
                        fill_price=actual_price,
                        current_market_price=actual_price,
                        best_bid=getattr(pos, "last_monitor_best_bid", None),
                        timestamp=getattr(closed, "last_exit_at", None),
                    )
            stats["filled"] += 1
        elif status in VOID_STATUSES:
            _mark_exit_retry(pos, reason=f"SELL_{status}", error=status)
            if conn is not None:
                log_pending_exit_recovery_event(
                    conn,
                    pos,
                    event_type="EXIT_ORDER_VOIDED",
                    reason=f"SELL_{status}",
                    error=status,
                )
                log_exit_retry_event(conn, pos, reason=f"SELL_{status}", error=status)
            stats["retried"] += 1
        elif status == "":
            # Empty status = CLOB outage or API error. Don't stall forever.
            # After 3 consecutive unknown statuses, trigger retry to avoid
            # permanent stall.
            pos.exit_retry_count += 1
            if pos.exit_retry_count >= 3:
                _mark_exit_retry(pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
                if conn is not None:
                    log_exit_retry_event(conn, pos, reason="SELL_STATUS_UNKNOWN", error="3_consecutive_unknown")
                stats["retried"] += 1
            else:
                stats["unchanged"] += 1
        else:
            stats["unchanged"] += 1

    return stats


def check_pending_retries(position: Position, conn: sqlite3.Connection | None = None) -> bool:
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
    if conn is not None:
        from src.state.db import log_exit_retry_released_event
        log_exit_retry_released_event(conn, position)
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


def _coerce_sell_result(trade_id: str, sell_result: OrderResult | dict) -> OrderResult:
    if isinstance(sell_result, OrderResult):
        return sell_result
    if isinstance(sell_result, dict):
        if sell_result.get("error"):
            return OrderResult(
                trade_id=trade_id,
                status="rejected",
                reason=str(sell_result["error"]),
            )
        order_id = (
            sell_result.get("orderID")
            or sell_result.get("orderId")
            or sell_result.get("id")
            or trade_id
        )
        return OrderResult(
            trade_id=trade_id,
            status="pending",
            order_id=order_id,
            external_order_id=order_id,
            submitted_price=sell_result.get("price"),
            shares=sell_result.get("shares"),
            venue_status=str(sell_result.get("status") or "placed"),
            fill_price=sell_result.get("avgPrice") or sell_result.get("avg_price") or sell_result.get("price"),
            reason="sell order posted",
            order_role="exit",
        )
    raise TypeError(f"unsupported sell result type: {type(sell_result)!r}")


def _serialize_sell_result(sell_result: OrderResult | dict) -> dict:
    if isinstance(sell_result, OrderResult):
        return {
            "trade_id": sell_result.trade_id,
            "status": sell_result.status,
            "reason": sell_result.reason,
            "order_id": sell_result.order_id,
            "external_order_id": sell_result.external_order_id,
            "submitted_price": sell_result.submitted_price,
            "shares": sell_result.shares,
            "venue_status": sell_result.venue_status,
            "fill_price": sell_result.fill_price,
            "order_role": sell_result.order_role,
            "intent_id": sell_result.intent_id,
            "idempotency_key": sell_result.idempotency_key,
        }
    return dict(sell_result)


def _extract_fill_price(
    sell_result: OrderResult | dict,
    current_market_price: float,
    best_bid: Optional[float] = None,
) -> float:
    """Extract actual fill price from sell result. Fall back to best_bid or market."""
    if isinstance(sell_result, OrderResult) and sell_result.fill_price not in (None, ""):
        try:
            return float(sell_result.fill_price)
        except (TypeError, ValueError):
            pass
    for key in ("avgPrice", "avg_price", "price"):
        if isinstance(sell_result, dict) and key in sell_result and sell_result[key] not in (None, ""):
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
