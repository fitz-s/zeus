"""Entry fill verification: pending_tracked → entered | voided.

Live entries create positions with status="pending_tracked" immediately,
even before CLOB confirms the fill. This module owns the fill-verification
contract; cycle_runtime delegates here as a thin orchestration wrapper.

Chain reconciliation remains the rescue path only when chain truth appears
before CLOB fill verification resolves. Do not create a third semantic owner.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from src.state.lifecycle_manager import (
    enter_filled_entry_runtime_state,
    enter_voided_entry_runtime_state,
)
from src.state.portfolio import PortfolioState, Position, void_position

logger = logging.getLogger(__name__)

FILL_STATUSES = frozenset({"FILLED", "MATCHED"})
CANCEL_STATUSES = frozenset({"CANCELLED", "CANCELED", "EXPIRED", "REJECTED"})

# Void pending entries after this many cycles without resolution
MAX_PENDING_CYCLES_WITHOUT_ORDER_ID = 2


def check_pending_entries(
    portfolio: PortfolioState,
    clob,
    tracker=None,
    *,
    deps=None,
    now: datetime | None = None,
) -> dict:
    """Check fill status for pending_tracked entries.

    Returns: {"entered": int, "voided": int, "still_pending": int, "dirty": bool, "tracker_dirty": bool}
    """
    stats = {
        "entered": 0,
        "voided": 0,
        "still_pending": 0,
        "dirty": False,
        "tracker_dirty": False,
    }
    now = _resolve_now(now, deps)

    for pos in list(portfolio.positions):
        if pos.state != "pending_tracked":
            continue

        if pos.entry_order_id or pos.order_id:
            if not pos.entry_order_id and pos.order_id:
                # Normalize legacy/older pending rows onto the entry-specific field.
                stats["dirty"] = True
            # Fallback: use order_id if entry_order_id not set
            pos.entry_order_id = pos.entry_order_id or pos.order_id
            outcome, dirty, tracker_dirty = _check_entry_fill(
                pos,
                portfolio,
                clob,
                now,
                tracker,
                deps=deps,
            )
        else:
            # No order ID at all — void after grace period
            outcome, dirty, tracker_dirty = _handle_no_order_id(
                pos,
                portfolio,
                now=now,
                deps=deps,
            )

        stats[outcome] += 1
        stats["dirty"] = stats["dirty"] or dirty
        stats["tracker_dirty"] = stats["tracker_dirty"] or tracker_dirty

    return stats


def _resolve_now(now: datetime | None, deps=None) -> datetime:
    if now is not None:
        return now
    if deps is not None and hasattr(deps, "_utcnow"):
        return deps._utcnow()
    return datetime.now(timezone.utc)


def _fill_statuses(deps=None):
    return getattr(deps, "PENDING_FILL_STATUSES", FILL_STATUSES)


def _cancel_statuses(deps=None):
    return getattr(deps, "PENDING_CANCEL_STATUSES", CANCEL_STATUSES)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pending_order_timed_out(pos: Position, now: datetime) -> bool:
    deadline = _parse_iso(getattr(pos, "order_timeout_at", ""))
    return deadline is not None and now >= deadline


def _maybe_update_trade_lifecycle(pos: Position, deps=None) -> None:
    """Production wrapper passes deps; standalone tests stay side-effect free."""
    if deps is None or not hasattr(deps, "get_connection"):
        return

    lifecycle_conn = None
    try:
        from src.state.db import update_trade_lifecycle

        lifecycle_conn = deps.get_connection()
        update_trade_lifecycle(conn=lifecycle_conn, pos=pos)
        lifecycle_conn.commit()
    except Exception as exc:
        logger.warning("Failed to update entry lifecycle for %s: %s", pos.trade_id, exc)
    finally:
        if lifecycle_conn is not None:
            try:
                lifecycle_conn.close()
            except Exception:
                pass


def _maybe_emit_canonical_entry_fill(pos: Position, deps=None) -> None:
    """Append the ENTRY_ORDER_FILLED canonical event so position_current
    advances from pending_entry to active. Events 1 and 2 (OPEN_INTENT,
    ORDER_POSTED) were written at order placement; here we only add the
    fill event at the next available sequence_no.
    """
    if deps is None or not hasattr(deps, "get_connection"):
        return
    fill_conn = None
    try:
        from src.engine.lifecycle_events import build_entry_fill_only_canonical_write
        from src.state.db import append_many_and_project

        fill_conn = deps.get_connection()
        row = fill_conn.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) FROM position_events WHERE position_id = ?",
            (getattr(pos, "trade_id", ""),),
        ).fetchone()
        next_seq = int((row[0] if row else 0) or 0) + 1
        events, projection = build_entry_fill_only_canonical_write(
            pos,
            sequence_no=next_seq,
            source_module="src.execution.fill_tracker",
        )
        append_many_and_project(fill_conn, events, projection)
        fill_conn.commit()
    except Exception as exc:
        logger.warning("Canonical entry-fill emit failed for %s: %s", pos.trade_id, exc)
    finally:
        if fill_conn is not None:
            try:
                fill_conn.close()
            except Exception:
                pass


def _maybe_log_execution_fill(
    pos: Position,
    *,
    submitted_price: float | None,
    shares: float | None,
    deps=None,
) -> None:
    if deps is None or not hasattr(deps, "get_connection"):
        return

    telemetry_conn = None
    try:
        from src.state.db import log_execution_report

        telemetry_conn = deps.get_connection()
        log_execution_report(
            telemetry_conn,
            pos,
            SimpleNamespace(
                status="filled",
                fill_price=getattr(pos, "entry_price", None),
                filled_at=getattr(pos, "entered_at", None),
                submitted_price=submitted_price,
                shares=shares,
                timeout_seconds=None,
            ),
        )
        telemetry_conn.commit()
    except Exception as exc:
        logger.warning("Failed to log entry fill telemetry for %s: %s", pos.trade_id, exc)
    finally:
        if telemetry_conn is not None:
            try:
                telemetry_conn.close()
            except Exception:
                pass


def _mark_entry_filled(
    pos: Position,
    payload,
    now: datetime,
    tracker=None,
    *,
    deps=None,
) -> tuple[str, bool, bool]:
    submitted_price = pos.entry_price
    fill_price = _extract_float(payload, "avgPrice", "avg_price", "price") or pos.entry_price
    shares = _extract_float(payload, "filledSize", "filled_size", "size", "originalSize")
    if shares is None and fill_price > 0:
        shares = pos.size_usd / fill_price

    pos.entry_price = fill_price
    pos.entry_order_id = pos.entry_order_id or pos.order_id
    pos.order_id = pos.order_id or pos.entry_order_id or ""
    pos.entry_fill_verified = True
    if shares is not None:
        pos.shares = shares
        actual_cost_basis = shares * fill_price
        if actual_cost_basis > 0:
            pos.size_usd = actual_cost_basis
            pos.cost_basis_usd = actual_cost_basis
    elif pos.cost_basis_usd <= 0:
        pos.cost_basis_usd = pos.size_usd
    if submitted_price not in (None, 0) and fill_price not in (None, 0):
        try:
            pos.fill_quality = (float(fill_price) - float(submitted_price)) / float(submitted_price)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    pos.state = enter_filled_entry_runtime_state(
        pos.state,
        exit_state=getattr(pos, "exit_state", ""),
        chain_state=getattr(pos, "chain_state", ""),
    )
    pos.order_status = "filled"
    pos.chain_state = "local_only"
    pos.entered_at = now.isoformat()

    _maybe_update_trade_lifecycle(pos, deps=deps)
    _maybe_emit_canonical_entry_fill(pos, deps=deps)
    _maybe_log_execution_fill(
        pos,
        submitted_price=submitted_price,
        shares=shares,
        deps=deps,
    )
    if tracker is not None:
        tracker.record_entry(pos)
        return "entered", True, True
    return "entered", True, False


def _mark_entry_voided(
    portfolio: PortfolioState,
    pos: Position,
    reason: str,
    *,
    deps=None,
) -> tuple[str, bool, bool]:
    voided = void_position(portfolio, pos.trade_id, reason)
    target = voided or pos
    if voided is None:
        target.state = enter_voided_entry_runtime_state(
            target.state,
            exit_state=getattr(target, "exit_state", ""),
            chain_state=getattr(target, "chain_state", ""),
        )
        target.exit_reason = reason
        target.admin_exit_reason = reason
    _maybe_update_trade_lifecycle(target, deps=deps)
    return "voided", True, False


def _check_entry_fill(
    pos: Position,
    portfolio: PortfolioState,
    clob,
    now: datetime,
    tracker=None,
    *,
    deps=None,
) -> tuple[str, bool, bool]:
    """Check CLOB status for a single pending entry. Returns outcome + dirty bits."""
    try:
        payload = clob.get_order_status(pos.entry_order_id)
        status = _normalize_status(payload)
    except Exception as exc:
        logger.warning("Fill check failed for %s: %s", pos.trade_id, exc)
        return "still_pending", False, False

    if status in _fill_statuses(deps):
        return _mark_entry_filled(pos, payload, now, tracker, deps=deps)

    if status in _cancel_statuses(deps):
        return _mark_entry_voided(portfolio, pos, "UNFILLED_ORDER", deps=deps)

    if _pending_order_timed_out(pos, now):
        order_id = pos.order_id or pos.entry_order_id
        cancel_succeeded = True
        if order_id and hasattr(clob, "cancel_order"):
            try:
                cancel_payload = clob.cancel_order(order_id)
                if cancel_payload is None:
                    cancel_succeeded = False
                else:
                    cancel_status = _normalize_status(cancel_payload)
                    cancel_succeeded = cancel_status in _cancel_statuses(deps)
            except Exception as exc:
                logger.warning("Cancel failed for timed-out order %s: %s", order_id, exc)
                cancel_succeeded = False
        if cancel_succeeded:
            return _mark_entry_voided(portfolio, pos, "UNFILLED_ORDER", deps=deps)
        return "still_pending", False, False

    if status:
        normalized = status.lower()
        if pos.order_status != normalized:
            pos.order_status = normalized
            return "still_pending", True, False
    return "still_pending", False, False


def _handle_no_order_id(
    pos: Position,
    portfolio: PortfolioState,
    *,
    now: datetime,
    deps=None,
) -> tuple[str, bool, bool]:
    """Handle pending entries with no order ID. Void after grace period."""
    # Track age via order_posted_at
    if not pos.order_posted_at:
        # First time seeing this — give it one more cycle
        pos.order_posted_at = now.isoformat()
        return "still_pending", True, False

    # If it's been pending for too long without an order ID, void it
    return _mark_entry_voided(portfolio, pos, "UNFILLED_NO_ORDER_ID", deps=deps)


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
