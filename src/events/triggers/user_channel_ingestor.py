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
    message_hash = _required(message, "message_hash")
    message_kind = str(message.get("message_type") or message.get("type") or "").lower()
    if message_kind == "order":
        return _append_deduped_user_channel_event(
            ledger,
            aggregate_id=aggregate_id,
            venue_order_id=venue_order_id,
            message_type=message_kind,
            message_hash=message_hash,
            occurred_at=occurred_at,
            append_fn=lambda: append_user_order_observed(
                ledger,
                aggregate_id=aggregate_id,
                event_id=event_id,
                final_intent_id=final_intent_id,
                source=source,
                order_update_type=_required(message, "order_update_type"),
                venue_order_id=venue_order_id,
                occurred_at=occurred_at,
                payload={"raw_user_channel_message_hash": message_hash},
            ),
        )
    if message_kind == "trade":
        return _append_deduped_user_channel_event(
            ledger,
            aggregate_id=aggregate_id,
            venue_order_id=venue_order_id,
            message_type=message_kind,
            message_hash=message_hash,
            occurred_at=occurred_at,
            append_fn=lambda: append_user_trade_observed(
                ledger,
                aggregate_id=aggregate_id,
                event_id=event_id,
                final_intent_id=final_intent_id,
                source=source,
                trade_status=_required(message, "trade_status"),
                venue_order_id=venue_order_id,
                occurred_at=occurred_at,
                payload={"raw_user_channel_message_hash": message_hash},
            ),
        )
    raise EdliUserChannelIngestorError(f"unsupported EDLI user-channel message type: {message_kind!r}")


def _append_deduped_user_channel_event(
    ledger: LiveOrderAggregateLedger,
    *,
    aggregate_id: str,
    venue_order_id: str,
    message_type: str,
    message_hash: str,
    occurred_at: datetime,
    append_fn: Any,
) -> LiveOrderAggregateEvent:
    existing = ledger.conn.execute(
        """
        SELECT message_hash, aggregate_id, venue_order_id, message_type
        FROM edli_user_channel_message_dedup
        WHERE message_hash = ?
        """,
        (message_hash,),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["aggregate_id"]) != aggregate_id
            or str(existing["venue_order_id"]) != venue_order_id
            or str(existing["message_type"]) != message_type
        ):
            raise EdliUserChannelIngestorError("EDLI_USER_CHANNEL_MESSAGE_HASH_DRIFT")
        row = ledger.conn.execute(
            """
            SELECT aggregate_event_id
            FROM edli_live_order_events
            WHERE aggregate_id = ?
              AND event_type IN ('UserOrderObserved', 'UserTradeObserved')
              AND json_extract(payload_json, '$.raw_user_channel_message_hash') = ?
            ORDER BY event_sequence ASC
            LIMIT 1
            """,
            (aggregate_id, message_hash),
        ).fetchone()
        if row is None:
            raise EdliUserChannelIngestorError("EDLI_USER_CHANNEL_DEDUP_EVENT_MISSING")
        return ledger.get_event(str(row["aggregate_event_id"]))

    return append_fn()


def _required(message: dict[str, Any], field: str) -> str:
    value = str(message.get(field) or "").strip()
    if not value:
        raise EdliUserChannelIngestorError(f"EDLI user-channel message missing {field}")
    return value
