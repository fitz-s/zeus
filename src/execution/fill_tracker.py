"""Entry fill verification: pending_tracked → entered | voided.

Live entries create positions with status="pending_tracked" immediately,
even before CLOB confirms the fill. This module owns the fill-verification
contract; cycle_runtime delegates here as a thin orchestration wrapper.

Chain reconciliation remains the rescue path only when chain truth appears
before CLOB fill verification resolves. Do not create a third semantic owner.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

from src.state.lifecycle_manager import (
    enter_filled_entry_runtime_state,
    enter_voided_entry_runtime_state,
)
from src.state.portfolio import (
    CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION,
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    FILL_AUTHORITY_CANCELLED_REMAINDER,
    FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    PortfolioState,
    Position,
    void_position,
)

logger = logging.getLogger(__name__)

FILL_STATUSES = frozenset({"CONFIRMED"})
OPTIMISTIC_FILL_STATUSES = frozenset({"MATCHED", "FILLED"})
PARTIAL_FILL_STATUSES = frozenset({"PARTIAL", "PARTIALLY_MATCHED", "PARTIALLY_FILLED"})
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
    statuses = frozenset(getattr(deps, "PENDING_FILL_STATUSES", FILL_STATUSES))
    # Older runtime deps exported MATCHED/FILLED as fill statuses. Keep those
    # stale constants from bypassing the explicit optimistic-finality branch,
    # but never let stale deps remove CONFIRMED as the only success terminal.
    return (statuses | FILL_STATUSES) - OPTIMISTIC_FILL_STATUSES - PARTIAL_FILL_STATUSES


def _cancel_statuses(deps=None):
    return getattr(deps, "PENDING_CANCEL_STATUSES", CANCEL_STATUSES)


def _optimistic_fill_statuses(deps=None):
    return getattr(deps, "PENDING_OPTIMISTIC_FILL_STATUSES", OPTIMISTIC_FILL_STATUSES)


def _partial_fill_statuses(deps=None):
    return getattr(deps, "PENDING_PARTIAL_FILL_STATUSES", PARTIAL_FILL_STATUSES)


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


def _maybe_update_trade_lifecycle(pos: Position, deps=None) -> bool:
    """Production wrapper passes deps; standalone tests stay side-effect free.
    Returns True on success or no-op, False if DB write fails."""
    if deps is None or not hasattr(deps, "get_connection"):
        return True

    lifecycle_conn = None
    try:
        from src.state.db import update_trade_lifecycle

        lifecycle_conn = deps.get_connection()
        update_trade_lifecycle(conn=lifecycle_conn, pos=pos)
        lifecycle_conn.commit()
        return True
    except Exception as exc:
        logger.error(f"Trade lifecycle DB update failed for {pos.trade_id}: {exc}")
        return False
    finally:
        if lifecycle_conn is not None:
            try:
                lifecycle_conn.close()
            except Exception:
                pass


def _maybe_emit_canonical_entry_fill(pos: Position, deps=None) -> bool:
    """Append the ENTRY_ORDER_FILLED canonical event so position_current
    advances from pending_entry to active. Events 1 and 2 (OPEN_INTENT,
    ORDER_POSTED) were written at order placement; here we only add the
    fill event at the next available sequence_no.
    Returns True on success or no-op, False if DB write fails.
    """
    if deps is None or not hasattr(deps, "get_connection"):
        return True
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
        return True
    except Exception as exc:
        logger.error(f"Canonical entry-fill DB update failed for {pos.trade_id}: {exc}")
        return False
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
    execution_status: str = "filled",
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
                status=execution_status,
                fill_price=getattr(pos, "entry_price", None),
                filled_at=getattr(pos, "entered_at", None),
                submitted_price=submitted_price,
                shares=shares,
                timeout_seconds=None,
            ),
        )
        telemetry_conn.commit()
    except Exception as exc:
        raise RuntimeError(f"Execution telemetry DB update failed for {pos.trade_id}: {exc}") from exc
    finally:
        if telemetry_conn is not None:
            try:
                telemetry_conn.close()
            except Exception:
                pass


def _maybe_append_venue_fill_observation(
    pos: Position,
    payload: Any,
    *,
    status: str,
    shares: float | None,
    fill_price: float | None,
    observed_at: datetime,
    deps=None,
) -> bool:
    """Append truthful U2 venue facts before mutating local position state.

    Order polling often exposes order facts without trade identity. We record
    those order facts when a venue command is linkable, but never synthesize a
    trade id. Trade facts and lots are written only when the payload carries
    explicit trade identity.
    """
    if deps is None or not hasattr(deps, "get_connection"):
        return True
    order_id = str(getattr(pos, "entry_order_id", "") or getattr(pos, "order_id", "") or "").strip()
    if not order_id:
        return True

    conn = None
    try:
        from src.state.venue_command_repo import (
            append_event,
            append_order_fact,
            append_position_lot,
            append_trade_fact,
        )

        conn = deps.get_connection()
        row = conn.execute(
            """
            SELECT *
              FROM venue_commands
             WHERE venue_order_id = ?
             ORDER BY updated_at DESC, created_at DESC
             LIMIT 1
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            return True

        command = dict(row)
        payload_dict = payload if isinstance(payload, dict) else {"raw": payload}
        payload_hash = _payload_hash(payload_dict)
        trade_id = _extract_trade_id(payload_dict)
        order_fact_state = _order_fact_state_for_status(status)
        order_fact_id = None
        if order_fact_state is not None:
            order_fact_id = append_order_fact(
                conn,
                venue_order_id=order_id,
                command_id=str(command["command_id"]),
                state=order_fact_state,
                remaining_size=_remaining_size(command, shares),
                matched_size=_decimal_str(shares),
                source="REST",
                observed_at=observed_at,
                venue_timestamp=_payload_timestamp(payload_dict),
                raw_payload_hash=payload_hash,
                raw_payload_json=payload_dict,
            )
            if not trade_id:
                event_type = (
                    "FILL_CONFIRMED"
                    if str(status or "").upper() == "CONFIRMED"
                    else "PARTIAL_FILL_OBSERVED"
                )
                try:
                    append_event(
                        conn,
                        command_id=str(command["command_id"]),
                        event_type=event_type,
                        occurred_at=observed_at.isoformat(),
                        payload={
                            "source": "REST",
                            "venue_order_id": order_id,
                            "order_fact_id": order_fact_id,
                            "status": status,
                        },
                    )
                except ValueError:
                    pass

        trade_state = _trade_fact_state_for_status(status, payload_dict)
        trade_fact_id = None
        if trade_id and trade_state:
            trade_fact_id = append_trade_fact(
                conn,
                trade_id=trade_id,
                venue_order_id=order_id,
                command_id=str(command["command_id"]),
                state=trade_state,
                filled_size=_decimal_str(shares, "0"),
                fill_price=_decimal_str(fill_price, "0"),
                source="REST",
                observed_at=observed_at,
                venue_timestamp=_payload_timestamp(payload_dict),
                raw_payload_hash=payload_hash,
                raw_payload_json=payload_dict,
                tx_hash=_first_text(payload_dict, "transaction_hash", "tx_hash"),
                block_number=_extract_int(payload_dict, "block_number", "blockNumber"),
                confirmation_count=_extract_int(payload_dict, "confirmation_count", "confirmationCount"),
            )
            lot_state = {
                "MATCHED": "OPTIMISTIC_EXPOSURE",
                "CONFIRMED": "CONFIRMED_EXPOSURE",
            }.get(trade_state)
            position_id = _position_id_from_command(command, conn)
            if (
                lot_state
                and position_id is not None
                and str(command.get("intent_kind") or "").upper() == "ENTRY"
                and str(command.get("side") or "").upper() == "BUY"
            ):
                append_position_lot(
                    conn,
                    position_id=position_id,
                    state=lot_state,
                    shares=_int_shares(shares),
                    entry_price_avg=_decimal_str(fill_price, "0"),
                    source_command_id=str(command["command_id"]),
                    source_trade_fact_id=trade_fact_id,
                    captured_at=observed_at,
                    state_changed_at=observed_at,
                    source="REST",
                    observed_at=observed_at,
                    venue_timestamp=_payload_timestamp(payload_dict),
                    raw_payload_json=payload_dict,
                )
            event_type = "FILL_CONFIRMED" if trade_state == "CONFIRMED" else "PARTIAL_FILL_OBSERVED"
            try:
                append_event(
                    conn,
                    command_id=str(command["command_id"]),
                    event_type=event_type,
                    occurred_at=observed_at.isoformat(),
                    payload={
                        "source": "REST",
                        "venue_order_id": order_id,
                        "trade_id": trade_id,
                        "trade_fact_id": trade_fact_id,
                        "status": status,
                    },
                )
            except ValueError:
                # Facts remain authoritative; do not widen command grammar from
                # the legacy polling path.
                pass
        conn.commit()
        return True
    except Exception as exc:
        logger.error("Venue fill fact append failed for %s: %s", pos.trade_id, exc)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _apply_entry_fill_economics(
    pos: Position,
    *,
    fill_price: float,
    shares: float | None,
    fill_authority: str,
    full_fill: bool,
) -> None:
    if shares is None or shares <= 0 or fill_price <= 0:
        return
    submitted_limit = float(
        getattr(pos, "entry_price_submitted", 0.0)
        or getattr(pos, "entry_price", 0.0)
        or 0.0
    )
    submitted_shares = float(getattr(pos, "shares_submitted", 0.0) or 0.0)
    if submitted_shares <= 0:
        submitted_shares = float(
            shares if full_fill else getattr(pos, "shares", 0.0) or shares
        )
    filled_cost_basis = float(shares) * float(fill_price)
    pos.target_notional_usd = float(
        getattr(pos, "target_notional_usd", 0.0)
        or getattr(pos, "size_usd", 0.0)
        or 0.0
    )
    pos.entry_price_submitted = submitted_limit
    pos.submitted_notional_usd = (
        submitted_shares * submitted_limit if submitted_limit > 0 else 0.0
    )
    pos.shares_submitted = submitted_shares
    pos.entry_price_avg_fill = float(fill_price)
    pos.shares_filled = float(shares)
    pos.filled_cost_basis_usd = filled_cost_basis
    pos.shares_remaining = max(0.0, submitted_shares - float(shares))
    pos.entry_economics_authority = ENTRY_ECONOMICS_AVG_FILL_PRICE
    pos.fill_authority = fill_authority
    _refresh_corrected_economics_eligibility(pos)


