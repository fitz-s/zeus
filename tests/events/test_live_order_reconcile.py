# Created: 2026-05-26
# Last reused or audited: 2026-07-03
# Authority basis: PR332 user-channel/reconcile authority substrate.
#   2026-06-08 (system_decomposition_plan §8 Step 3, P3 lift): the user-channel/reconcile
#   cycle was lifted from src.main to src.ingest.price_channel_ingest. The four
#   reconcile-cycle relationship tests repoint their host + patch surface accordingly
#   (bare call, not `.__wrapped__`; world-conn + scheduler-health patched on their
#   canonical source modules); the reconcile invariants asserted are unchanged.
from __future__ import annotations

import sqlite3
import json
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
    enqueue_user_channel_inbox_message,
    mark_user_channel_inbox_status,
    pending_user_channel_inbox_messages,
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
        payload={"raw_user_channel_message_hash": "order-direct-1"},
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
        payload={"raw_user_channel_message_hash": "trade-direct-matched-1"},
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
        payload={"raw_user_channel_message_hash": "trade-direct-confirmed-1"},
    )

    assert event.payload["fill_authority_state"] == "FILL_CONFIRMED"


def test_user_trade_before_execution_command_fails_append_law():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(Exception, match="ExecutionCommandCreated"):
        append_user_trade_observed(
            ledger,
            aggregate_id="event-1:intent-1",
            event_id="event-1",
            final_intent_id="intent-1",
            source="polymarket_user_channel",
            trade_status="CONFIRMED",
            venue_order_id="venue-1",
            occurred_at=NOW,
            payload={"raw_user_channel_message_hash": "trade-before-command"},
        )


def test_user_trade_without_submit_attempt_fails_append_law():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_without_submit_attempt(ledger)

    with pytest.raises(Exception, match="venue submit attempt"):
        append_user_trade_observed(
            ledger,
            aggregate_id="event-1:intent-1",
            event_id="event-1",
            final_intent_id="intent-1",
            source="polymarket_user_channel",
            trade_status="CONFIRMED",
            venue_order_id="venue-1",
            occurred_at=NOW,
            payload={"raw_user_channel_message_hash": "trade-without-submit"},
        )


def test_user_trade_venue_order_mismatch_fails_append_law():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    with pytest.raises(Exception, match="venue_order_id must match"):
        append_user_trade_observed(
            ledger,
            aggregate_id="event-1:intent-1",
            event_id="event-1",
            final_intent_id="intent-1",
            source="polymarket_user_channel",
            trade_status="CONFIRMED",
            venue_order_id="other-venue",
            occurred_at=NOW,
            payload={"raw_user_channel_message_hash": "trade-wrong-venue"},
        )


def test_duplicate_user_channel_message_hash_fails_append_law():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)
    append_user_trade_observed(
        ledger,
        aggregate_id="event-1:intent-1",
        event_id="event-1",
        final_intent_id="intent-1",
        source="polymarket_user_channel",
        trade_status="MATCHED",
        venue_order_id="venue-1",
        occurred_at=NOW,
        payload={"raw_user_channel_message_hash": "dup-trade"},
    )

    with pytest.raises(Exception, match="duplicate user-channel message hash"):
        append_user_trade_observed(
            ledger,
            aggregate_id="event-1:intent-1",
            event_id="event-1",
            final_intent_id="intent-1",
            source="polymarket_user_channel",
            trade_status="CONFIRMED",
            venue_order_id="venue-1",
            occurred_at=NOW,
            payload={"raw_user_channel_message_hash": "dup-trade"},
        )


def test_timeout_unknown_reconcile_clears_pending_only_from_explicit_reconcile():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "command-1", "venue_order_id": "venue-1"},
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
            "price": 0.44,
            "size": 10.0,
            "fees": 0.01,
            "trade_id": "trade-1",
            "maker_taker": "maker",
            "matched_at": "2026-05-26T12:00:00+00:00",
            "confirmed_at": "2026-05-26T12:00:01+00:00",
        },
        occurred_at=NOW,
    )

    assert order_event.event_type == "UserOrderObserved"
    assert trade_event.event_type == "UserTradeObserved"
    assert trade_event.payload["fill_authority_state"] == "FILL_CONFIRMED"
    assert trade_event.payload["avg_fill_price"] == 0.44
    assert trade_event.payload["fill_price"] == 0.44
    assert trade_event.payload["filled_size"] == 10.0
    assert trade_event.payload["fees"] == 0.01
    assert trade_event.payload["trade_id"] == "trade-1"
    assert trade_event.payload["maker_taker"] == "maker"


