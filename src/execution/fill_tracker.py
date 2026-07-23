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
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Optional

from src.contracts.canonical_lifecycle import VenueOrderStatus, VenueTradeStatus
from src.contracts.chain_observation_envelope import (
    UNOBSERVED_CHAIN_ENVELOPE,
    ChainObservationEnvelope,
)
from src.contracts.review_work_item import ReviewReasonCode
from src.state.lifecycle_manager import (
    enter_filled_entry_runtime_state,
    enter_voided_entry_runtime_state,
)
from src.state.portfolio import (
    CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION,
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE,
    FILL_AUTHORITY_CANCELLED_REMAINDER,
    FILL_AUTHORITY_NONE,
    FILL_AUTHORITY_OPTIMISTIC_SUBMITTED,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    PortfolioState,
    Position,
    void_position,
)

logger = logging.getLogger(__name__)

# Venue-fill classifications tied to the canonical VenueTradeStatus / VenueOrderStatus
# enums (single source). Identical values; raw synonyms (FILLED, PARTIAL,
# PARTIALLY_FILLED, CANCELLED/CANCELED, OPEN, ACCEPTED, RESTING) are folded at ingress
# by canonical_lifecycle normalizers and retained here only during the migration.
FILL_STATUSES = frozenset({VenueTradeStatus.CONFIRMED.value})
OPTIMISTIC_FILL_STATUSES = frozenset({VenueTradeStatus.MATCHED.value, VenueTradeStatus.MINED.value, "FILLED"})
PARTIAL_FILL_STATUSES = frozenset({VenueOrderStatus.PARTIALLY_MATCHED.value, "PARTIAL", "PARTIALLY_FILLED"})
TRADE_FILL_ECONOMICS_STATUSES = frozenset({VenueTradeStatus.MATCHED.value, VenueTradeStatus.MINED.value, VenueTradeStatus.CONFIRMED.value})
CANCEL_STATUSES = frozenset({VenueOrderStatus.EXPIRED.value, "CANCELLED", "CANCELED", "REJECTED"})
# Resting-open statuses: a post_only/GTC maker order that has reached the venue
# and is RESTING on the book (not yet filled). These must land a LIVE
# venue_order_facts row linked by command_id so resting fills/partials/cancels
# can be tracked — without it the command_id join yields NO_FACT for post_only
# GTC orders and the maker fill-rate measurement loop is blind.
RESTING_OPEN_STATUSES = frozenset({"LIVE", "RESTING", "OPEN", "ACCEPTED"})

# T4 (docs/rebuild/, 2026-07-11 lifecycle-scar excision plan):
# pending-entry uncertainty resolves through chain/venue truth, never through a
# lifecycle scar. The order_status strings each _hold_pending_* helper sets
# (below) are telemetry breadcrumbs only — the position stays
# "pending_tracked" and a ReviewWorkItem (see _open_review_work_item) carries
# the real reason code, exposure bound, and retry schedule.

# Void pending entries after this many cycles without resolution
MAX_PENDING_CYCLES_WITHOUT_ORDER_ID = 2

# BLOCKER-3 freshness bound for a ChainObservationEnvelope used in the
# ambiguous-timeout void gate: two chain_mirror_reconciler cycles (~10 min
# cadence each — src.state.chain_mirror_reconciler's own two-consecutive-run
# threshold) rather than a single read, so a single stale/lagging snapshot can
# never itself justify a void.
_CHAIN_OBSERVATION_FRESHNESS_MAX_AGE_SECONDS = 1200


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
    # Older runtime deps exported MATCHED/FILLED/MINED as fill statuses. Fill
    # success is intentionally not dependency-extensible: only CONFIRMED may
    # promote full fill authority.
    return FILL_STATUSES


def _cancel_statuses(deps=None):
    return getattr(deps, "PENDING_CANCEL_STATUSES", CANCEL_STATUSES)


def _optimistic_fill_statuses(deps=None):
    statuses = frozenset(getattr(deps, "PENDING_OPTIMISTIC_FILL_STATUSES", OPTIMISTIC_FILL_STATUSES))
    return statuses | OPTIMISTIC_FILL_STATUSES


