"""EDLI user-channel event adapter for live-order aggregate facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.events.live_order_aggregate import LiveOrderAggregateEvent, LiveOrderAggregateLedger
from src.events.live_order_reconcile import append_user_order_observed, append_user_trade_observed


class EdliUserChannelIngestorError(ValueError):
    """Raised when an authenticated user-channel message cannot be mapped."""


INBOX_PENDING = "PENDING"
INBOX_PROCESSED = "PROCESSED"
INBOX_DUPLICATE = "DUPLICATE"
INBOX_FAILED = "FAILED"
INBOX_STALE_REJECTED = "STALE_REJECTED"


def enqueue_user_channel_inbox_message(
    conn,
    *,
    message: dict[str, Any],
    aggregate_id: str,
    occurred_at: datetime,
    received_at: datetime,
) -> bool:
    """Persist an authenticated user-channel message before aggregate append.

    The inbox is the durable ack boundary for the runtime processor. Returning
    False means the exact message_hash already exists and the caller should let
    the existing row/status drive processing.
    """

    source = str(message.get("source_authority") or message.get("source") or "")
    if source != "polymarket_user_channel":
        raise EdliUserChannelIngestorError("EDLI_USER_CHANNEL_INBOX_SOURCE_INVALID")
    message_type = str(message.get("message_type") or message.get("type") or "").lower()
    if message_type not in {"order", "trade"}:
        raise EdliUserChannelIngestorError("EDLI_USER_CHANNEL_INBOX_MESSAGE_TYPE_INVALID")
    message_hash = _required(message, "message_hash")
    event_id = _required(message, "event_id")
    final_intent_id = _required(message, "final_intent_id")
    venue_order_id = _required(message, "venue_order_id")
    payload_json = _stable_json(message)
    existing = conn.execute(
        """
        SELECT aggregate_id, venue_order_id, message_type, payload_json
        FROM edli_user_channel_inbox
        WHERE message_hash = ?
        """,
        (message_hash,),
    ).fetchone()
    if existing is not None:
        if (
            str(existing["aggregate_id"]) != aggregate_id
            or str(existing["venue_order_id"]) != venue_order_id
            or str(existing["message_type"]) != message_type
            or str(existing["payload_json"]) != payload_json
        ):
            raise EdliUserChannelIngestorError("EDLI_USER_CHANNEL_INBOX_HASH_DRIFT")
        return False
    conn.execute(
        """
        INSERT INTO edli_user_channel_inbox (
            message_hash, source_authority, message_type, aggregate_id, event_id,
            final_intent_id, venue_order_id, payload_json, occurred_at, received_at,
            processing_status, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_hash,
            source,
            message_type,
            aggregate_id,
            event_id,
            final_intent_id,
            venue_order_id,
            payload_json,
            occurred_at.isoformat(),
            received_at.isoformat(),
            INBOX_PENDING,
            1,
        ),
    )
    return True


def pending_user_channel_inbox_messages(conn, *, limit: int) -> list:
    return list(
        conn.execute(
            """
            SELECT *
            FROM edli_user_channel_inbox
            WHERE processing_status = 'PENDING'
            ORDER BY received_at ASC
            LIMIT ?
            """,
            (max(0, limit),),
        ).fetchall()
    )


def mark_user_channel_inbox_status(
    conn,
    *,
    message_hash: str,
    status: str,
    processed_at: datetime,
    error: str | None = None,
) -> None:
    if status not in {INBOX_PROCESSED, INBOX_DUPLICATE, INBOX_FAILED, INBOX_STALE_REJECTED}:
        raise EdliUserChannelIngestorError(f"EDLI_USER_CHANNEL_INBOX_STATUS_INVALID:{status}")
    conn.execute(
        """
        UPDATE edli_user_channel_inbox
        SET processing_status = ?,
            processed_at = ?,
            processing_error = ?
        WHERE message_hash = ?
        """,
        (status, processed_at.isoformat(), error, message_hash),
    )


def inbox_row_to_user_channel_message(row) -> dict[str, Any]:
    import json

    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):
        raise EdliUserChannelIngestorError("EDLI_USER_CHANNEL_INBOX_PAYLOAD_INVALID")
    payload.setdefault("source_authority", str(row["source_authority"]))
    payload.setdefault("message_type", str(row["message_type"]))
    payload.setdefault("event_id", str(row["event_id"]))
    payload.setdefault("final_intent_id", str(row["final_intent_id"]))
    payload.setdefault("venue_order_id", str(row["venue_order_id"]))
    payload.setdefault("message_hash", str(row["message_hash"]))
    return payload


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


def _stable_json(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