def test_user_channel_ingestor_requires_message_hash_for_idempotency():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    with pytest.raises(EdliUserChannelIngestorError, match="message_hash"):
        append_user_channel_message(
            ledger,
            aggregate_id="event-1:intent-1",
            message={
                "source": "polymarket_user_channel",
                "type": "order",
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "venue_order_id": "venue-1",
                "order_update_type": "UPDATE",
            },
            occurred_at=NOW,
        )


def test_user_channel_ingestor_requires_trade_message_hash_for_idempotency():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    with pytest.raises(EdliUserChannelIngestorError, match="message_hash"):
        append_user_channel_message(
            ledger,
            aggregate_id="event-1:intent-1",
            message={
                "source": "polymarket_user_channel",
                "type": "trade",
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "venue_order_id": "venue-1",
                "trade_status": "CONFIRMED",
            },
            occurred_at=NOW,
        )


def test_user_channel_ingestor_rejects_message_hash_drift():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)
    append_user_channel_message(
        ledger,
        aggregate_id="event-1:intent-1",
        message={
            "source": "polymarket_user_channel",
            "type": "trade",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "venue-1",
            "trade_status": "CONFIRMED",
            "message_hash": "trade-msg-drift",
        },
        occurred_at=NOW,
    )

    with pytest.raises(EdliUserChannelIngestorError, match="EDLI_USER_CHANNEL_MESSAGE_HASH_DRIFT"):
        append_user_channel_message(
            ledger,
            aggregate_id="event-2:intent-2",
            message={
                "source": "polymarket_user_channel",
                "type": "trade",
                "event_id": "event-2",
                "final_intent_id": "intent-2",
                "venue_order_id": "venue-1",
                "trade_status": "CONFIRMED",
                "message_hash": "trade-msg-drift",
            },
            occurred_at=NOW,
        )


def test_reconciled_without_submit_unknown_fails_append_law():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed(ledger)

    with pytest.raises(Exception, match="Reconciled requires SubmitUnknown"):
        append_reconciled(
            ledger,
            aggregate_id="event-1:intent-1",
            event_id="event-1",
            final_intent_id="intent-1",
            source="venue_reconcile",
            pending_reconcile=False,
            occurred_at=NOW,
        )


def test_cap_consumed_before_venue_authority_fails_append_law():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_without_submit_attempt(ledger)

    with pytest.raises(Exception, match="CONSUMED requires VenueSubmitAcknowledged"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="CapTransitioned",
            payload={
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "execution_command_id": "command-1",
                "execution_receipt_hash": "receipt-hash-1",
                "to_status": "CONSUMED",
                "projection_status": "CONSUMED",
                "transition_reason": "CONFIRMED",
            },
            occurred_at=NOW,
            source_authority="live_cap_ledger",
        )