def _refresh_corrected_economics_eligibility(pos: Position) -> None:
    pos.corrected_executable_economics_eligible = (
        pos.has_fill_economics_authority
        and pos.pricing_semantics_version == CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION
    )


def _mark_entry_filled(
    pos: Position,
    payload,
    now: datetime,
    tracker=None,
    *,
    order_status: str = "filled",
    execution_status: str = "filled",
    deps=None,
) -> tuple[str, bool, bool]:
    submitted_price = pos.entry_price
    fill_price = _extract_float(payload, "avgPrice", "avg_price", "price") or pos.entry_price
    shares = _extract_filled_shares(payload, allow_order_size_fallback=True)
    if shares is None and getattr(pos, "shares", 0) not in (None, 0):
        shares = float(getattr(pos, "shares"))
    if shares is None and fill_price > 0:
        shares = pos.size_usd / fill_price

    ledger_ok = _maybe_append_venue_fill_observation(
        pos,
        payload,
        status=str(order_status or execution_status or "filled").upper(),
        shares=shares,
        fill_price=fill_price,
        observed_at=now,
        deps=deps,
    )
    if not ledger_ok:
        pos.state = "quarantine_fill_failed"
        return "still_pending", True, False

    _apply_entry_fill_economics(
        pos,
        fill_price=fill_price,
        shares=shares,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
        full_fill=True,
    )
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
    pos.order_status = order_status
    pos.chain_state = "local_only"
    pos.entered_at = now.isoformat()

    lc_ok = _maybe_update_trade_lifecycle(pos, deps=deps)
    cf_ok = _maybe_emit_canonical_entry_fill(pos, deps=deps)
    if not lc_ok or not cf_ok:
        pos.state = "quarantine_fill_failed"
        
    _maybe_log_execution_fill(
        pos,
        submitted_price=submitted_price,
        shares=shares,
        execution_status=execution_status,
        deps=deps,
    )
    if tracker is not None:
        tracker.record_entry(pos)
        return "entered", True, True
    return "entered", True, False


