"""Entry fill verification: pending_tracked → entered | voided.

Live entries create positions with status="pending_tracked" immediately,
even before CLOB confirms the fill. This module checks fill status
each cycle and transitions pending positions appropriately.

This is separate from cycle_runner's existing _reconcile_pending_positions
to maintain the structural constraint: CycleRunner orchestrates, fill_tracker
owns the fill verification logic.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from src.state.portfolio import Position, PortfolioState, void_position

logger = logging.getLogger(__name__)

FILL_STATUSES = frozenset({"FILLED", "MATCHED"})
CANCEL_STATUSES = frozenset({"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"})

# Void pending entries after this many cycles without resolution
MAX_PENDING_CYCLES_WITHOUT_ORDER_ID = 2


def check_pending_entries(
    portfolio: PortfolioState,
    clob,
    tracker=None,
) -> dict:
    """Check fill status for pending_tracked entries.

    Returns: {"entered": int, "voided": int, "still_pending": int}
    """
    stats = {"entered": 0, "voided": 0, "still_pending": 0}
    now = datetime.now(timezone.utc)

    for pos in list(portfolio.positions):
        if pos.state != "pending_tracked":
            continue

        if pos.entry_order_id:
            outcome = _check_entry_fill(pos, portfolio, clob, now, tracker)
        elif pos.order_id:
            # Fallback: use order_id if entry_order_id not set
            pos.entry_order_id = pos.order_id
            outcome = _check_entry_fill(pos, portfolio, clob, now, tracker)
        else:
            # No order ID at all — void after grace period
            outcome = _handle_no_order_id(pos, portfolio)

        stats[outcome] += 1

    return stats


def _check_entry_fill(
    pos: Position,
    portfolio: PortfolioState,
    clob,
    now: datetime,
    tracker=None,
) -> str:
    """Check CLOB status for a single pending entry. Returns outcome key."""
    try:
        payload = clob.get_order_status(pos.entry_order_id)
        status = _normalize_status(payload)
    except Exception as exc:
        logger.warning("Fill check failed for %s: %s", pos.trade_id, exc)
        return "still_pending"

    if status in FILL_STATUSES:
        fill_price = _extract_float(payload, "avgPrice", "avg_price", "price") or pos.entry_price
        shares = _extract_float(payload, "filledSize", "filled_size", "size", "originalSize")
        if shares is None and fill_price > 0:
            shares = pos.size_usd / fill_price

        pos.entry_price = fill_price
        if shares is not None:
            pos.shares = shares
        if pos.cost_basis_usd <= 0:
            pos.cost_basis_usd = pos.size_usd
        pos.state = "entered"
        pos.order_status = status.lower()
        pos.chain_state = "synced"
        pos.entry_fill_verified = True
        pos.entered_at = now.isoformat()
        if tracker is not None:
            tracker.record_entry(pos)
        return "entered"

    if status in CANCEL_STATUSES:
        voided = void_position(portfolio, pos.trade_id, "UNFILLED_ORDER")
        if voided is None:
            # void_position couldn't find it (already removed) — mark directly
            pos.state = "voided"
            pos.exit_reason = "UNFILLED_ORDER"
            pos.admin_exit_reason = "UNFILLED_ORDER"
        return "voided"

    return "still_pending"


def _handle_no_order_id(pos: Position, portfolio: PortfolioState) -> str:
    """Handle pending entries with no order ID. Void after grace period."""
    # Track age via order_posted_at
    if not pos.order_posted_at:
        # First time seeing this — give it one more cycle
        pos.order_posted_at = datetime.now(timezone.utc).isoformat()
        return "still_pending"

    # If it's been pending for too long without an order ID, void it
    voided = void_position(portfolio, pos.trade_id, "UNFILLED_NO_ORDER_ID")
    if voided is not None:
        return "voided"
    return "still_pending"


def _normalize_status(payload) -> str:
    """Normalize CLOB status response to uppercase string."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.upper()
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("state") or payload.get("orderStatus")
        return str(status).upper() if status else ""
    return ""


def _extract_float(payload, *keys: str) -> Optional[float]:
    """Extract first valid float from payload dict."""
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return None
