"""EDLI live-order user-channel and reconcile authority boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.events.live_order_aggregate import LiveOrderAggregateEvent, LiveOrderAggregateLedger


USER_CHANNEL_SOURCE = "polymarket_user_channel"
RECONCILE_SOURCE = "venue_reconcile"
PUBLIC_MARKET_CHANNEL_SOURCES = {"polymarket_market_channel", "market_channel", "public_market_channel"}


class LiveOrderReconcileError(ValueError):
    """Raised when a non-authoritative source attempts to write live-order truth."""


def assert_user_channel_fill_authority(*, source: str) -> None:
    if source in PUBLIC_MARKET_CHANNEL_SOURCES:
        raise LiveOrderReconcileError("public market channel cannot produce user order, trade, or fill truth")
    if source not in {USER_CHANNEL_SOURCE, RECONCILE_SOURCE}:
        raise LiveOrderReconcileError("user channel or explicit reconcile is required for fill truth")


def append_user_order_observed(
    ledger: LiveOrderAggregateLedger,
    *,
    aggregate_id: str,
    event_id: str,
    final_intent_id: str,
    source: str,
    order_update_type: str,
    venue_order_id: str,
    occurred_at: datetime,
    payload: dict[str, Any] | None = None,
) -> LiveOrderAggregateEvent:
    assert_user_channel_fill_authority(source=source)
    if source != USER_CHANNEL_SOURCE:
        raise LiveOrderReconcileError("UserOrderObserved requires polymarket_user_channel source")
    update_type = str(order_update_type or "").upper()
    if update_type not in {"PLACEMENT", "UPDATE", "CANCELLATION"}:
        raise LiveOrderReconcileError(f"unsupported user order update type: {order_update_type!r}")
    return ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="UserOrderObserved",
        payload={
            **dict(payload or {}),
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "source_authority": source,
            "order_update_type": update_type,
            "venue_order_id": venue_order_id,
        },
        occurred_at=occurred_at,
        source_authority="user_channel",
    )


def append_user_trade_observed(
    ledger: LiveOrderAggregateLedger,
    *,
    aggregate_id: str,
    event_id: str,
    final_intent_id: str,
    source: str,
    trade_status: str,
    venue_order_id: str,
    occurred_at: datetime,
    payload: dict[str, Any] | None = None,
) -> LiveOrderAggregateEvent:
    assert_user_channel_fill_authority(source=source)
    if source != USER_CHANNEL_SOURCE:
        raise LiveOrderReconcileError("UserTradeObserved requires polymarket_user_channel source")
    status = str(trade_status or "").upper()
    if status not in {"MATCHED", "MINED", "CONFIRMED", "RETRYING", "FAILED"}:
        raise LiveOrderReconcileError(f"unsupported user trade status: {trade_status!r}")
    return ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="UserTradeObserved",
        payload={
            **dict(payload or {}),
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "source_authority": source,
            "trade_status": status,
            "fill_authority_state": _fill_authority_state(status),
            "venue_order_id": venue_order_id,
        },
        occurred_at=occurred_at,
        source_authority="user_channel",
    )


def append_reconciled(
    ledger: LiveOrderAggregateLedger,
    *,
    aggregate_id: str,
    event_id: str,
    final_intent_id: str,
    source: str,
    pending_reconcile: bool,
    occurred_at: datetime,
    payload: dict[str, Any] | None = None,
) -> LiveOrderAggregateEvent:
    assert_user_channel_fill_authority(source=source)
    if source != RECONCILE_SOURCE:
        raise LiveOrderReconcileError("Reconciled requires venue_reconcile source")
    return ledger.append_event(
        aggregate_id=aggregate_id,
        event_type="Reconciled",
        payload={
            **dict(payload or {}),
            "event_id": event_id,
            "final_intent_id": final_intent_id,
            "source_authority": source,
            "pending_reconcile": bool(pending_reconcile),
        },
        occurred_at=occurred_at,
        source_authority="explicit_reconcile",
    )


def _fill_authority_state(status: str) -> str:
    if status == "CONFIRMED":
        return "FILL_CONFIRMED"
    if status in {"MATCHED", "MINED"}:
        return "MATCHED_PENDING_FINALITY"
    return "RECONCILE_REQUIRED"
