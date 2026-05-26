"""EDLI user-channel event adapter for live-order aggregate facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.events.live_order_aggregate import LiveOrderAggregateEvent, LiveOrderAggregateLedger
from src.events.live_order_reconcile import append_user_order_observed, append_user_trade_observed


class EdliUserChannelIngestorError(ValueError):
    """Raised when an authenticated user-channel message cannot be mapped."""


def append_user_channel_message(
    ledger: LiveOrderAggregateLedger,
    *,
    aggregate_id: str,
    message: dict[str, Any],
    occurred_at: datetime,
) -> LiveOrderAggregateEvent:
    """Append an authenticated user-channel order/trade observation.

    This adapter intentionally accepts only user-channel authority. Public
    market-channel messages are rejected before they can reach fill truth.
    """

    source = str(message.get("source_authority") or message.get("source") or "")
    if source != "polymarket_user_channel":
        raise EdliUserChannelIngestorError("EDLI user-channel ingest requires polymarket_user_channel source")
    event_id = _required(message, "event_id")
    final_intent_id = _required(message, "final_intent_id")
    venue_order_id = _required(message, "venue_order_id")
    message_kind = str(message.get("message_type") or message.get("type") or "").lower()
    if message_kind == "order":
        return append_user_order_observed(
            ledger,
            aggregate_id=aggregate_id,
            event_id=event_id,
            final_intent_id=final_intent_id,
            source=source,
            order_update_type=_required(message, "order_update_type"),
            venue_order_id=venue_order_id,
            occurred_at=occurred_at,
            payload={"raw_user_channel_message_hash": str(message.get("message_hash") or "")},
        )
    if message_kind == "trade":
        return append_user_trade_observed(
            ledger,
            aggregate_id=aggregate_id,
            event_id=event_id,
            final_intent_id=final_intent_id,
            source=source,
            trade_status=_required(message, "trade_status"),
            venue_order_id=venue_order_id,
            occurred_at=occurred_at,
            payload={"raw_user_channel_message_hash": str(message.get("message_hash") or "")},
        )
    raise EdliUserChannelIngestorError(f"unsupported EDLI user-channel message type: {message_kind!r}")


def _required(message: dict[str, Any], field: str) -> str:
    value = str(message.get(field) or "").strip()
    if not value:
        raise EdliUserChannelIngestorError(f"EDLI user-channel message missing {field}")
    return value
