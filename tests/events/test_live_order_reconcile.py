# Created: 2026-05-26
# Authority basis: PR332 user-channel/reconcile authority substrate.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import (
    LiveOrderReconcileError,
    append_reconciled,
    append_user_order_observed,
    append_user_trade_observed,
    assert_user_channel_fill_authority,
)
from src.events.triggers.user_channel_ingestor import (
    EdliUserChannelIngestorError,
    append_user_channel_message,
)


NOW = datetime(2026, 5, 26, 12, tzinfo=timezone.utc)


def test_public_market_channel_cannot_write_user_trade_or_fill_truth():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    with pytest.raises(LiveOrderReconcileError, match="public market channel"):
        assert_user_channel_fill_authority(source="polymarket_market_channel")
    with pytest.raises(LiveOrderReconcileError, match="public market channel"):
        append_user_trade_observed(
            ledger,
            aggregate_id="event-1:intent-1",
            event_id="event-1",
            final_intent_id="intent-1",
            source="polymarket_market_channel",
            trade_status="MATCHED",
            venue_order_id="venue-1",
            occurred_at=NOW,
        )


def test_authenticated_user_channel_order_updates_append_user_order_observed():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    event = append_user_order_observed(
        ledger,
        aggregate_id="event-1:intent-1",
        event_id="event-1",
        final_intent_id="intent-1",
        source="polymarket_user_channel",
        order_update_type="UPDATE",
        venue_order_id="venue-1",
        occurred_at=NOW,
    )

    assert event.event_type == "UserOrderObserved"
    assert event.payload["source_authority"] == "polymarket_user_channel"
    assert ledger.get_projection("event-1:intent-1").current_state == "USER_ORDER_OBSERVED"


def test_user_trade_matched_is_not_final_fill_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    event = append_user_trade_observed(
        ledger,
        aggregate_id="event-1:intent-1",
        event_id="event-1",
        final_intent_id="intent-1",
        source="polymarket_user_channel",
        trade_status="MATCHED",
        venue_order_id="venue-1",
        occurred_at=NOW,
    )

    assert event.payload["fill_authority_state"] == "MATCHED_PENDING_FINALITY"


def test_user_trade_confirmed_is_fill_authority_state():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    event = append_user_trade_observed(
        ledger,
        aggregate_id="event-1:intent-1",
        event_id="event-1",
        final_intent_id="intent-1",
        source="polymarket_user_channel",
        trade_status="CONFIRMED",
        venue_order_id="venue-1",
        occurred_at=NOW,
    )

    assert event.payload["fill_authority_state"] == "FILL_CONFIRMED"


def test_timeout_unknown_reconcile_clears_pending_only_from_explicit_reconcile():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    assert ledger.get_projection("event-1:intent-1").pending_reconcile is True

    with pytest.raises(LiveOrderReconcileError, match="Reconciled requires venue_reconcile"):
        append_reconciled(
            ledger,
            aggregate_id="event-1:intent-1",
            event_id="event-1",
            final_intent_id="intent-1",
            source="polymarket_user_channel",
            pending_reconcile=False,
            occurred_at=NOW,
        )

    append_reconciled(
        ledger,
        aggregate_id="event-1:intent-1",
        event_id="event-1",
        final_intent_id="intent-1",
        source="venue_reconcile",
        pending_reconcile=False,
        occurred_at=NOW,
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "RECONCILED"
    assert projection.pending_reconcile is False


def test_user_channel_ingestor_rejects_public_market_messages():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    with pytest.raises(EdliUserChannelIngestorError, match="polymarket_user_channel"):
        append_user_channel_message(
            ledger,
            aggregate_id="event-1:intent-1",
            message={
                "source": "polymarket_market_channel",
                "type": "trade",
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "venue_order_id": "venue-1",
                "trade_status": "CONFIRMED",
            },
            occurred_at=NOW,
        )


def test_user_channel_ingestor_appends_order_and_confirmed_trade_events():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    order_event = append_user_channel_message(
        ledger,
        aggregate_id="event-1:intent-1",
        message={
            "source": "polymarket_user_channel",
            "type": "order",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "venue-1",
            "order_update_type": "PLACEMENT",
            "message_hash": "order-msg-1",
        },
        occurred_at=NOW,
    )
    trade_event = append_user_channel_message(
        ledger,
        aggregate_id="event-1:intent-1",
        message={
            "source": "polymarket_user_channel",
            "type": "trade",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "venue-1",
            "trade_status": "CONFIRMED",
            "message_hash": "trade-msg-1",
        },
        occurred_at=NOW,
    )

    assert order_event.event_type == "UserOrderObserved"
    assert trade_event.event_type == "UserTradeObserved"
    assert trade_event.payload["fill_authority_state"] == "FILL_CONFIRMED"


def _seed(ledger: LiveOrderAggregateLedger) -> None:
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