def _partial_fill_statuses(deps=None):
    statuses = frozenset(getattr(deps, "PENDING_PARTIAL_FILL_STATUSES", PARTIAL_FILL_STATUSES))
    return statuses | PARTIAL_FILL_STATUSES


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
                fill_price=(
                    getattr(pos, "entry_price_avg_fill", None)
                    if getattr(pos, "has_fill_economics_authority", False)
                    else getattr(pos, "entry_price", None)
                ),
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
    explicit trade identity and explicit fill economics.
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
        explicit_trade_status = _first_text(
            payload_dict,
            "trade_status",
            "tradeStatus",
            "trade_state",
            "tradeState",
        )
        trade_state = _trade_fact_state_for_status(status, payload_dict)
        unsupported_explicit_trade_status = bool(explicit_trade_status and trade_state is None)
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
                confirmed_without_trade = str(status or "").upper() == "CONFIRMED"
                event_type = (
                    "REVIEW_REQUIRED"
                    if confirmed_without_trade or unsupported_explicit_trade_status
                    else "PARTIAL_FILL_OBSERVED"
                )
                reason_payload = {}
                if confirmed_without_trade:
                    reason_payload = {
                        "reason": "poll_confirmed_requires_trade_fact",
                        "semantic_guard": (
                            "order_status_confirmed_is_not_fill_economics_authority"
                        ),
                    }
                elif unsupported_explicit_trade_status:
                    reason_payload = {
                        "reason": "poll_unknown_trade_status",
                        "incoming_trade_status": explicit_trade_status,
                        "semantic_guard": "unknown_trade_lifecycle_is_not_fill_progress_authority",
                    }
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
                            **reason_payload,
                        },
                    )
                except ValueError:
                    pass
                if unsupported_explicit_trade_status:
                    # T4: was silent at the Python level — only the DB event
                    # row above recorded this. A venue status Zeus does not
                    # recognize is real review debt, not a fill.
                    logger.error(
                        "Venue fill semantic conflict for order=%s reason=poll_unknown_trade_status "
                        "incoming_trade_status=%s (no trade identity)",
                        order_id,
                        explicit_trade_status,
                    )
                    conn.commit()
                    return False

        trade_fact_id = None
        if trade_id and unsupported_explicit_trade_status:
            try:
                append_event(
                    conn,
                    command_id=str(command["command_id"]),
                    event_type="REVIEW_REQUIRED",
                    occurred_at=observed_at.isoformat(),
                    payload={
                        "source": "REST",
                        "venue_order_id": order_id,
                        "trade_id": trade_id,
                        "status": status,
                        "reason": "poll_unknown_trade_status",
                        "incoming_trade_status": explicit_trade_status,
                        "semantic_guard": "unknown_trade_lifecycle_is_not_fill_progress_authority",
                    },
                )
            except ValueError:
                pass
            logger.error(
                "Venue fill semantic conflict for order=%s trade_id=%s reason=poll_unknown_trade_status "
                "incoming_trade_status=%s",
                order_id,
                trade_id,
                explicit_trade_status,
            )
            conn.commit()
            return False
        trade_state_requires_fill_economics = trade_state in TRADE_FILL_ECONOMICS_STATUSES
        has_explicit_fill_economics = _has_explicit_fill_economics(
            shares=shares,
            fill_price=fill_price,
        )
        if trade_id and trade_state and (
            has_explicit_fill_economics or not trade_state_requires_fill_economics
        ):
            filled_size = _decimal_str(shares, "0")
            fill_price_s = _decimal_str(fill_price, "0")
            latest_fact = _latest_trade_fact_for_trade_id(conn, trade_id)
            if latest_fact is not None:
                identity_mismatch = []
                if str(latest_fact.get("command_id") or "") != str(command.get("command_id") or ""):
                    identity_mismatch.append("command_id")
                if str(latest_fact.get("venue_order_id") or "") != str(order_id):
                    identity_mismatch.append("venue_order_id")
                if identity_mismatch:
                    _append_trade_lifecycle_review_required(
                        conn,
                        append_event=append_event,
                        command=command,
                        order_id=order_id,
                        trade_id=trade_id,
                        trade_state=trade_state,
                        observed_at=observed_at,
                        latest_fact=latest_fact,
                        reason="poll_trade_identity_conflict",
                        payload_extra={"mismatch": identity_mismatch},
                    )
                    conn.commit()
                    return False
                if not trade_state_requires_fill_economics:
                    if str(latest_fact.get("state") or "") == trade_state:
                        _append_trade_lifecycle_review_required(
                            conn,
                            append_event=append_event,
                            command=command,
                            order_id=order_id,
                            trade_id=trade_id,
                            trade_state=trade_state,
                            observed_at=observed_at,
                            latest_fact=latest_fact,
                            reason="poll_trade_status_not_fill_authority",
                        )
                        conn.commit()
                        return False
                    if not _trade_lifecycle_transition_allowed(
                        str(latest_fact.get("state") or ""),
                        trade_state,
                    ):
                        _append_trade_lifecycle_review_required(
                            conn,
                            append_event=append_event,
                            command=command,
                            order_id=order_id,
                            trade_id=trade_id,
                            trade_state=trade_state,
                            observed_at=observed_at,
                            latest_fact=latest_fact,
                            reason="poll_trade_lifecycle_regression_or_economic_drift",
                            payload_extra={
                                "incoming_filled_size": filled_size,
                                "incoming_fill_price": fill_price_s,
                            },
                        )
                        conn.commit()
                        return False
                same_fill_economics = _same_trade_fill_economics(
                    latest_fact,
                    filled_size=filled_size,
                    fill_price=fill_price_s,
                )
                if (
                    same_fill_economics
                    and trade_state in {"FAILED", "RETRYING"}
                    and str(latest_fact.get("state") or "") == trade_state
                ):
                    _append_trade_lifecycle_review_required(
                        conn,
                        append_event=append_event,
                        command=command,
                        order_id=order_id,
                        trade_id=trade_id,
                        trade_state=trade_state,
                        observed_at=observed_at,
                        latest_fact=latest_fact,
                        reason="poll_trade_status_not_fill_authority",
                    )
                    conn.commit()
                    return False
                if same_fill_economics and str(latest_fact.get("state") or "") == trade_state:
                    conn.commit()
                    return True
                if trade_state_requires_fill_economics and not same_fill_economics:
                    _append_trade_lifecycle_review_required(
                        conn,
                        append_event=append_event,
                        command=command,
                        order_id=order_id,
                        trade_id=trade_id,
                        trade_state=trade_state,
                        observed_at=observed_at,
                        latest_fact=latest_fact,
                        reason="poll_trade_lifecycle_regression_or_economic_drift",
                        payload_extra={
                            "incoming_filled_size": filled_size,
                            "incoming_fill_price": fill_price_s,
                        },
                    )
                    conn.commit()
                    return False
                if not _trade_lifecycle_transition_allowed(str(latest_fact.get("state") or ""), trade_state):
                    _append_trade_lifecycle_review_required(
                        conn,
                        append_event=append_event,
                        command=command,
                        order_id=order_id,
                        trade_id=trade_id,
                        trade_state=trade_state,
                        observed_at=observed_at,
                        latest_fact=latest_fact,
                        reason="poll_trade_lifecycle_regression_or_economic_drift",
                        payload_extra={
                            "incoming_filled_size": filled_size,
                            "incoming_fill_price": fill_price_s,
                        },
                    )
                    conn.commit()
                    return False
            trade_fact_id = append_trade_fact(
                conn,
                trade_id=trade_id,
                venue_order_id=order_id,
                command_id=str(command["command_id"]),
                state=trade_state,
                filled_size=filled_size,
                fill_price=fill_price_s,
                source="REST",
                observed_at=observed_at,
                venue_timestamp=_payload_timestamp(payload_dict),
                raw_payload_hash=payload_hash,
                raw_payload_json=payload_dict,
                tx_hash=_first_text(payload_dict, "transaction_hash", "tx_hash"),
                block_number=_extract_int(payload_dict, "block_number", "blockNumber"),
                confirmation_count=_extract_int(payload_dict, "confirmation_count", "confirmationCount"),
            )
            if trade_state in {"FAILED", "RETRYING"}:
                try:
                    append_event(
                        conn,
                        command_id=str(command["command_id"]),
                        event_type="REVIEW_REQUIRED",
                        occurred_at=observed_at.isoformat(),
                        payload={
                            "source": "REST",
                            "venue_order_id": order_id,
                            "trade_id": trade_id,
                            "trade_fact_id": trade_fact_id,
                            "status": trade_state,
                            "reason": "poll_trade_status_not_fill_authority",
                            "semantic_guard": "failed_or_retrying_trade_is_not_fill_progress_authority",
                        },
                    )
                except ValueError:
                    pass
                conn.commit()
                return False
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
                    shares=filled_size,
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