def test_user_channel_reconcile_cycle_processes_authenticated_queue(monkeypatch, tmp_path):
    # P3 lift (system_decomposition_plan §8 Step 3): the user-channel/reconcile cycle
    # moved from src.main to src.ingest.price_channel_ingest. The cycle is a BARE function
    # there (the P3 daemon wraps it at add_job time, the P2 pattern), so call it directly
    # (no `.__wrapped__`). `settings` is the lane module's own module global; the world
    # trade connection + scheduler-health writer are imported INSIDE the cycle from their canonical
    # source modules, so they are patched on those sources, not on this alias.
    from src.ingest import price_channel_ingest as main
    import src.state.db as _state_db
    import src.observability.scheduler_health as _sched_health

    db_path = tmp_path / "world.db"
    conn = _conn(db_path)
    ledger = LiveOrderAggregateLedger(conn)
    _seed(ledger)
    conn.commit()
    queue_path = tmp_path / "user_channel.jsonl"
    queue_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "source": "polymarket_user_channel",
                        "type": "order",
                        "aggregate_id": "event-1:intent-1",
                        "event_id": "event-1",
                        "final_intent_id": "intent-1",
                        "venue_order_id": "venue-1",
                        "order_update_type": "UPDATE",
                        "message_hash": "order-msg-1",
                        "occurred_at": NOW.isoformat(),
                    }
                ),
                json.dumps(
                    {
                        "source": "polymarket_user_channel",
                        "type": "trade",
                        "aggregate_id": "event-1:intent-1",
                        "event_id": "event-1",
                        "final_intent_id": "intent-1",
                        "venue_order_id": "venue-1",
                        "trade_status": "CONFIRMED",
                        "message_hash": "trade-msg-1",
                        "occurred_at": NOW.isoformat(),
                    }
                ),
            ]
        )
    )

    monkeypatch.setattr(
        main,
        "settings",
        {
            "edli": {
                "enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "edli_user_channel_message_queue_path": str(queue_path),
                "edli_venue_reconcile_facts_path": "",
            }
        },
    )
    monkeypatch.setattr(
        _state_db,
        "get_trade_connection_with_world_required",
        lambda *args, **kwargs: conn,
    )
    monkeypatch.setattr(_sched_health, "_write_scheduler_health", lambda *args, **kwargs: None)

    main._edli_user_channel_reconcile_cycle()

    check_ledger = LiveOrderAggregateLedger(_conn(db_path))
    projection = check_ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "USER_TRADE_OBSERVED"
    row = check_ledger.conn.execute(
        """
        SELECT payload_json
        FROM edli_live_order_events
        WHERE event_type = 'UserTradeObserved'
        """
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload["fill_authority_state"] == "FILL_CONFIRMED"


def test_user_channel_reconcile_cycle_is_idempotent_for_duplicate_queue_messages(monkeypatch, tmp_path):
    # P3 lift (system_decomposition_plan §8 Step 3): the user-channel/reconcile cycle
    # moved from src.main to src.ingest.price_channel_ingest. The cycle is a BARE function
    # there (the P3 daemon wraps it at add_job time, the P2 pattern), so call it directly
    # (no `.__wrapped__`). `settings` is the lane module's own module global; the world
    # trade connection + scheduler-health writer are imported INSIDE the cycle from their canonical
    # source modules, so they are patched on those sources, not on this alias.
    from src.ingest import price_channel_ingest as main
    import src.state.db as _state_db
    import src.observability.scheduler_health as _sched_health

    db_path = tmp_path / "world.db"
    conn = _conn(db_path)
    ledger = LiveOrderAggregateLedger(conn)
    _seed(ledger)
    conn.commit()
    queue_path = tmp_path / "user_channel.jsonl"
    queue_path.write_text(
        json.dumps(
            {
                "source": "polymarket_user_channel",
                "type": "order",
                "aggregate_id": "event-1:intent-1",
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "venue_order_id": "venue-1",
                "order_update_type": "UPDATE",
                "message_hash": "order-msg-1",
                "occurred_at": NOW.isoformat(),
            }
        )
        + "\n"
    )
    monkeypatch.setattr(
        main,
        "settings",
        {
            "edli": {
                "enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "edli_user_channel_message_queue_path": str(queue_path),
                "edli_venue_reconcile_facts_path": "",
            }
        },
    )
    monkeypatch.setattr(
        _state_db,
        "get_trade_connection_with_world_required",
        lambda *args, **kwargs: _conn(db_path),
    )
    monkeypatch.setattr(_sched_health, "_write_scheduler_health", lambda *args, **kwargs: None)

    main._edli_user_channel_reconcile_cycle()
    main._edli_user_channel_reconcile_cycle()

    check_conn = _conn(db_path)
    count = check_conn.execute(
        "SELECT COUNT(*) FROM edli_live_order_events WHERE event_type = 'UserOrderObserved'"
    ).fetchone()[0]
    assert count == 1
    inbox = check_conn.execute(
        "SELECT processing_status FROM edli_user_channel_inbox WHERE message_hash = ?",
        ("order-msg-1",),
    ).fetchone()
    assert inbox["processing_status"] in {"PROCESSED", "DUPLICATE"}


def test_user_channel_inbox_rejects_hash_drift_before_aggregate_append():
    conn = _conn()
    enqueue_user_channel_inbox_message(
        conn,
        aggregate_id="event-1:intent-1",
        message={
            "source": "polymarket_user_channel",
            "type": "trade",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "venue-1",
            "trade_status": "CONFIRMED",
            "message_hash": "inbox-msg-1",
        },
        occurred_at=NOW,
        received_at=NOW,
    )

    with pytest.raises(EdliUserChannelIngestorError, match="EDLI_USER_CHANNEL_INBOX_HASH_DRIFT"):
        enqueue_user_channel_inbox_message(
            conn,
            aggregate_id="event-2:intent-2",
            message={
                "source": "polymarket_user_channel",
                "type": "trade",
                "event_id": "event-2",
                "final_intent_id": "intent-2",
                "venue_order_id": "venue-1",
                "trade_status": "CONFIRMED",
                "message_hash": "inbox-msg-1",
            },
            occurred_at=NOW,
            received_at=NOW,
        )


def test_user_channel_inbox_persists_pending_until_ack_status():
    conn = _conn()
    inserted = enqueue_user_channel_inbox_message(
        conn,
        aggregate_id="event-1:intent-1",
        message={
            "source": "polymarket_user_channel",
            "type": "order",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "venue_order_id": "venue-1",
            "order_update_type": "UPDATE",
            "message_hash": "inbox-order-1",
        },
        occurred_at=NOW,
        received_at=NOW,
    )
    assert inserted is True
    assert len(pending_user_channel_inbox_messages(conn, limit=10)) == 1

    mark_user_channel_inbox_status(
        conn,
        message_hash="inbox-order-1",
        status="PROCESSED",
        processed_at=NOW,
    )

    assert pending_user_channel_inbox_messages(conn, limit=10) == []


def test_user_channel_reconcile_cycle_is_idempotent_for_duplicate_trade_messages(monkeypatch, tmp_path):
    # P3 lift (system_decomposition_plan §8 Step 3): the user-channel/reconcile cycle
    # moved from src.main to src.ingest.price_channel_ingest. The cycle is a BARE function
    # there (the P3 daemon wraps it at add_job time, the P2 pattern), so call it directly
    # (no `.__wrapped__`). `settings` is the lane module's own module global; the world
    # connection + scheduler-health writer are imported INSIDE the cycle from their canonical
    # source modules, so they are patched on those sources, not on this alias.
    from src.ingest import price_channel_ingest as main
    import src.state.db as _state_db
    import src.observability.scheduler_health as _sched_health

    db_path = tmp_path / "world.db"
    conn = _conn(db_path)
    ledger = LiveOrderAggregateLedger(conn)
    _seed(ledger)
    conn.commit()
    queue_path = tmp_path / "user_channel.jsonl"
    queue_path.write_text(
        json.dumps(
            {
                "source": "polymarket_user_channel",
                "type": "trade",
                "aggregate_id": "event-1:intent-1",
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "venue_order_id": "venue-1",
                "trade_status": "CONFIRMED",
                "message_hash": "trade-msg-1",
                "occurred_at": NOW.isoformat(),
            }
        )
        + "\n"
    )
    monkeypatch.setattr(
        main,
        "settings",
        {
            "edli": {
                "enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "edli_user_channel_message_queue_path": str(queue_path),
                "edli_venue_reconcile_facts_path": "",
            }
        },
    )
    monkeypatch.setattr(
        _state_db,
        "get_trade_connection_with_world_required",
        lambda *args, **kwargs: _conn(db_path),
    )
    monkeypatch.setattr(_sched_health, "_write_scheduler_health", lambda *args, **kwargs: None)

    main._edli_user_channel_reconcile_cycle()
    main._edli_user_channel_reconcile_cycle()

    check_conn = _conn(db_path)
    count = check_conn.execute(
        "SELECT COUNT(*) FROM edli_live_order_events WHERE event_type = 'UserTradeObserved'"
    ).fetchone()[0]
    assert count == 1


def test_user_channel_reconcile_cycle_clears_submit_unknown_from_venue_fact(monkeypatch, tmp_path):
    # P3 lift (system_decomposition_plan §8 Step 3): the user-channel/reconcile cycle
    # moved from src.main to src.ingest.price_channel_ingest. The cycle is a BARE function
    # there (the P3 daemon wraps it at add_job time, the P2 pattern), so call it directly
    # (no `.__wrapped__`). `settings` is the lane module's own module global; the world
    # connection + scheduler-health writer are imported INSIDE the cycle from their canonical
    # source modules, so they are patched on those sources, not on this alias.
    from src.ingest import price_channel_ingest as main
    import src.state.db as _state_db
    import src.observability.scheduler_health as _sched_health

    db_path = tmp_path / "world.db"
    conn = _conn(db_path)
    ledger = LiveOrderAggregateLedger(conn)
    _seed(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "command-1", "venue_order_id": "venue-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    assert ledger.get_projection("event-1:intent-1").pending_reconcile is True
    conn.commit()
    reconcile_path = tmp_path / "venue_reconcile.jsonl"
    reconcile_path.write_text(
        json.dumps(
            {
                "aggregate_id": "event-1:intent-1",
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "source": "venue_reconcile",
                "pending_reconcile": False,
                "observed_at": NOW.isoformat(),
                "payload": {"venue_order_exists": False, "cap_transition_recommendation": "RELEASED"},
            }
        )
        + "\n"
    )

    monkeypatch.setattr(
        main,
        "settings",
        {
            "edli": {
                "enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "edli_user_channel_message_queue_path": "",
                "edli_venue_reconcile_facts_path": str(reconcile_path),
            }
        },
    )
    monkeypatch.setattr(
        _state_db,
        "get_trade_connection_with_world_required",
        lambda *args, **kwargs: conn,
    )
    monkeypatch.setattr(_sched_health, "_write_scheduler_health", lambda *args, **kwargs: None)

    main._edli_user_channel_reconcile_cycle()

    check_ledger = LiveOrderAggregateLedger(_conn(db_path))
    projection = check_ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "RECONCILED"
    assert projection.pending_reconcile is False


def _seed(ledger: LiveOrderAggregateLedger) -> None:
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
            "usage_id": "usage-1",
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "command-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "venue_order_id": "venue-1",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )


def _seed_command_without_submit_attempt(ledger: LiveOrderAggregateLedger) -> None:
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    pre_submit = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    live_cap = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "command-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
            "usage_id": "usage-1",
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )


def _pre_submit_payload(**overrides):
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "token-1",
        "side": "BUY",
        "direction": "YES",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-05-26T12:00:00+00:00",
        "quote_seen_at": "2026-05-26T11:59:59.900000+00:00",
        "quote_age_ms": 100,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.42,
        "current_best_ask": 0.43,
        "limit_price": 0.42,
        "size": 7.0,
        "q_live": 0.72,
        "q_lcb_5pct": 0.62,
        "expected_edge": 0.20,
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 1.0,
        "min_submit_edge_density": 0.05,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "route_type": "portfolio",
        },
        "would_cross_book": False,
        "tick_size": 0.01,
        "tick_aligned": True,
        "min_order_size": 5.0,
        "size_ok": True,
        "neg_risk": False,
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "execution_feasibility_evidence",
        "book_captured_at": "2026-05-26T11:59:59.900000+00:00",
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": "2026-05-26T12:00:00+00:00",
        "user_ws_authority_id": "authenticated_user_channel",
        "user_ws_checked_at": "2026-05-26T12:00:00+00:00",
        "venue_connectivity_authority_id": "polymarket_preflight",
        "venue_connectivity_checked_at": "2026-05-26T12:00:00+00:00",
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": "2026-05-26T12:00:00+00:00",
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
    }
    payload.update(overrides)
    return payload


def _conn(path=":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    from src.state.schema.edli_live_order_events_schema import ensure_tables

    ensure_tables(conn)
    return conn
