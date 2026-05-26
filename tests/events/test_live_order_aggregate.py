# Created: 2026-05-25
# Last reused/audited: 2026-05-25
# Authority basis: PR332 full-live split verdict; live-order aggregate substrate PR A.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.events.live_order_aggregate import LiveOrderAggregateError, LiveOrderAggregateLedger
from src.state.db import init_schema


NOW = datetime(2026, 5, 25, 18, tzinfo=timezone.utc)


def test_live_order_aggregate_appends_and_projects_sequence():
    ledger = LiveOrderAggregateLedger(_conn())

    first = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    second = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="LiveCapReserved",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
        expected_parent_event_hash=first.event_hash,
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert second.event_sequence == 2
    assert second.parent_event_hash == first.event_hash
    assert projection.current_state == "LIVE_CAP_RESERVED"
    assert projection.last_sequence == 2
    assert projection.final_intent_id == "intent-1"


def test_live_order_projection_rebuilds_from_append_only_events():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    conn.execute("DELETE FROM edli_live_order_projection WHERE aggregate_id = ?", ("event-1:intent-1",))
    rebuilt = ledger.rebuild_projection("event-1:intent-1")

    assert rebuilt.current_state == "EXECUTION_COMMAND_CREATED"
    assert rebuilt.last_sequence == 2


def test_live_order_aggregate_rejects_parent_hash_mismatch():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="parent hash mismatch"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="LiveCapReserved",
            payload={"event_id": "event-1", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="live_cap_ledger",
            expected_parent_event_hash="wrong",
        )


def test_live_order_aggregate_rejects_event_id_drift_before_append():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="event_id drift"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="SubmitPlanBuilt",
            payload={"event_id": "event-2", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_live_order_events_table_is_append_only():
    ledger = LiveOrderAggregateLedger(_conn())
    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.conn.execute(
            "UPDATE edli_live_order_events SET event_type = 'SubmitPlanBuilt' WHERE aggregate_event_id = ?",
            (event.aggregate_event_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        ledger.conn.execute(
            "DELETE FROM edli_live_order_events WHERE aggregate_event_id = ?",
            (event.aggregate_event_id,),
        )


def test_live_order_submit_unknown_stays_pending_until_reconcile():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    assert ledger.get_projection("event-1:intent-1").pending_reconcile is True

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="Reconciled",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "pending_reconcile": False},
        occurred_at=NOW,
        source_authority="explicit_reconcile",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "RECONCILED"
    assert projection.pending_reconcile is False


def test_init_schema_creates_live_order_aggregate_tables():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert {"edli_live_order_events", "edli_live_order_projection"} <= tables


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