def _record_partial_entry_observed(
    pos: Position,
    payload,
    now: datetime,
    *,
    deps=None,
) -> tuple[str, bool, bool]:
    fill_price = _extract_float(payload, "avgPrice", "avg_price", "price") or pos.entry_price
    shares = _extract_filled_shares(payload, allow_order_size_fallback=False)
    if shares is None or shares <= 0:
        return _update_pending_status(pos, "partial")

    ledger_ok = _maybe_append_venue_fill_observation(
        pos,
        payload,
        status="PARTIALLY_MATCHED",
        shares=shares,
        fill_price=fill_price,
        observed_at=now,
        deps=deps,
    )
    if not ledger_ok:
        pos.state = "quarantine_fill_failed"
        return "still_pending", True, False

    _apply_entry_fill_economics(
        pos,
        fill_price=fill_price,
        shares=shares,
        fill_authority=FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
        full_fill=False,
    )
    pos.entry_price = fill_price
    pos.entry_order_id = pos.entry_order_id or pos.order_id
    pos.order_id = pos.order_id or pos.entry_order_id or ""
    pos.entry_fill_verified = False
    pos.shares = shares
    actual_cost_basis = shares * fill_price
    if actual_cost_basis > 0:
        pos.size_usd = actual_cost_basis
        pos.cost_basis_usd = actual_cost_basis
    pos.order_status = "partial"
    return "still_pending", True, False