def _latest_order_fact_state_for_command(conn: Any, command_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT state
          FROM venue_order_facts
         WHERE command_id = ?
         ORDER BY local_sequence DESC, fact_id DESC
         LIMIT 1
        """,
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        return str(row["state"] or "").upper()
    except (KeyError, TypeError, IndexError):
        try:
            return str(row[0] or "").upper()
        except (TypeError, IndexError):
            return None


def _maybe_append_resting_order_fact(
    pos: Position,
    payload: Any,
    *,
    observed_at: datetime,
    deps=None,
) -> None:
    """Record a LIVE venue_order_fact for a resting post_only/GTC maker order.

    A resting maker order that polls as LIVE/OPEN/RESTING never reaches the
    fill/partial/cancel branches that write order facts, so the command_id join
    used to be NO_FACT for every post_only GTC order (the maker fill-rate loop
    was blind to resting/partial/cancel lifecycle). This writes the LIVE order
    fact so the resting order is linked by command_id; subsequent fill / partial
    / cancel facts then chain off it.

    Idempotent: skipped when the latest fact for this command is already an open
    state (LIVE / RESTING / PARTIALLY_MATCHED) so a multi-cycle rest does not
    append a LIVE row every poll. Never raises into the poll loop (resting-fact
    tracking is observational and must not abort fill verification).
    """

    if deps is None or not hasattr(deps, "get_connection"):
        return
    order_id = str(getattr(pos, "entry_order_id", "") or getattr(pos, "order_id", "") or "").strip()
    if not order_id:
        return

    conn = None
    try:
        from src.state.venue_command_repo import append_order_fact

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
            return

        command = dict(row)
        command_id = str(command.get("command_id") or "")
        if not command_id:
            return
        latest_state = _latest_order_fact_state_for_command(conn, command_id)
        if latest_state in {"LIVE", "RESTING", "PARTIALLY_MATCHED"}:
            return  # already recorded as open; do not duplicate each poll.

        payload_dict = payload if isinstance(payload, dict) else {"raw": payload}
        append_order_fact(
            conn,
            venue_order_id=order_id,
            command_id=command_id,
            state="LIVE",
            remaining_size=_remaining_size(command, None),
            matched_size=None,
            source="REST",
            observed_at=observed_at,
            venue_timestamp=_payload_timestamp(payload_dict),
            raw_payload_hash=_payload_hash(payload_dict),
            raw_payload_json=payload_dict,
        )
        conn.commit()
    except Exception as exc:
        logger.warning("Resting order-fact append failed for %s: %s", pos.trade_id, exc)
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
    pos.entry_economics_authority = (
        ENTRY_ECONOMICS_OPTIMISTIC_MATCH_PRICE
        if fill_authority == FILL_AUTHORITY_OPTIMISTIC_SUBMITTED
        else ENTRY_ECONOMICS_AVG_FILL_PRICE
    )
    pos.fill_authority = fill_authority
    _refresh_corrected_economics_eligibility(pos)


def _has_explicit_fill_economics(
    *,
    shares: float | None,
    fill_price: float | None,
) -> bool:
    return _positive_finite(shares) and _positive_finite(fill_price)


def _latest_trade_fact_for_trade_id(conn, trade_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT *
          FROM venue_trade_facts
         WHERE trade_id = ?
         ORDER BY local_sequence DESC, trade_fact_id DESC
         LIMIT 1
        """,
        (trade_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _same_decimal_value(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _same_trade_fill_economics(fact: dict, *, filled_size: str, fill_price: str) -> bool:
    return (
        _same_decimal_value(fact.get("filled_size"), filled_size)
        and _same_decimal_value(fact.get("fill_price"), fill_price)
    )


def _trade_lifecycle_transition_allowed(previous: str, current: str) -> bool:
    if previous == current:
        return False
    allowed = {
        "RETRYING": {"MATCHED", "MINED", "CONFIRMED", "FAILED"},
        "MATCHED": {"MINED", "CONFIRMED", "FAILED"},
        "MINED": {"CONFIRMED", "FAILED"},
        "CONFIRMED": set(),
        "FAILED": set(),
    }
    return current in allowed.get(previous, set())


def _append_trade_lifecycle_review_required(
    conn,
    *,
    append_event,
    command: dict,
    order_id: str,
    trade_id: str,
    trade_state: str,
    observed_at: datetime,
    latest_fact: dict,
    reason: str,
    payload_extra: dict | None = None,
) -> None:
    # T4: this semantic-conflict early-return used to be silent at the Python
    # level (only a DB event row recorded it). A local bug must not relabel
    # venue truth, but it must also never be invisible — loud ERROR here
    # regardless of whether the append_event write below succeeds.
    logger.error(
        "Venue fill semantic conflict for order=%s trade_id=%s reason=%s "
        "existing_state=%s incoming_state=%s",
        order_id,
        trade_id,
        reason,
        latest_fact.get("state"),
        trade_state,
    )
    try:
        append_event(
            conn,
            command_id=str(command["command_id"]),
            event_type="REVIEW_REQUIRED",
            occurred_at=observed_at.isoformat(),
            payload={
                "source": "REST",
                "venue_order_id": order_id,
                "trade_id": trade_id,
                "status": trade_state,
                "reason": reason,
                "existing_trade_fact_id": latest_fact.get("trade_fact_id"),
                "existing_state": latest_fact.get("state"),
                "existing_filled_size": latest_fact.get("filled_size"),
                "existing_fill_price": latest_fact.get("fill_price"),
                "semantic_guard": "trade_lifecycle_must_preserve_identity_and_fill_economics",
                **(payload_extra or {}),
            },
        )
    except ValueError:
        pass


def _extract_explicit_fill_price(payload) -> Optional[float]:
    """Extract venue fill price fields only.

    Generic order ``price`` is intentionally excluded: on order-status polling
    it can denote submitted limit price, not realized fill price.
    """
    return _extract_float(payload, "avgPrice", "avg_price", "fillPrice", "fill_price")


def _work_item_conn(deps):
    if deps is None or not hasattr(deps, "get_connection"):
        return None
    return deps.get_connection()


def _position_family_key(pos: Position):
    """Best-effort FamilyKey straight off the Position's own identity fields —
    a pending entry always knows its own city/target_date/temperature_metric
    (Blueprint v2 §2 entry context), so this never needs the ChainOnlyFact-style
    market_events lookup src.state.review_work_items.family_key_for_condition_or_token
    performs for facts with no local intent.
    """
    from src.contracts.review_work_item import FamilyKey

    city = str(getattr(pos, "city", "") or "")
    target_date = str(getattr(pos, "target_date", "") or "")
    metric = str(getattr(pos, "temperature_metric", "") or "")
    if not (city and target_date and metric):
        return None
    return FamilyKey(city=city, target_date=target_date, temperature_metric=metric)


def _open_review_work_item(
    pos: Position,
    deps,
    *,
    reason_code: ReviewReasonCode,
    detail: str,
    last_error_class: str = "",
    exposure_bound_usd: float | None = None,
    unbounded: bool = False,
) -> None:
    """Idempotently open a ReviewWorkItem for this pending entry (T4).

    Never raises into the poll loop: work-item bookkeeping failing must not
    itself become a new failure mode — a missed open here just means the next
    cycle's identical call tries again (open_work_item is itself idempotent).
    """
    conn = None
    try:
        conn = _work_item_conn(deps)
        if conn is None:
            return
        from src.state.review_work_items import open_work_item
        from src.state.schema.review_work_items_schema import ensure_table

        ensure_table(conn)
        bound = exposure_bound_usd
        is_unbounded = bool(unbounded)
        if bound is None and not is_unbounded:
            # BLOCKER-1 worst-case bound: shares x $1/share (long-only CTF
            # payout bound). Unknown shares -> genuinely unbounded, never
            # silently treated as zero exposure.
            shares = float(getattr(pos, "shares_submitted", 0.0) or getattr(pos, "shares", 0.0) or 0.0)
            if shares > 0:
                bound = shares
            else:
                is_unbounded = True
        open_work_item(
            conn,
            owner_domain="trade",
            owner_table="position_current",
            subject_id=str(pos.trade_id),
            reason_code=reason_code,
            evidence_refs=(detail,),
            family_key=_position_family_key(pos),
            exposure_bound_usd=bound,
            unbounded=is_unbounded,
            last_error_class=last_error_class,
            last_error_detail=detail,
        )
        conn.commit()
    except Exception as exc:
        logger.error(
            "Review work item open failed for %s (%s): %s",
            getattr(pos, "trade_id", ""),
            reason_code,
            exc,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _resolve_review_work_items_for_position(pos: Position, deps, *, resolution_evidence: str) -> None:
    """Resolve every OPEN ReviewWorkItem for this position's subject identity —
    T4 item 5: once truth lands (a confirmed fill, or a confirmed void), every
    open review debt this pending entry accumulated (missing economics,
    missing authority, local write failures, unconfirmed-timeout absence) is
    moot. Never raises into the poll loop.
    """
    conn = None
    try:
        conn = _work_item_conn(deps)
        if conn is None:
            return
        from src.state.review_work_items import resolve_all_open_for_subject
        from src.state.schema.review_work_items_schema import ensure_table

        ensure_table(conn)
        resolve_all_open_for_subject(
            conn,
            owner_table="position_current",
            subject_id=str(pos.trade_id),
            resolver_identity="fill_tracker",
            resolution_evidence=resolution_evidence,
        )
        conn.commit()
    except Exception as exc:
        logger.error(
            "Review work item resolve failed for %s: %s",
            getattr(pos, "trade_id", ""),
            exc,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _release_entry_risk_reservation(pos: Position, deps) -> None:
    """Resolve the T2 EntryRiskReservation (EntryExposureObligation) opened at
    command-admission time (src.execution.executor._open_entry_risk_reservation),
    now that this command's true fate is settled truth (fill confirmed, or
    confirmed void). Calls the same underlying
    src.state.entry_exposure_obligation.resolve_entry_exposure_obligation
    primitive executor.py's own release helper wraps — fill_tracker does not
    import executor.py (a K3-scale module) just for this one call; both
    call sites share the identical writer, so there is exactly one resolution
    behavior regardless of which module observes the command's fate first.
    Safe to call on a command with no obligation (no-op, never raises).
    """
    conn = None
    try:
        conn = _work_item_conn(deps)
        if conn is None:
            return
        order_id = str(getattr(pos, "entry_order_id", "") or getattr(pos, "order_id", "") or "").strip()
        if not order_id:
            return
        row = conn.execute(
            "SELECT command_id FROM venue_commands WHERE venue_order_id = ? "
            "ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        if row is None:
            return
        command_id = str(row[0])
        if not command_id:
            return
        from src.state.entry_exposure_obligation import resolve_entry_exposure_obligation
        from src.state.schema.entry_exposure_obligations_schema import ensure_table as _ensure_obligations_table

        _ensure_obligations_table(conn)
        resolve_entry_exposure_obligation(conn, command_id=command_id)
        conn.commit()
    except Exception as exc:
        logger.error(
            "Entry risk reservation release failed for %s: %s",
            getattr(pos, "trade_id", ""),
            exc,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _chain_observation_for_position(pos: Position) -> ChainObservationEnvelope:
    """Best-effort ChainObservationEnvelope from fields
    src.state.chain_reconciliation already maintains on Position (T4/BLOCKER-3
    — see src.contracts.chain_observation_envelope module docstring for the
    upgrade path). ``last_chain_absence_observed_at`` is the only positive
    absence signal available synchronously here: fill_tracker has no live
    chain client of its own and must not add one merely to arbitrate a
    poll-loop timeout — that arbitration belongs to
    src.state.chain_mirror_reconciler's own two-consecutive-run protocol.

    BLOCKER-3: "a positive chain observation always overrides local absence
    evidence" — ``chain_verified_at`` (positive presence) at or after the
    absence timestamp means the chain currently shows the token IS held, so no
    qualifying absence envelope is ever built in that case.
    """
    absence_at = str(getattr(pos, "last_chain_absence_observed_at", "") or "")
    if not absence_at:
        return UNOBSERVED_CHAIN_ENVELOPE
    presence_at = str(getattr(pos, "chain_verified_at", "") or "")
    if presence_at and presence_at >= absence_at:
        return UNOBSERVED_CHAIN_ENVELOPE
    posted_at = str(getattr(pos, "order_posted_at", "") or "")
    post_command = bool(posted_at) and absence_at >= posted_at
    return ChainObservationEnvelope(
        account_scope="wallet:zeus_operator",
        fetched_at=absence_at,
        # chain_reconciliation's absence detection reads the wallet's full,
        # unfiltered get_positions_from_api() snapshot — the only "positive
        # evidence of a full read" this call site can offer today.
        complete=True,
        post_command_watermark=post_command,
        source="chain_reconciliation",
    )


def _chain_observation_is_fresh(envelope: ChainObservationEnvelope, now: datetime) -> bool:
    fetched = _parse_iso(envelope.fetched_at)
    if fetched is None:
        return False
    return (now - fetched).total_seconds() <= _CHAIN_OBSERVATION_FRESHNESS_MAX_AGE_SECONDS


def _confirmed_absent_or_defer(pos: Position, now: datetime) -> bool:
    """BLOCKER-3 gate for the ambiguous (no definitive CLOB status) timeout
    branch: True iff the position's own chain observation qualifies to
    support a force-void decision right now. False means the caller must
    defer (stay pending, open/refresh a review work item) — the
    chain-mirror reconciler's own two-consecutive-absence protocol remains
    the authority that eventually force-closes a genuinely absent position,
    independent of this poll loop's cadence.
    """
    envelope = _chain_observation_for_position(pos)
    if not envelope.qualifies_for_absence_vote():
        return False
    return _chain_observation_is_fresh(envelope, now)


def _hold_pending_missing_fill_economics(
    pos: Position,
    *,
    status: str,
    missing: tuple[str, ...],
    deps=None,
) -> tuple[str, bool, bool]:
    order_status = f"{str(status or 'fill').lower()}_missing_fill_economics"
    pos.entry_fill_verified = False
    pos.fill_authority = FILL_AUTHORITY_NONE
    _refresh_corrected_economics_eligibility(pos)
    pos.order_status = order_status
    logger.error(
        "Fill economics missing for %s status=%s missing=%s; venue truth gap — "
        "holding pending entry for review, no lifecycle scar",
        getattr(pos, "trade_id", ""),
        status,
        ",".join(missing),
    )
    _open_review_work_item(
        pos,
        deps,
        reason_code=ReviewReasonCode.MISSING_FILL_ECONOMICS,
        detail=f"{order_status}:{','.join(missing)}",
        last_error_class="MissingFillEconomics",
    )
    return "still_pending", True, False


def _hold_pending_missing_fill_authority(
    pos: Position,
    *,
    status: str,
    missing: tuple[str, ...],
    deps=None,
) -> tuple[str, bool, bool]:
    reason = "_".join(missing) if missing else "authority"
    order_status = f"{str(status or 'fill').lower()}_missing_{reason}"
    pos.entry_fill_verified = False
    pos.fill_authority = FILL_AUTHORITY_NONE
    _refresh_corrected_economics_eligibility(pos)
    pos.order_status = order_status
    logger.error(
        "Fill authority missing for %s status=%s missing=%s; venue truth gap — "
        "holding pending entry for review, no lifecycle scar",
        getattr(pos, "trade_id", ""),
        status,
        ",".join(missing),
    )
    _open_review_work_item(
        pos,
        deps,
        reason_code=ReviewReasonCode.MISSING_FILL_AUTHORITY,
        detail=f"{order_status}:{','.join(missing)}",
        last_error_class="MissingFillAuthority",
    )
    return "still_pending", True, False


def _hold_pending_timeout_absence_unconfirmed(
    pos: Position,
    *,
    deps=None,
) -> tuple[str, bool, bool]:
    """T4/BLOCKER-3: an ambiguous timeout (no definitive CLOB status, cancel
    attempt itself unconfirmed) whose chain observation does not qualify for
    a confirmed-absence vote. Stays pending_tracked; the chain-mirror
    reconciler's own two-consecutive-absence protocol remains the authority
    that eventually force-closes a genuinely absent position.
    """
    order_status = "timeout_awaiting_chain_confirmation"
    pos.order_status = order_status
    logger.warning(
        "Pending entry timeout for %s has no venue confirmation and no "
        "qualifying chain absence observation; holding pending, not voiding",
        getattr(pos, "trade_id", ""),
    )
    _open_review_work_item(
        pos,
        deps,
        reason_code=ReviewReasonCode.TIMEOUT_ABSENCE_UNCONFIRMED,
        detail=order_status,
        last_error_class="AmbiguousTimeout",
    )
    return "still_pending", True, False


def _hold_pending_local_write_failure(
    pos: Position,
    *,
    order_status: str,
    detail: str,
    deps=None,
) -> tuple[str, bool, bool]:
    """T4 sites 988/1050/1114/1165/1240/1287: a LOCAL ledger/canonical/void
    write failed. Venue/chain truth is NOT in question — a local bug must
    never relabel venue truth, so this stays pending_tracked (in the
    check_pending_entries scan set) and retries next cycle.
    """
    pos.order_status = order_status
    logger.error(
        "Local write failed for %s (%s); a local bug must not relabel venue "
        "truth — holding pending entry for retry, no lifecycle scar",
        getattr(pos, "trade_id", ""),
        detail,
    )
    _open_review_work_item(
        pos,
        deps,
        reason_code=ReviewReasonCode.LOCAL_WRITE_FAILURE,
        detail=detail,
        last_error_class="LocalWriteFailure",
    )
    return "still_pending", True, False


def _missing_fill_economics(
    *,
    fill_price: float | None,
    shares: float | None,
) -> tuple[str, ...]:
    missing: list[str] = []
    if not _positive_finite(fill_price):
        missing.append("fill_price")
    if not _positive_finite(shares):
        missing.append("filled_size")
    return tuple(missing)


def _positive_finite(value: float | None) -> bool:
    try:
        numeric = float(value or 0.0)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric > 0.0


def _non_fill_progress_trade_state(payload: Any, status: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    trade_state = _trade_fact_state_for_status(status, payload)
    if trade_state and trade_state not in TRADE_FILL_ECONOMICS_STATUSES:
        return trade_state
    return None


def _record_non_fill_progress_trade_if_present(
    pos: Position,
    payload: Any,
    now: datetime,
    *,
    status: str,
    deps=None,
    order_status_on_failure: str,
) -> tuple[str, bool, bool] | None:
    if _non_fill_progress_trade_state(payload, status) is None:
        return None
    ledger_ok = _maybe_append_venue_fill_observation(
        pos,
        payload,
        status=status,
        shares=_extract_filled_shares(payload, allow_order_size_fallback=False),
        fill_price=_extract_explicit_fill_price(payload),
        observed_at=now,
        deps=deps,
    )
    if not ledger_ok:
        return _hold_pending_local_write_failure(
            pos,
            order_status=order_status_on_failure,
            detail=order_status_on_failure,
            deps=deps,
        )
    return None


def _refresh_corrected_economics_eligibility(pos: Position) -> None:
    pos.corrected_executable_economics_eligible = (
        pos.has_fill_economics_authority
        and pos.pricing_semantics_id == CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION
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
    submitted_price = (
        getattr(pos, "entry_price_submitted", 0.0)
        or getattr(pos, "entry_price", 0.0)
    )
    fill_price = _extract_explicit_fill_price(payload)
    shares = _extract_filled_shares(payload, allow_order_size_fallback=False)
    trade_id = _extract_trade_id(payload if isinstance(payload, dict) else {})
    observed_status = str(order_status or execution_status or "filled").upper()
    non_fill_progress = _record_non_fill_progress_trade_if_present(
        pos,
        payload,
        now,
        status=observed_status,
        deps=deps,
        order_status_on_failure="fill_ledger_write_failed",
    )
    if non_fill_progress is not None:
        return non_fill_progress
    missing = _missing_fill_economics(fill_price=fill_price, shares=shares)
    if missing:
        return _hold_pending_missing_fill_economics(
            pos,
            status=observed_status,
            missing=missing,
            deps=deps,
        )

    ledger_ok = _maybe_append_venue_fill_observation(
        pos,
        payload,
        status=observed_status,
        shares=shares,
        fill_price=fill_price,
        observed_at=now,
        deps=deps,
    )
    if not ledger_ok:
        return _hold_pending_local_write_failure(
            pos,
            order_status="fill_ledger_write_failed",
            detail="fill_ledger_write_failed",
            deps=deps,
        )
    if not trade_id:
        return _hold_pending_missing_fill_authority(
            pos,
            status=observed_status,
            missing=("trade_identity",),
            deps=deps,
        )

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
    # M2a (timing-semantics fix 2026-06-16; reconcile-safe 2026-06-16): entered_at
    # feeds hours_since_open -> compute_alpha -> live exits, so grading it against
    # the bare processing clock when the venue actually reported a match time is
    # the fabrication this fix removes. Basis precedence:
    #   (1) venue match time from the fill payload — REAL_SOURCE (live-WS path)
    #   (2) an entry time the caller already set    — preserve, do not clobber
    #   (3) the caller's observation `now`          — DERIVED_JUSTIFIED upper
    #       bound: the fill is CONFIRMED to have occurred by the time we observe
    #       it, so `now` never OVER-states hold age (conservative for exits).
    # (3) is the honest reconcile-confirmed entry instant: a reconciled fill
    # carries no WS match time, and the canonical lifecycle builder requires a
    # non-empty timestamp, so the prior honest-absent "" terminal was invalid
    # (it held every reconcile entry pending forever with no fill_authority).
    # Never the bare clock over a real venue time.
    _venue_match_time = _payload_timestamp(payload if isinstance(payload, dict) else {})
    pos.entered_at = _venue_match_time or pos.entered_at or now.isoformat()

    lc_ok = _maybe_update_trade_lifecycle(pos, deps=deps)
    cf_ok = _maybe_emit_canonical_entry_fill(pos, deps=deps)
    if not lc_ok or not cf_ok:
        return _hold_pending_local_write_failure(
            pos,
            order_status="fill_canonical_write_failed",
            detail="fill_canonical_write_failed",
            deps=deps,
        )

    _maybe_log_execution_fill(
        pos,
        submitted_price=submitted_price,
        shares=shares,
        execution_status=execution_status,
        deps=deps,
    )
    # T4 item 5: fill economics are now confirmed applied — release the T2
    # EntryRiskReservation and resolve any open review debt for this subject
    # (both no-ops when none exist; never raise into the poll loop).
    _release_entry_risk_reservation(pos, deps)
    _resolve_review_work_items_for_position(
        pos, deps, resolution_evidence="fill_confirmed"
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
) -> tuple[str, bool, bool, bool]:
    """Returns (outcome, dirty, tracker_dirty, held_for_review).

    ``held_for_review`` (T4) is True iff this call short-circuited into a
    hold-pending-review outcome (venue truth gap or local write failure) —
    the caller (_check_entry_fill) must NOT then also evaluate this cycle's
    timeout/cancel logic against fill state this call could not durably
    establish (that would let a local write failure silently relabel venue
    truth as "no exposure, safe to void" — exactly the disease T4 removes).
    """
    fill_price = _extract_explicit_fill_price(payload)
    shares = _extract_filled_shares(payload, allow_order_size_fallback=False)
    non_fill_progress = _record_non_fill_progress_trade_if_present(
        pos,
        payload,
        now,
        status="PARTIALLY_MATCHED",
        deps=deps,
        order_status_on_failure="partial_fill_ledger_write_failed",
    )
    if non_fill_progress is not None:
        return (*non_fill_progress, True)
    if shares is None or shares <= 0:
        return (*_update_pending_status(pos, "partial"), False)
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
        return (
            *_hold_pending_local_write_failure(
                pos,
                order_status="partial_fill_ledger_write_failed",
                detail="partial_fill_ledger_write_failed",
                deps=deps,
            ),
            True,
        )

    missing = _missing_fill_economics(fill_price=fill_price, shares=shares)
    if missing:
        return (
            *_hold_pending_missing_fill_economics(
                pos,
                status="PARTIALLY_MATCHED",
                missing=missing,
                deps=deps,
            ),
            True,
        )

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
    return "still_pending", True, False, False


def _record_optimistic_entry_observed(
    pos: Position,
    payload,
    now: datetime,
    *,
    status: str,
    deps=None,
) -> tuple[str, bool, bool]:
    fill_price = _extract_explicit_fill_price(payload)
    shares = _extract_filled_shares(payload, allow_order_size_fallback=False)
    non_fill_progress = _record_non_fill_progress_trade_if_present(
        pos,
        payload,
        now,
        status=status,
        deps=deps,
        order_status_on_failure="optimistic_fill_ledger_write_failed",
    )
    if non_fill_progress is not None:
        return non_fill_progress
    if shares is None or shares <= 0:
        return _update_pending_status(pos, status.lower())
    missing = _missing_fill_economics(fill_price=fill_price, shares=shares)
    if missing:
        return _hold_pending_missing_fill_economics(
            pos,
            status=status,
            missing=missing,
            deps=deps,
        )

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
        return _hold_pending_local_write_failure(
            pos,
            order_status="optimistic_fill_ledger_write_failed",
            detail="optimistic_fill_ledger_write_failed",
            deps=deps,
        )

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
        # T4 site 1287: the void write itself failed locally. Venue/chain
        # truth about this order's fate is not in question — a local bug must
        # not relabel it. Stay pending; the void re-derives from the same
        # durable inputs next cycle.
        return _hold_pending_local_write_failure(
            target,
            order_status="void_canonical_write_failed",
            detail="void_canonical_write_failed",
            deps=deps,
        )

    # T4 item 5 (BLOCKER-1 law): confirmed absence also supersedes the
    # conservative EntryRiskReservation estimate, same as a confirmed fill —
    # release it and resolve any open review debt for this subject.
    _release_entry_risk_reservation(target, deps)
    _resolve_review_work_items_for_position(
        target, deps, resolution_evidence="confirmed_void"
    )
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
        trade_status_conflict = _nonfinal_trade_status_for_confirmed_order(payload)
        if trade_status_conflict:
            if trade_status_conflict in _optimistic_fill_statuses(deps) or trade_status_conflict == "MINED":
                if _extract_filled_shares(payload, allow_order_size_fallback=False) is None:
                    return _update_pending_status(pos, trade_status_conflict.lower())
                return _record_optimistic_entry_observed(
                    pos,
                    payload,
                    now,
                    status=trade_status_conflict,
                    deps=deps,
                )
            non_fill_progress = _record_non_fill_progress_trade_if_present(
                pos,
                payload,
                now,
                status=status,
                deps=deps,
                order_status_on_failure="fill_ledger_write_failed",
            )
            if non_fill_progress is not None:
                return non_fill_progress
            return _hold_pending_missing_fill_authority(
                pos,
                status=f"{status}_TRADE_{trade_status_conflict}",
                missing=("confirmed_trade_status",),
                deps=deps,
            )
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
        if (
            _extract_filled_shares(payload, allow_order_size_fallback=False) is None
            and _non_fill_progress_trade_state(payload, status) is None
        ):
            return _update_pending_status(pos, status.lower())
        return _record_optimistic_entry_observed(
            pos,
            payload,
            now,
            status=status,
            deps=deps,
        )

    if status in _partial_fill_statuses(deps):
        outcome, dirty, tracker_dirty, held_for_review = _record_partial_entry_observed(
            pos, payload, now, deps=deps
        )
        if held_for_review:
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
            outcome, dirty, tracker_dirty, held_for_review = _record_partial_entry_observed(
                pos, payload, now, deps=deps
            )
            if held_for_review:
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
            # Venue-confirmed cancel lane (CLOB itself confirmed the
            # cancellation) — kept as-is, no chain gate needed: this is
            # unambiguous venue truth about the order, not an inference.
            if _position_has_observed_exposure(pos):
                pos.fill_authority = FILL_AUTHORITY_CANCELLED_REMAINDER
                _refresh_corrected_economics_eligibility(pos)
                return _update_pending_status(
                    pos,
                    "partial_remainder_cancelled",
                )
            return _mark_entry_voided(portfolio, pos, "UNFILLED_ORDER", deps=deps)
        # T4/BLOCKER-3: ambiguous branch — CLOB gave no definitive status at
        # all AND the cancel attempt itself did not confirm cancellation.
        # Chain is the arbiter for absence (reconciliation order law), never
        # fill_tracker's own inference from a bare timeout. Void only if this
        # position's own chain observation qualifies as a confirmed-absence
        # vote right now; otherwise defer (stay pending, open/refresh a
        # review work item) and let the chain-mirror reconciler's own
        # two-consecutive-absence protocol eventually force-close a
        # genuinely absent position on its own cadence.
        if _confirmed_absent_or_defer(pos, now):
            return _mark_entry_voided(portfolio, pos, "UNFILLED_ORDER", deps=deps)
        return _hold_pending_timeout_absence_unconfirmed(pos, deps=deps)

    if status:
        if status in RESTING_OPEN_STATUSES:
            # Resting post_only/GTC maker order: record the LIVE order fact so it
            # is linked by command_id (FIX: post_only GTC orders used to yield
            # NO_FACT, blinding the maker fill-rate measurement loop). Idempotent
            # and non-raising; the order stays still_pending.
            _maybe_append_resting_order_fact(pos, payload, observed_at=now, deps=deps)
        return _update_pending_status(pos, status.lower())
    return "still_pending", False, False


def _handle_no_order_id(
    pos: Position,
    portfolio: PortfolioState,
    *,
    now: datetime,
    deps=None,
) -> tuple[str, bool, bool]:
    """Handle pending entries with no order ID at all.

    T4: no order id means Zeus has no way to ask the venue what happened — a
    genuine authority gap, not something to destroy or scar. Stays
    pending_tracked forever (in the check_pending_entries scan
    set) with an open MISSING_FILL_AUTHORITY review work item; it may still
    resolve later via chain reconciliation adopting the position, or an
    operator investigating the review debt directly.
    """
    # Track age via order_posted_at
    if not pos.order_posted_at:
        # First time seeing this — give it one more cycle
        pos.order_posted_at = now.isoformat()
        return "still_pending", True, False

    _open_review_work_item(
        pos,
        deps,
        reason_code=ReviewReasonCode.MISSING_FILL_AUTHORITY,
        detail="no_order_id_after_grace_period",
        last_error_class="NoOrderId",
    )
    return "still_pending", True, False


def _normalize_status(payload) -> str:
    """Normalize CLOB status response to uppercase string."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.upper()
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("state") or payload.get("orderStatus")
        normalized = str(status).upper() if status else ""
        if normalized in {"LIVE", "RESTING", "OPEN", "ACCEPTED"} and _positive_finite(
            _extract_filled_shares(payload, allow_order_size_fallback=False)
        ):
            return "PARTIALLY_MATCHED"
        return normalized
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


def _nonfinal_trade_status_for_confirmed_order(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    trade_status = _first_text(payload, "trade_status", "tradeStatus", "trade_state", "tradeState")
    normalized = str(trade_status or "").upper()
    if not normalized or normalized == "CONFIRMED":
        return None
    return normalized


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
    from src.state.venue_command_repo import resolve_position_lot_id_for_command

    return resolve_position_lot_id_for_command(conn, command)


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