def _record_optimistic_entry_observed(
    pos: Position,
    payload,
    now: datetime,
    *,
    status: str,
    deps=None,
) -> tuple[str, bool, bool]:
    fill_price = _extract_float(payload, "avgPrice", "avg_price", "price") or pos.entry_price
    shares = _extract_filled_shares(payload, allow_order_size_fallback=False)
    if shares is None or shares <= 0:
        return _update_pending_status(pos, status.lower())

    ledger_ok = _maybe_append_venue_fill_observation(
        pos,
        payload,
        status=status,
        shares=shares,
        fill_price=fill_price,
        observed_at=now,
        deps=deps,
    )
    if not ledger_ok:
        pos.state = "quarantine_fill_failed"
        return "still_pending", True, False

    _apply_entry_fill_economics(
        pos,
        fill_price=fill_price,
        shares=shares,
        fill_authority=FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
        full_fill=False,
    )
    pos.entry_price = fill_price
    pos.entry_order_id = pos.entry_order_id or pos.order_id
    pos.order_id = pos.order_id or pos.entry_order_id or ""
    pos.entry_fill_verified = False
    pos.shares = shares
    actual_cost_basis = shares * fill_price
    if actual_cost_basis > 0:
        pos.size_usd = actual_cost_basis
        pos.cost_basis_usd = actual_cost_basis
    _update_pending_status(pos, status.lower())
    return "still_pending", True, False


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
        
    lc_ok = _maybe_update_trade_lifecycle(target, deps=deps)
    if not lc_ok:
        target.state = "quarantine_void_failed"
        
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
    # B041: typed error taxonomy (SD-B). The previous ``except Exception``
    # conflated two distinct states under ``still_pending``:
    #   1. legitimate transient IO errors (network / timeout / auth) where
    #      the exchange state is genuinely unknown this cycle \u2014 the
    #      correct answer is ``still_pending`` so we retry next cycle.
    #   2. code defects (AttributeError on a wrong-shape clob, TypeError
    #      from a regression, ImportError, KeyError / IndexError from
    #      ``_normalize_status`` reading a missing field) which must NOT
    #      be silently retried forever. These are exchange-silent latent
    #      bugs.
    # Re-raise the code-defect classes; legitimate IO failures still map
    # to ``still_pending``.
    #
    # Amendment (critic-alice review): KeyError / IndexError were omitted
    # from the first pass. ``_normalize_status(payload)`` reads
    # ``payload["status"]``; a malformed CLOB response shape would raise
    # KeyError which was silently caught as ``still_pending``. Those two
    # are now in the re-raise set.
    try:
        payload = clob.get_order_status(pos.entry_order_id)
        status = _normalize_status(payload)
    except (AttributeError, TypeError, ImportError, NameError, KeyError, IndexError):
        raise
    except Exception as exc:
        logger.warning("Fill check failed for %s: %s", pos.trade_id, exc)
        return "still_pending", False, False

    if status in _fill_statuses(deps):
        return _mark_entry_filled(
            pos,
            payload,
            now,
            tracker,
            order_status=status.lower(),
            execution_status="filled",
            deps=deps,
        )

    if status in _optimistic_fill_statuses(deps):
        if _extract_filled_shares(payload, allow_order_size_fallback=False) is None:
            return _update_pending_status(pos, status.lower())
        return _record_optimistic_entry_observed(
            pos,
            payload,
            now,
            status=status,
            deps=deps,
        )

    if status in _partial_fill_statuses(deps):
        outcome, dirty, tracker_dirty = _record_partial_entry_observed(pos, payload, now, deps=deps)
        if getattr(pos, "state", "") == "quarantine_fill_failed":
            return outcome, dirty, tracker_dirty
        if _pending_order_timed_out(pos, now):
            cancel_succeeded = _cancel_order_remainder(pos, clob, deps=deps)
            if cancel_succeeded:
                if _position_has_observed_exposure(pos):
                    pos.fill_authority = FILL_AUTHORITY_CANCELLED_REMAINDER
                    _refresh_corrected_economics_eligibility(pos)
                    pending_outcome, pending_dirty, pending_tracker_dirty = _update_pending_status(
                        pos,
                        "partial_remainder_cancelled",
                    )
                    return (
                        pending_outcome,
                        dirty or pending_dirty,
                        tracker_dirty or pending_tracker_dirty,
                    )
                return _mark_entry_voided(portfolio, pos, "UNFILLED_ORDER", deps=deps)
        return outcome, dirty, tracker_dirty

    if status in _cancel_statuses(deps):
        dirty = False
        tracker_dirty = False
        if _extract_filled_shares(payload, allow_order_size_fallback=False):
            outcome, dirty, tracker_dirty = _record_partial_entry_observed(pos, payload, now, deps=deps)
            if getattr(pos, "state", "") == "quarantine_fill_failed":
                return outcome, dirty, tracker_dirty
        if _position_has_observed_exposure(pos):
            pos.fill_authority = FILL_AUTHORITY_CANCELLED_REMAINDER
            _refresh_corrected_economics_eligibility(pos)
            pending_outcome, pending_dirty, pending_tracker_dirty = _update_pending_status(
                pos,
                "partial_remainder_cancelled",
            )
            return pending_outcome, dirty or pending_dirty, tracker_dirty or pending_tracker_dirty
        return _mark_entry_voided(portfolio, pos, "UNFILLED_ORDER", deps=deps)

    if _pending_order_timed_out(pos, now):
        cancel_succeeded = _cancel_order_remainder(pos, clob, deps=deps)
        if cancel_succeeded:
            if _position_has_observed_exposure(pos):
                pos.fill_authority = FILL_AUTHORITY_CANCELLED_REMAINDER
                _refresh_corrected_economics_eligibility(pos)
                return _update_pending_status(
                    pos,
                    "partial_remainder_cancelled",
                )
            return _mark_entry_voided(portfolio, pos, "UNFILLED_ORDER", deps=deps)
        return "still_pending", False, False

    if status:
        return _update_pending_status(pos, status.lower())
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

    # If it's been pending for too long without an order ID, quarantine it
    # rather than destroying it, since it may have hit the exchange engine.
    pos.state = "quarantine_no_order_id"
    _maybe_update_trade_lifecycle(pos, deps=deps)
    return "still_pending", True, False


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


def _extract_filled_shares(payload, *, allow_order_size_fallback: bool) -> Optional[float]:
    shares = _extract_float(
        payload,
        "filledSize",
        "filled_size",
        "filledAmount",
        "filled_amount",
        "matchedSize",
        "matched_size",
        "sizeMatched",
        "size_matched",
    )
    if shares is not None:
        return shares
    if allow_order_size_fallback:
        return _extract_float(payload, "size", "originalSize", "original_size")
    return None


def _update_pending_status(pos: Position, status: str) -> tuple[str, bool, bool]:
    if status and pos.order_status != status:
        pos.order_status = status
        return "still_pending", True, False
    return "still_pending", False, False


def _position_has_observed_exposure(pos: Position) -> bool:
    observed_statuses = frozenset(
        {
            "matched",
            "filled",
            "partial",
            "partially_matched",
            "partially_filled",
            "partial_remainder_cancelled",
        }
    )
    if str(getattr(pos, "order_status", "") or "").lower() not in observed_statuses:
        return False
    try:
        return (
            float(getattr(pos, "shares_filled", 0) or 0) > 0
            or float(getattr(pos, "shares", 0) or 0) > 0
        )
    except (TypeError, ValueError):
        return False


def _cancel_order_remainder(pos: Position, clob, *, deps=None) -> bool:
    order_id = pos.order_id or pos.entry_order_id
    if not order_id or not hasattr(clob, "cancel_order"):
        return True
    try:
        cancel_payload = clob.cancel_order(order_id)
        if cancel_payload is None:
            return False
        cancel_status = _normalize_status(cancel_payload)
        return cancel_status in _cancel_statuses(deps)
    except Exception as exc:
        logger.warning("Cancel failed for timed-out order %s: %s", order_id, exc)
        return False


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decimal_str(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    return str(value)


def _int_shares(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _order_fact_state_for_status(status: str) -> str | None:
    normalized = str(status or "").upper()
    if normalized in PARTIAL_FILL_STATUSES:
        return "PARTIALLY_MATCHED"
    if normalized in {"MATCHED", "FILLED", "CONFIRMED"}:
        return "MATCHED"
    return None


def _trade_fact_state_for_status(status: str, payload: dict) -> str | None:
    direct = _first_text(payload, "trade_status", "tradeStatus", "trade_state", "tradeState")
    normalized = str(direct or status or "").upper()
    if normalized in PARTIAL_FILL_STATUSES:
        return "MATCHED"
    if normalized in {"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"}:
        return normalized
    return None


def _extract_trade_id(payload: dict) -> str | None:
    direct = _first_text(payload, "trade_id", "tradeId", "tradeID")
    if direct:
        return direct
    for key in ("trade_ids", "tradeIds", "tradeIDs"):
        value = payload.get(key)
        if isinstance(value, (list, tuple)) and value:
            first = str(value[0]).strip()
            if first:
                return first
    return None


def _position_id_from_command(command: dict, conn=None) -> int | None:
    """Resolve U2 lot identity without synthesizing integer position ids.

    Live executor commands store the runtime trade id in venue_commands.position_id.
    After materialization, trade_decisions.runtime_trade_id maps that runtime id
    to the integer trade_decisions.trade_id used by the current position_lots
    schema. If neither the direct numeric compatibility fields nor that mapping
    exist, polling records order/trade facts but skips the lot projection.
    """

    if conn is None:
        return None

    # The live executor's runtime id is a UUID prefix and may be all digits.
    # Treat command identity fields as runtime aliases first; only accept a
    # numeric value as canonical after checking the target decision row.
    for key in ("position_id", "decision_id"):
        parsed = _trade_decision_id_for_runtime_id(conn, command.get(key))
        if parsed is not None:
            return parsed

    position_id = command.get("position_id")
    parsed_position_id = _parse_positive_int(position_id)
    if parsed_position_id is not None and _trade_decision_id_is_compatible(
        conn,
        parsed_position_id,
        runtime_trade_id=position_id,
    ):
        return parsed_position_id

    decision_id = command.get("decision_id")
    parsed_decision_id = _parse_positive_int(decision_id)
    if parsed_decision_id is not None and _trade_decision_id_is_compatible(
        conn,
        parsed_decision_id,
        runtime_trade_id=position_id,
    ):
        return parsed_decision_id
    return None


def _parse_positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _trade_decision_id_for_runtime_id(conn, runtime_trade_id: Any) -> int | None:
    runtime_id = str(runtime_trade_id or "").strip()
    if not runtime_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT trade_id
              FROM trade_decisions
             WHERE runtime_trade_id = ?
             ORDER BY trade_id DESC
             LIMIT 1
            """,
            (runtime_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    if hasattr(row, "keys"):
        return _parse_positive_int(row["trade_id"])
    return _parse_positive_int(row[0])


def _trade_decision_id_is_compatible(
    conn,
    trade_decision_id: int,
    *,
    runtime_trade_id: Any,
) -> bool:
    try:
        row = conn.execute(
            """
            SELECT runtime_trade_id
              FROM trade_decisions
             WHERE trade_id = ?
             LIMIT 1
            """,
            (int(trade_decision_id),),
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    row_runtime = row["runtime_trade_id"] if hasattr(row, "keys") else row[0]
    row_runtime_s = str(row_runtime or "").strip()
    expected_runtime_s = str(runtime_trade_id or "").strip()
    return not row_runtime_s or not expected_runtime_s or row_runtime_s == expected_runtime_s


def _remaining_size(command: dict, shares: float | None) -> str | None:
    try:
        size = float(command.get("size") or 0)
        filled = float(shares or 0)
    except (TypeError, ValueError):
        return None
    return str(max(0.0, size - filled))


def _payload_timestamp(payload: dict) -> str | None:
    return _first_text(payload, "timestamp", "created_at", "createdAt", "updated_at", "updatedAt")


def _first_text(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_int(payload: dict, *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
