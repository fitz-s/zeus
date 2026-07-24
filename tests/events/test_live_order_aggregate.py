# Created: 2026-05-25
# Last reused/audited: 2026-07-18
# Authority basis: PR332 full-live split verdict; live-order aggregate substrate PR A.
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest

from src.decision_kernel.canonicalization import qkernel_current_state_identity_hash
from src.events.live_order_aggregate import LiveOrderAggregateError, LiveOrderAggregateLedger
from src.state.db import init_schema


NOW = datetime(2026, 5, 25, 18, tzinfo=timezone.utc)


def test_initialized_schema_mode_does_not_repeat_schema_work():
    conn = _conn()
    init_schema(conn)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        ledger = LiveOrderAggregateLedger(conn, initialize_schema=False)
        ledger.append_event(
            aggregate_id="event-fast:intent-fast",
            event_type="DecisionProofAccepted",
            payload={"event_id": "event-fast", "final_intent_id": "intent-fast"},
            occurred_at=NOW,
            source_authority="decision_kernel",
        )
    finally:
        conn.set_trace_callback(None)

    assert not any(sql.lstrip().upper().startswith("CREATE ") for sql in statements)
    assert not any("PRAGMA table_info" in sql for sql in statements)


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
    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    incremental = dict(
        conn.execute(
            "SELECT * FROM edli_live_order_projection WHERE aggregate_id = ?",
            ("event-1:intent-1",),
        ).fetchone()
    )
    conn.execute("DELETE FROM edli_live_order_projection WHERE aggregate_id = ?", ("event-1:intent-1",))
    rebuilt = ledger.rebuild_projection("event-1:intent-1")
    replayed = dict(
        conn.execute(
            "SELECT * FROM edli_live_order_projection WHERE aggregate_id = ?",
            ("event-1:intent-1",),
        ).fetchone()
    )

    assert rebuilt.current_state == "EXECUTION_COMMAND_CREATED"
    assert rebuilt.last_sequence == 4
    incremental.pop("updated_at")
    replayed.pop("updated_at")
    assert replayed == incremental


def test_live_order_append_projects_incrementally_without_history_replay():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="SubmitPlanBuilt",
            payload={"event_id": "event-1", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
    finally:
        conn.set_trace_callback(None)

    normalized = [" ".join(sql.split()) for sql in statements]
    assert not any("ORDER BY event_sequence ASC" in sql for sql in normalized)
    assert any(sql.startswith("UPDATE edli_live_order_projection") for sql in normalized)
    projection = ledger.get_projection("event-1:intent-1")
    assert projection.last_sequence == 2
    assert projection.current_state == "SUBMIT_PLAN_BUILT"


def test_live_order_append_rolls_back_event_when_projection_fails(monkeypatch):
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)

    def _fail_projection(**_kwargs):
        raise LiveOrderAggregateError("injected projection failure")

    monkeypatch.setattr(ledger, "_project_appended_event", _fail_projection)
    with pytest.raises(LiveOrderAggregateError, match="injected projection failure"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="DecisionProofAccepted",
            payload={"event_id": "event-1", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="decision_kernel",
        )

    assert conn.execute("SELECT COUNT(*) FROM edli_live_order_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edli_live_order_projection").fetchone()[0] == 0
    assert conn.in_transaction is False


def test_live_order_append_repairs_stale_projection_before_incremental_update():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    first = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    conn.execute(
        """
        UPDATE edli_live_order_projection
           SET last_sequence = 0,
               last_event_hash = 'stale'
         WHERE aggregate_id = 'event-1:intent-1'
        """
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        second = ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="SubmitPlanBuilt",
            payload={"event_id": "event-1", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="engine_adapter",
            expected_parent_event_hash=first.event_hash,
        )
    finally:
        conn.set_trace_callback(None)

    normalized = [" ".join(sql.split()) for sql in statements]
    assert any("ORDER BY event_sequence ASC" in sql for sql in normalized)
    projection = ledger.get_projection("event-1:intent-1")
    assert second.event_sequence == 2
    assert projection.last_sequence == 2
    assert projection.last_event_hash == second.event_hash
    assert projection.current_state == "SUBMIT_PLAN_BUILT"


@pytest.mark.parametrize(
    ("column", "corrupt_value"),
    [
        ("current_state", "CORRUPT"),
        ("last_event_type", "SubmitRejected"),
        ("pending_reconcile", 0),
        ("venue_order_id", "wrong-order"),
        ("final_intent_id", "wrong-intent"),
        ("posterior_id", 999),
        ("probability_authority", "wrong-authority"),
    ],
)
def test_live_order_append_repairs_projection_state_hash_mismatch(
    column,
    corrupt_value,
):
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-unknown",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "decision_audit": {
                "posterior_id": 42,
                "probability_authority": "replacement",
            },
        },
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    conn.execute(
        f"UPDATE edli_live_order_projection SET {column} = ? WHERE aggregate_id = ?",
        (corrupt_value, "event-1:intent-1"),
    )
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="SubmitPlanBuilt",
            payload={"event_id": "event-1", "final_intent_id": "intent-1"},
            occurred_at=NOW,
            source_authority="engine_adapter",
        )
    finally:
        conn.set_trace_callback(None)

    normalized = [" ".join(sql.split()) for sql in statements]
    assert any("ORDER BY event_sequence ASC" in sql for sql in normalized)
    row = conn.execute(
        "SELECT * FROM edli_live_order_projection WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    ).fetchone()
    assert row["current_state"] == "SUBMIT_PLAN_BUILT"
    assert row["pending_reconcile"] == 1
    assert row["venue_order_id"] == "venue-unknown"
    assert row["final_intent_id"] == "intent-1"
    assert row["posterior_id"] == 42
    assert row["probability_authority"] == "replacement"
    assert row["projection_state_hash"]


def test_live_order_append_serializes_before_reading_aggregate_tail(tmp_path):
    path = tmp_path / "live-order-race.db"
    conn1 = sqlite3.connect(path, timeout=2.0)
    conn1.row_factory = sqlite3.Row
    conn1.execute("PRAGMA journal_mode = WAL")
    ledger1 = LiveOrderAggregateLedger(conn1)
    conn1.commit()

    conn2 = sqlite3.connect(path, timeout=2.0, check_same_thread=False)
    conn2.row_factory = sqlite3.Row
    ledger2 = LiveOrderAggregateLedger(conn2)
    conn2.commit()

    first = ledger1.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    started = threading.Event()
    results = []
    errors = []

    def _append_second():
        started.set()
        try:
            results.append(
                ledger2.append_event(
                    aggregate_id="event-1:intent-1",
                    event_type="SubmitPlanBuilt",
                    payload={"event_id": "event-1", "final_intent_id": "intent-1"},
                    occurred_at=NOW,
                    source_authority="engine_adapter",
                    expected_parent_event_hash=first.event_hash,
                )
            )
            conn2.commit()
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    thread = threading.Thread(target=_append_second)
    thread.start()
    assert started.wait(timeout=1.0)
    time.sleep(0.05)
    assert thread.is_alive()
    conn1.commit()
    thread.join(timeout=2.0)
    conn2.close()

    assert not thread.is_alive()
    assert errors == []
    assert [event.event_sequence for event in results] == [2]
    projection = ledger1.get_projection("event-1:intent-1")
    assert projection.last_sequence == 2
    assert projection.current_state == "SUBMIT_PLAN_BUILT"


def test_live_order_projection_hash_migrates_legacy_row_and_repairs_on_append():
    from src.state.schema.edli_live_order_events_schema import ensure_tables

    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-legacy:intent-legacy",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-legacy", "final_intent_id": "intent-legacy"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    conn.commit()
    conn.execute(
        "ALTER TABLE edli_live_order_projection DROP COLUMN projection_state_hash"
    )
    conn.commit()

    ensure_tables(conn)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(edli_live_order_projection)")
    }
    assert "projection_state_hash" in columns
    assert conn.execute(
        "SELECT projection_state_hash FROM edli_live_order_projection "
        "WHERE aggregate_id = 'event-legacy:intent-legacy'"
    ).fetchone()[0] is None

    migrated = LiveOrderAggregateLedger(conn, initialize_schema=False)
    migrated.append_event(
        aggregate_id="event-legacy:intent-legacy",
        event_type="SubmitPlanBuilt",
        payload={"event_id": "event-legacy", "final_intent_id": "intent-legacy"},
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    row = conn.execute(
        "SELECT last_sequence, current_state, projection_state_hash "
        "FROM edli_live_order_projection "
        "WHERE aggregate_id = 'event-legacy:intent-legacy'"
    ).fetchone()
    assert tuple(row[:2]) == (2, "SUBMIT_PLAN_BUILT")
    assert row[2]


def test_live_order_append_respects_caller_owned_transaction_rollback():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")

    ledger.append_event(
        aggregate_id="event-rollback:intent-rollback",
        event_type="DecisionProofAccepted",
        payload={
            "event_id": "event-rollback",
            "final_intent_id": "intent-rollback",
        },
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    assert conn.execute("SELECT COUNT(*) FROM edli_live_order_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM edli_live_order_projection").fetchone()[0] == 1

    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM edli_live_order_events").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM edli_live_order_projection").fetchone()[0] == 0


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
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-unknown",
        },
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


def test_pre_submit_rejected_terminates_without_venue_submit_attempt():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_without_submit_attempt(ledger)

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitRejected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "execution_receipt_hash": "receipt-hash",
            "reason_code": "duplicate_entry_same_token:token-yes",
            "submit_status": "PRE_SUBMIT_ERROR",
            "venue_call_started": False,
            "venue_ack_received": False,
            "pre_submit_rejection": True,
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "SUBMIT_REJECTED"
    assert projection.pending_reconcile is False


def test_non_pre_submit_rejected_requires_venue_submit_attempt():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_without_submit_attempt(ledger)

    with pytest.raises(LiveOrderAggregateError, match="VenueSubmitAttempted"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="SubmitRejected",
            payload={
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "execution_command_id": "cmd-1",
                "execution_receipt_hash": "receipt-hash",
                "reason_code": "venue_rejected",
                "submit_status": "REJECTED",
                "venue_call_started": True,
                "venue_ack_received": False,
                "pre_submit_rejection": False,
            },
            occurred_at=NOW,
            source_authority="existing_executor",
        )


def test_live_order_aggregate_allows_new_submit_attempt_after_new_command():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitRejected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "execution_receipt_hash": "receipt-hash-1",
            "reason_code": "venue_rejected",
            "submit_status": "REJECTED",
            "venue_call_started": True,
            "venue_ack_received": True,
            "pre_submit_rejection": False,
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )
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
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "usage_id": "usage-2"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="ExecutionCommandCreated",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-2",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-2"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )

    with pytest.raises(LiveOrderAggregateError, match="current command"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="VenueSubmitAttempted",
            payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-2"},
            occurred_at=NOW,
            source_authority="existing_executor",
        )


def test_live_order_cap_transition_pending_reconcile_sets_projection_pending():
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
            "execution_command_id": "cmd-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="SubmitUnknown",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-unknown",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "to_status": "PENDING_RECONCILE",
            "projection_status": "RESERVED",
            "transition_reason": "SUBMIT_TIMEOUT",
            "execution_receipt_hash": "receipt-hash",
        },
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.pending_reconcile is True
    assert projection.current_state == "PENDING_RECONCILE"


def test_cap_consumed_does_not_hide_acknowledged_live_order_projection():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-live-1",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="CapTransitioned",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "to_status": "CONSUMED",
            "execution_receipt_hash": "receipt-hash",
        },
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "VENUE_SUBMIT_ACKED"
    assert projection.last_event_type == "CapTransitioned"
    assert projection.venue_order_id == "venue-live-1"
    assert projection.pending_reconcile is False


def test_order_lifecycle_projected_terminal_no_fill_closes_projection():
    ledger = LiveOrderAggregateLedger(_conn())
    _seed_command_with_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAcknowledged",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-live-1",
        },
        occurred_at=NOW,
        source_authority="existing_executor",
    )

    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="OrderLifecycleProjected",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "execution_command_id": "cmd-1",
            "venue_order_id": "venue-live-1",
            "order_lifecycle_state": "TERMINAL_NO_FILL",
            "exposure_created": False,
            "pending_reconcile": False,
        },
        occurred_at=NOW,
        source_authority="explicit_reconcile",
    )

    projection = ledger.get_projection("event-1:intent-1")
    assert projection.current_state == "TERMINAL_NO_FILL"
    assert projection.last_event_type == "OrderLifecycleProjected"
    assert projection.pending_reconcile is False
    assert projection.venue_order_id == "venue-live-1"


def test_execution_command_requires_pre_submit_revalidation():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="PreSubmitRevalidated"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="ExecutionCommandCreated",
            payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"quote_age_ms": 1001, "max_quote_age_ms": 1000}, "quote_age_ms exceeds"),
        ({"would_cross_book": True}, "would_cross_book=false"),
        ({"tick_aligned": False}, "tick_aligned=true"),
        ({"size_ok": False}, "size_ok=true"),
        ({"heartbeat_status": "EXPIRED"}, "heartbeat_status=OK"),
        ({"user_ws_status": "GAP"}, "user_ws_status=OK"),
        ({"venue_connectivity_status": "DOWN"}, "venue_connectivity_status=OK"),
        ({"balance_allowance_status": "INSUFFICIENT"}, "balance_allowance_status=OK"),
        ({"book_authority_id": ""}, "book_authority_id"),
        ({"current_best_bid": "invalid"}, "current_best_bid"),
        ({"current_best_ask": None}, "current_best_ask"),
        ({"time_in_force": "FOK"}, "GTC/GTD"),
        ({"q_lcb_5pct": 0.72, "q_live": 0.70}, "q_lcb_5pct <= q_live"),
        ({"q_lcb_5pct": 0.39, "limit_price": 0.40}, "positive submit q_lcb-minus-cost-bound"),
        ({"expected_edge": 0.25}, "expected_edge exceeds"),
        ({"size": 0.0}, "positive size"),
        ({"min_entry_price": -0.01}, "min_entry_price"),
        # One-law 2026-07-24: the live floor is the universal band edge 0.05,
        # so a declared 0.05 sits AT the floor (legal); 0.04 is below it.
        ({"min_entry_price": 0.04}, "min_entry_price below live floor"),
        ({"limit_price": 0.09, "min_entry_price": 0.10}, "entry price below strategy floor"),
        ({"min_expected_profit_usd": 10.0}, "expected profit below"),
        ({"min_submit_edge_density": 0.75}, "submit edge density below"),
        ({"expected_edge_source_certificate_hash": ""}, "expected_edge_source_certificate_hash"),
        ({"cost_basis_source_certificate_hash": ""}, "cost_basis_source_certificate_hash"),
        ({"qkernel_execution_economics": None}, "qkernel_execution_economics"),
    ],
)
def test_pre_submit_revalidation_failures_block_command(override, message):
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match=message):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(**override),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


@pytest.mark.parametrize(
    ("direction", "payoff_side"),
    (("buy_yes", "YES"), ("buy_no", "NO")),
)
def test_pre_submit_buy_accepts_ask_only_book(direction, payoff_side):
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    payload = _pre_submit_payload(current_best_bid=None, direction=direction)
    payload["qkernel_execution_economics"] = {
        **payload["qkernel_execution_economics"],
        "side": payoff_side,
        "route_id": f"DIRECT_{payoff_side}:b20@proof",
    }
    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=payload,
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    assert event.event_type == "PreSubmitRevalidated"


def test_pre_submit_rejects_lucknow_negative_submit_edge():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="positive submit q_lcb-minus-cost-bound"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                q_live=0.12,
                q_lcb_5pct=0.11,
                limit_price=0.12,
                expected_edge=0.04,
                min_entry_price=0.10,
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_global_exact_submit_uses_fee_aware_max_spend_not_raw_limit_or_base_cost():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    base = _pre_submit_payload(
        order_type="FOK",
        time_in_force="FOK",
        post_only=False,
        would_cross_book=True,
        limit_price=0.44,
        size=10.0,
        q_live=0.70,
        q_lcb_5pct=0.60,
        expected_edge=0.15,
    )
    economics = dict(base["qkernel_execution_economics"])
    economics.update(
        {
            "cost": 0.40,
            "edge_lcb": 0.20,
            "global_actuation_identity": "global-actuation-1",
            "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
            "global_target_shares": "10",
            "global_limit_price": "0.44",
            "global_expected_fill_price_before_fee": "0.41",
            "global_expected_cost_usd": "4.2",
            "global_max_spend_usd": "4.5",
            "global_robust_delta_log_wealth": 0.01,
            "global_robust_ev_usd": 1.0,
        }
    )
    base["qkernel_execution_economics"] = economics

    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=base,
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    assert event.event_type == "PreSubmitRevalidated"


def test_global_maker_accepts_same_terminal_shares_at_better_limit():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    payload = _pre_submit_payload(
        order_type="GTC",
        time_in_force="GTC",
        post_only=True,
        would_cross_book=False,
        limit_price=0.40,
        size=10.0,
        q_live=0.70,
        q_lcb_5pct=0.60,
        expected_edge=0.15,
    )
    economics = dict(payload["qkernel_execution_economics"])
    economics.update(
        {
            "cost": 0.40,
            "edge_lcb": 0.20,
            "global_actuation_identity": "global-actuation-1",
            "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
            "global_target_shares": "10",
            "global_limit_price": "0.44",
            "global_expected_fill_price_before_fee": "0.41",
            "global_expected_cost_usd": "4.2",
            "global_max_spend_usd": "4.5",
            "global_robust_delta_log_wealth": 0.01,
            "global_robust_ev_usd": 1.0,
        }
    )
    payload["qkernel_execution_economics"] = economics

    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=payload,
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    assert event.event_type == "PreSubmitRevalidated"


def test_global_maker_rejects_limit_worse_than_selected_curve():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    payload = _pre_submit_payload(
        order_type="GTC",
        time_in_force="GTC",
        post_only=True,
        would_cross_book=False,
        limit_price=0.45,
        size=10.0,
        q_live=0.70,
        q_lcb_5pct=0.60,
        expected_edge=0.14,
    )
    economics = dict(payload["qkernel_execution_economics"])
    economics.update(
        {
            "cost": 0.40,
            "edge_lcb": 0.20,
            "global_actuation_identity": "global-actuation-1",
            "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
            "global_target_shares": "10",
            "global_limit_price": "0.44",
            "global_expected_fill_price_before_fee": "0.41",
            "global_expected_cost_usd": "4.2",
            "global_max_spend_usd": "4.5",
        }
    )
    payload["qkernel_execution_economics"] = economics

    with pytest.raises(LiveOrderAggregateError, match="global maker limit worsened"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=payload,
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_global_exact_submit_rejects_edge_above_fee_aware_max_spend_bound():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    payload = _pre_submit_payload(
        order_type="FOK",
        time_in_force="FOK",
        post_only=False,
        would_cross_book=True,
        limit_price=0.44,
        size=10.0,
        q_live=0.70,
        q_lcb_5pct=0.60,
        expected_edge=0.151,
    )
    economics = dict(payload["qkernel_execution_economics"])
    economics.update(
        {
            "cost": 0.40,
            "edge_lcb": 0.20,
            "global_actuation_identity": "global-actuation-1",
            "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
            "global_target_shares": "10",
            "global_limit_price": "0.44",
            "global_expected_fill_price_before_fee": "0.41",
            "global_expected_cost_usd": "4.2",
            "global_max_spend_usd": "4.5",
        }
    )
    payload["qkernel_execution_economics"] = economics

    with pytest.raises(LiveOrderAggregateError, match="conservative submit edge"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=payload,
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_allows_exact_strategy_entry_floor_for_day0_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_day0_pre_submit_payload(
            limit_price=0.10,
            q_live=0.50,
            q_lcb_5pct=0.30,
            expected_edge=0.20,
            size=10.0,
            min_entry_price=0.10,
            min_expected_profit_usd=0.50,
            min_submit_edge_density=0.05,
            current_best_bid=0.09,
            current_best_ask=0.11,
            qkernel_execution_economics={
                **_day0_qkernel_economics(),
                "payoff_q_point": 0.50,
                "payoff_q_lcb": 0.30,
                "cost": 0.10,
                "edge_lcb": 0.20,
                "selection_guard_q_safe": 0.30,
            },
        ),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    assert event.event_type == "PreSubmitRevalidated"


def test_pre_submit_rejects_weak_qkernel_without_selection_guard():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    weak_economics = dict(_pre_submit_payload()["qkernel_execution_economics"])
    weak_economics.pop("selection_guard_basis")

    with pytest.raises(LiveOrderAggregateError, match="selection_guard_basis"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(qkernel_execution_economics=weak_economics),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


@pytest.mark.parametrize(
    ("direction", "side"),
    (("buy_yes", "YES"), ("buy_no", "NO")),
)
def test_pre_submit_current_state_winner_ignores_legacy_profit_density_floors(
    direction,
    side,
):
    ledger = LiveOrderAggregateLedger(_conn())
    payload = _pre_submit_payload(
        direction=direction,
        size=1.0,
        min_order_size=1.0,
        min_entry_price=0.10,
        min_expected_profit_usd=1000.0,
        min_submit_edge_density=1000.0,
    )
    economics = dict(payload["qkernel_execution_economics"])
    economics.update(
        {
            "side": side,
            "decision_id": "decision-current-1",
            "receipt_hash": "receipt-current-1",
            "q_version": "q-current-1",
            "sample_hash": "current-sample-1",
            "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "current-sample-1",
            "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
            "selection_guard_abstained": False,
            "selection_guard_cell_key": "current-sample-1",
            "selection_guard_n": 64,
            "optimal_stake_usd": 0.01,
            "global_actuation_identity": "global-current-1",
            "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
            "global_candidate_id": "candidate-current-1",
            "global_bin_id": "bin-1",
            "global_universe_witness_identity": "universe-current-1",
            "global_wealth_witness_identity": "wealth-current-1",
            "global_selection_epoch_identity": "epoch-current-1",
            "global_selection_cut_at": "2026-07-13T02:00:00+00:00",
            "global_selection_decision_at": "2026-07-13T02:00:01+00:00",
            "global_jit_book_hash": "book-current-1",
            "global_jit_venue_book_hash": "venue-book-current-1",
            "global_jit_book_snapshot_id": "snapshot-current-1",
            "global_jit_execution_curve_identity": "curve-current-1",
            "global_target_shares": "1",
            "global_limit_price": "0.40",
            "global_expected_fill_price_before_fee": "0.40",
            "global_expected_cost_usd": "0.40",
            "global_max_spend_usd": "0.40",
            "global_robust_delta_log_wealth": 0.001,
            "global_robust_ev_usd": 0.20,
            "global_cut_time_win_probability_lcb": 0.60,
            "global_cut_time_loss_probability_ucb": 0.40,
            "global_terminal_win_probability_lcb": 0.60,
            "global_terminal_loss_probability_ucb": 0.40,
            "global_terminal_loss_payoff_usd": "-0.40",
            "global_terminal_win_payoff_usd": "0.60",
            "global_terminal_median_payoff_usd": "0.60",
            "global_terminal_wealth_after_loss_usd": "99.60",
            "global_terminal_wealth_after_win_usd": "100.60",
            "global_cut_time_expected_value_diagnostic_usd": 0.20,
            "global_expected_value_diagnostic_usd": 0.20,
            "global_expected_value_semantics": (
                "DIAGNOSTIC_EXPECTATION_NOT_REALIZED_GAIN"
            ),
            "global_terminal_payoff_semantics": "BINARY_0_1",
        }
    )
    for legacy_field in (
        "route_id",
        "route_type",
        "delta_u_at_min",
        "optimal_stake_usd",
        "optimal_delta_u",
        "false_edge_rate",
        "direction_law_ok",
        "coherence_allows",
    ):
        economics.pop(legacy_field, None)
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )
    payload["qkernel_execution_economics"] = economics
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "decision_audit": {"qkernel_execution_economics": economics},
        },
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    event = ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=payload,
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    assert event.event_type == "PreSubmitRevalidated"


    drift_ledger = LiveOrderAggregateLedger(_conn())
    drift_ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "decision_audit": {"qkernel_execution_economics": economics},
        },
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    with pytest.raises(LiveOrderAggregateError, match="payoff_q_lcb mismatches"):
        drift_ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload={**payload, "q_lcb_5pct": 0.61},
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


@pytest.mark.parametrize(
    ("direction", "side"),
    (("buy_yes", "YES"), ("buy_no", "NO")),
)
def test_pre_submit_current_state_cannot_waive_venue_tick_boundary(
    direction, side
):
    ledger = LiveOrderAggregateLedger(_conn())
    payload = _pre_submit_payload(
        direction=direction,
        limit_price=0.001,
        size=1000.0,
        min_order_size=5.0,
        q_live=0.92,
        q_lcb_5pct=0.80,
        expected_edge=0.799,
        min_entry_price=0.10,
    )
    economics = dict(payload["qkernel_execution_economics"])
    economics.update(
        {
            "side": side,
            "payoff_q_point": 0.92,
            "payoff_q_lcb": 0.80,
            "cost": 0.001,
            "edge_lcb": 0.799,
            "decision_id": "decision-current-floor",
            "receipt_hash": "receipt-current-floor",
            "q_version": "q-current-floor",
            "sample_hash": "current-sample-floor",
            "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "current-sample-floor",
            "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
            "selection_guard_abstained": False,
            "selection_guard_cell_key": "current-sample-floor",
            "selection_guard_n": 64,
            "global_actuation_identity": "global-current-floor",
            "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
            "global_candidate_id": "candidate-current-floor",
            "global_bin_id": "bin-1",
            "global_universe_witness_identity": "universe-current-floor",
            "global_wealth_witness_identity": "wealth-current-floor",
            "global_selection_epoch_identity": "epoch-current-floor",
            "global_selection_cut_at": "2026-07-13T02:00:00+00:00",
            "global_selection_decision_at": "2026-07-13T02:00:01+00:00",
            "global_jit_book_hash": "book-current-floor",
            "global_jit_venue_book_hash": "venue-book-current-floor",
            "global_jit_book_snapshot_id": "snapshot-current-floor",
            "global_jit_execution_curve_identity": "curve-current-floor",
            "global_target_shares": "1000",
            "global_limit_price": "0.001",
            "global_expected_fill_price_before_fee": "0.001",
            "global_expected_cost_usd": "1.0",
            "global_max_spend_usd": "1.0",
            "global_robust_delta_log_wealth": 0.10,
            "global_robust_ev_usd": 799.0,
            "global_cut_time_win_probability_lcb": 0.80,
            "global_cut_time_loss_probability_ucb": 0.20,
            "global_terminal_win_probability_lcb": 0.80,
            "global_terminal_loss_probability_ucb": 0.20,
            "global_terminal_loss_payoff_usd": "-1.0",
            "global_terminal_win_payoff_usd": "999.0",
            "global_terminal_median_payoff_usd": "999.0",
            "global_terminal_wealth_after_loss_usd": "99.0",
            "global_terminal_wealth_after_win_usd": "1099.0",
            "global_cut_time_expected_value_diagnostic_usd": 799.0,
            "global_expected_value_diagnostic_usd": 799.0,
            "global_expected_value_semantics": (
                "DIAGNOSTIC_EXPECTATION_NOT_REALIZED_GAIN"
            ),
            "global_terminal_payoff_semantics": "BINARY_0_1",
        }
    )
    for legacy_field in (
        "route_id",
        "route_type",
        "delta_u_at_min",
        "optimal_stake_usd",
        "optimal_delta_u",
        "false_edge_rate",
        "direction_law_ok",
        "coherence_allows",
    ):
        economics.pop(legacy_field, None)
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )
    payload["qkernel_execution_economics"] = economics
    ledger.append_event(
        aggregate_id="event-floor:intent-floor",
        event_type="DecisionProofAccepted",
        payload={
            "event_id": "event-floor",
            "final_intent_id": "intent-floor",
            "decision_audit": {"qkernel_execution_economics": economics},
        },
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="unit price out of bounds"):
        ledger.append_event(
            aggregate_id="event-floor:intent-floor",
            event_type="PreSubmitRevalidated",
            payload={
                **payload,
                "event_id": "event-floor",
                "final_intent_id": "intent-floor",
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_recomputed_current_state_marker_cannot_bypass_decision_proof():
    ledger = LiveOrderAggregateLedger(_conn())
    durable_economics = dict(_pre_submit_payload()["qkernel_execution_economics"])
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "decision_audit": {
                "qkernel_execution_economics": durable_economics,
            },
        },
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    payload = _pre_submit_payload(size=1.0, min_order_size=1.0)
    economics = dict(payload["qkernel_execution_economics"])
    economics.update(
        {
            "decision_id": "decision-current-1",
            "receipt_hash": "receipt-current-1",
            "q_version": "q-current-1",
            "sample_hash": "current-sample-1",
            "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "current-sample-1",
            "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
            "selection_guard_abstained": False,
            "selection_guard_cell_key": "current-sample-1",
            "selection_guard_n": 64,
            "global_actuation_identity": "forged-global-current-1",
            "global_optimum_semantics": "CUT_TIME_GLOBAL_OPTIMUM",
            "global_candidate_id": "candidate-current-1",
            "global_bin_id": "bin-1",
            "global_universe_witness_identity": "universe-current-1",
            "global_wealth_witness_identity": "wealth-current-1",
            "global_selection_epoch_identity": "epoch-current-1",
            "global_selection_cut_at": "2026-07-13T02:00:00+00:00",
            "global_selection_decision_at": "2026-07-13T02:00:01+00:00",
            "global_jit_book_hash": "book-current-1",
            "global_jit_venue_book_hash": "venue-book-current-1",
            "global_jit_book_snapshot_id": "snapshot-current-1",
            "global_jit_execution_curve_identity": "curve-current-1",
            "global_target_shares": "1",
            "global_limit_price": "0.40",
            "global_expected_fill_price_before_fee": "0.40",
            "global_expected_cost_usd": "0.40",
            "global_max_spend_usd": "0.40",
            "global_robust_delta_log_wealth": 0.001,
            "global_robust_ev_usd": 0.20,
            "global_cut_time_win_probability_lcb": 0.60,
            "global_cut_time_loss_probability_ucb": 0.40,
            "global_terminal_win_probability_lcb": 0.60,
            "global_terminal_loss_probability_ucb": 0.40,
            "global_terminal_loss_payoff_usd": "-0.40",
            "global_terminal_win_payoff_usd": "0.60",
            "global_terminal_median_payoff_usd": "0.60",
            "global_terminal_wealth_after_loss_usd": "99.60",
            "global_terminal_wealth_after_win_usd": "100.60",
            "global_cut_time_expected_value_diagnostic_usd": 0.20,
            "global_expected_value_diagnostic_usd": 0.20,
            "global_expected_value_semantics": (
                "DIAGNOSTIC_EXPECTATION_NOT_REALIZED_GAIN"
            ),
            "global_terminal_payoff_semantics": "BINARY_0_1",
        }
    )
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )
    payload["qkernel_execution_economics"] = economics

    with pytest.raises(LiveOrderAggregateError, match="expected profit below"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=payload,
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_low_price_yes_below_live_floor():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="entry price below strategy floor"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                q_live=0.118809820736935,
                q_lcb_5pct=0.100708440505795,
                limit_price=0.07,
                expected_edge=0.0307084405057945,
                size=50.0,
                min_entry_price=0.10,
                min_expected_profit_usd=1.0,
                min_submit_edge_density=0.05,
                current_best_bid=0.06,
                current_best_ask=0.08,
                qkernel_execution_economics={
                    "source": "qkernel_spine",
                    "route_id": "DIRECT_YES:b23@proof",
                    "route_type": "direct",
                    "side": "YES",
                "payoff_q_point": 0.118809820736935,
                "payoff_q_lcb": 0.100708440505795,
                "cost": 0.07,
                "edge_lcb": 0.0307084405057945,
                "delta_u_at_min": 0.00009152233738979263,
                "optimal_stake_usd": 1.4412832709285736,
                "optimal_delta_u": 0.000411243383550307,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.100708440505795,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_legacy_qkernel_price_below_absolute_price_floor():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="unit price out of bounds"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                strategy_key="center_buy",
                q_live=0.12180248510788458,
                q_lcb_5pct=0.06052567908958011,
                limit_price=0.031,
                expected_edge=0.020510409830349664,
                size=46.49,
                min_entry_price=0.02,
                min_expected_profit_usd=0.05,
                min_submit_edge_density=0.02,
                current_best_bid=0.02,
                current_best_ask=0.04,
                qkernel_execution_economics={
                    **_pre_submit_payload()["qkernel_execution_economics"],
                    "route_id": "DIRECT_YES:b34@proof",
                    "side": "YES",
                    "payoff_q_point": 0.12180248510788458,
                    "payoff_q_lcb": 0.06052567908958011,
                    "cost": 0.04001526925923045,
                    "edge_lcb": 0.020510409830349664,
                    "delta_u_at_min": 0.00009152233738979263,
                    "optimal_stake_usd": 0.01,
                    "optimal_delta_u": 0.0006333828915951036,
                    "selection_guard_q_safe": 0.06052567908958011,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_qkernel_false_edge_rate_above_live_alpha():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    economics = {
        **_pre_submit_payload()["qkernel_execution_economics"],
        "false_edge_rate": 0.50,
    }

    with pytest.raises(LiveOrderAggregateError, match="qkernel false_edge_rate blocks"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                expected_edge=0.20,
                qkernel_execution_economics=economics,
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_nonpositive_qkernel_delta_u_at_min():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    economics = {
        **_pre_submit_payload()["qkernel_execution_economics"],
        "delta_u_at_min": -0.01,
    }

    with pytest.raises(LiveOrderAggregateError, match="delta_u_at_min"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(qkernel_execution_economics=economics),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_accepts_center_buy_yes_at_absolute_floor():
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
        event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                strategy_key="center_buy",
                q_live=0.35,
                q_lcb_5pct=0.25,
                limit_price=0.05,
                expected_edge=0.20,
                size=30.0,
            min_entry_price=0.05,
            min_expected_profit_usd=1.0,
            min_submit_edge_density=0.05,
            current_best_bid=0.04,
            current_best_ask=0.05,
            qkernel_execution_economics={
                "source": "qkernel_spine",
                    "route_id": "DIRECT_YES:b24@proof",
                    "route_type": "direct",
                    "side": "YES",
                    "payoff_q_point": 0.35,
                    "payoff_q_lcb": 0.25,
                    "cost": 0.05,
                    "edge_lcb": 0.20,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 30.0,
                "optimal_delta_u": 0.01,
                "false_edge_rate": 0.01,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.25,
                },
            ),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )


def test_pre_submit_rejects_direct_qkernel_yes_below_absolute_floor():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="unit price out of bounds"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                strategy_key="center_buy",
                q_live=0.82,
                q_lcb_5pct=0.72,
                limit_price=0.006,
                expected_edge=0.714,
                size=1497.78,
                min_entry_price=0.10,
                min_expected_profit_usd=1.0,
                min_submit_edge_density=0.05,
                current_best_bid=0.001,
                current_best_ask=0.007,
                qkernel_execution_economics={
                    "source": "qkernel_spine",
                    "route_id": "DIRECT_YES:b20@proof",
                    "route_type": "direct",
                    "side": "YES",
                    "payoff_q_point": 0.82,
                    "payoff_q_lcb": 0.72,
                    "cost": 0.006,
                    "edge_lcb": 0.714,
                    "delta_u_at_min": 0.01,
                    "optimal_stake_usd": 1497.78,
                    "optimal_delta_u": 0.01,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.72,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_accepts_day0_observation_authority_with_qkernel():
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
        event_type="PreSubmitRevalidated",
        payload=_day0_pre_submit_payload(),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None
    assert pre_submit.payload["event_type"] == "DAY0_EXTREME_UPDATED"
    assert pre_submit.payload["qkernel_execution_economics"]["source"] == "qkernel_spine"
    assert pre_submit.payload["_edli_q_source"] == "day0_remaining_day"


def test_pre_submit_accepts_degenerate_day0_remaining_window_guarded_probability():
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
        event_type="PreSubmitRevalidated",
        payload=_day0_pre_submit_payload(
            q_live=0.60,
            q_lcb_5pct=0.60,
            qkernel_execution_economics={
                **_day0_qkernel_economics(),
                "payoff_q_point": 0.60,
                "payoff_q_lcb": 0.60,
                "cost": 0.40,
                "edge_lcb": 0.20,
                "selection_guard_q_safe": 0.60,
            },
        ),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None


def test_pre_submit_rejects_degenerate_day0_inert_pass_through_probability():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="degenerate with q_live"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(
                q_live=0.60,
                q_lcb_5pct=0.60,
                qkernel_execution_economics={
                    **_day0_qkernel_economics(),
                    "payoff_q_point": 0.60,
                    "payoff_q_lcb": 0.60,
                    "cost": 0.40,
                    "edge_lcb": 0.20,
                    "selection_guard_q_safe": 0.60,
                    "q_lcb_guard_basis": "INERT",
                    "q_lcb_guard_cell_key": "high|L1|YES|modal|qb12|coarse_global",
                    "selection_guard_basis": "INERT",
                    "selection_guard_cell_key": "high|L1|YES|modal|qb12|coarse_global",
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_observed_boundary_guard_without_remaining_window():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="DAY0_OBSERVED_BOUNDARY"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(
                q_live=0.90,
                q_lcb_5pct=0.80,
                limit_price=0.44,
                expected_edge=0.25,
                current_best_bid=0.43,
                current_best_ask=0.45,
                qkernel_execution_economics={
                    **_day0_qkernel_economics(),
                    "payoff_q_point": 0.90,
                    "payoff_q_lcb": 0.80,
                    "cost": 0.55,
                    "edge_lcb": 0.25,
                    "q_lcb_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                    "q_lcb_guard_cell_key": "day0_observed_boundary",
                    "selection_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                    "selection_guard_cell_key": "day0_observed_boundary",
                    "selection_guard_q_safe": 0.80,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_accepts_day0_non_hard_fact_candidate_inert_oof_guard():
    """RED-on-revert: a Day0 non-hard-fact candidate the OOF guard passed through INERT

    (no reliability artifact present) must clear the live pre-submit authority gate --
    not just the decision-engine object (see
    test_day0_finite_boundary_yes_routes_through_qlcb_reliability_guard in
    tests/decision/test_family_decision_engine.py, which selects exactly this basis).
    Before this fix, ``assert_live_day0_qkernel_guard_authority`` hard-required
    ``q_lcb_guard_basis``/``selection_guard_basis`` to literally equal
    ``DAY0_REMAINING_DAY_Q_LCB``, so this legitimately-guarded, positive-edge
    candidate was rejected at submit -- fail-closed, but it silently killed ALL Day0
    non-hard-fact trading instead of letting cost decide after deflation.
    """
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
        event_type="PreSubmitRevalidated",
        payload=_day0_pre_submit_payload(
            qkernel_execution_economics={
                **_day0_qkernel_economics(),
                "q_lcb_guard_basis": "INERT",
                "q_lcb_guard_cell_key": "high|L1|YES|modal|qb12|coarse_global",
                "selection_guard_basis": "INERT",
                "selection_guard_cell_key": "high|L1|YES|modal|qb12|coarse_global",
            },
        ),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None
    economics = pre_submit.payload["qkernel_execution_economics"]
    assert economics["q_lcb_guard_basis"] == "INERT"
    assert economics["selection_guard_basis"] == "INERT"


def test_pre_submit_accepts_day0_non_hard_fact_candidate_licensed_oof_deflation():
    """RED-on-revert: a Day0 non-hard-fact candidate the OOF guard deflated-but-cleared

    cost (a real reliability cell licensed it, ``OOF_WILSON_95``) must clear the live
    pre-submit authority gate. Before this fix this basis was rejected exactly like the
    INERT case above -- any basis other than the literal Day0 hard-fact stamp failed.
    """
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
        event_type="PreSubmitRevalidated",
        payload=_day0_pre_submit_payload(
            qkernel_execution_economics={
                **_day0_qkernel_economics(),
                "q_lcb_guard_basis": "OOF_WILSON_95",
                "q_lcb_guard_cell_key": "high|L1|YES|modal|qb10|coarse_global",
                "selection_guard_basis": "OOF_WILSON_95",
                "selection_guard_cell_key": "high|L1|YES|modal|qb10|coarse_global",
            },
        ),
        occurred_at=NOW,
        source_authority="engine_adapter",
    )

    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None
    economics = pre_submit.payload["qkernel_execution_economics"]
    assert economics["q_lcb_guard_basis"] == "OOF_WILSON_95"
    assert economics["selection_guard_basis"] == "OOF_WILSON_95"


def test_pre_submit_rejects_day0_non_hard_fact_candidate_abstained_by_oof_reliability():
    """A Day0 non-hard-fact candidate the OOF guard could not grade (no cell -- an

    active artifact abstain, ``OOF_WILSON_95_MISSING_CELL``) must still be rejected at
    the live pre-submit authority gate -- caught here by the generic (Day0-agnostic)
    ``selection_guard_abstained`` check that runs before the Day0-specific authority
    function even executes. Widening the Day0 basis check to admit legitimate OOF
    guard verdicts must not also admit an abstained one: this basis always pairs with
    ``abstained=True`` in the guard's own verdict construction and is deliberately
    excluded from the accepted-basis set, so it would fail the Day0-specific check too
    if it were ever reached.
    """
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="selection_guard_abstained"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(
                qkernel_execution_economics={
                    **_day0_qkernel_economics(),
                    "q_lcb_guard_basis": "OOF_WILSON_95_MISSING_CELL",
                    "q_lcb_guard_abstained": True,
                    "q_lcb_guard_cell_key": "high|L1|YES|modal|qb19|coarse_global",
                    "selection_guard_basis": "OOF_WILSON_95_MISSING_CELL",
                    "selection_guard_abstained": True,
                    "selection_guard_cell_key": "high|L1|YES|modal|qb19|coarse_global",
                    "selection_guard_q_safe": 0.0,
                    "edge_lcb": -0.60,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_missing_remaining_window_probability_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="remaining-window probability authority required"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(
                day0_probability_authority=None,
                _edli_q_source=None,
                _edli_day0_q_mode=None,
                _edli_day0_remaining_models=None,
                _edli_day0_lcb_transform=None,
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_hard_fact_probability_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="hard-fact calibration"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(
                day0_probability_authority={
                    **_day0_probability_authority(),
                    "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_missing_observation_authority():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="day0 observation authority required"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(live_authority_status=None),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_day0_missing_qkernel_economics():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="requires qkernel_execution_economics"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_day0_pre_submit_payload(qkernel_execution_economics=None),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_pre_submit_rejects_jeddah_qkernel_payoff_mismatched_submit_lcb():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="payoff_q_lcb mismatches submit q_lcb_5pct"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                side="BUY",
                direction="buy_no",
                token_id="token-no",
                q_live=0.986261171798223,
                q_lcb_5pct=0.986261171798223,
                limit_price=0.95,
                expected_edge=0.005,
                size=200.0,
                min_submit_edge_density=0.0,
                qkernel_execution_economics={
                    "source": "qkernel_spine",
                    "route_id": "DIRECT_NO:b24@proof",
                    "side": "NO",
                    "payoff_q_point": 0.986261171798223,
                    "payoff_q_lcb": 0.998678563135879,
                    "cost": 0.95,
                    "edge_lcb": 0.048678563135879,
                    "delta_u_at_min": 0.001,
                    "optimal_stake_usd": 200.0,
                    "optimal_delta_u": 0.001,
                    "false_edge_rate": 0.01,
                    "direction_law_ok": True,
                    "coherence_allows": True,
                    "selection_guard_basis": "SELECTION_BETA_95",
                    "selection_guard_abstained": False,
                    "selection_guard_q_safe": 0.998678563135879,
                },
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
            )


def test_pre_submit_rejects_jeddah_micro_edge_density_even_when_positive_edge():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
    )

    with pytest.raises(LiveOrderAggregateError, match="submit edge density below strategy floor"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="PreSubmitRevalidated",
            payload=_pre_submit_payload(
                side="BUY",
                direction="buy_no",
                token_id="token-no",
                q_live=0.96,
                q_lcb_5pct=0.956,
                limit_price=0.95,
                size=21.99,
                expected_edge=0.005,
                min_expected_profit_usd=0.05,
                min_submit_edge_density=0.02,
            ),
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_execution_command_final_intent_must_match_pre_submit_revalidation():
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
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(final_intent_id="intent-1"),
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
    pre_submit = ledger.latest_event_of_type("event-1:intent-1", "PreSubmitRevalidated")
    assert pre_submit is not None

    with pytest.raises(LiveOrderAggregateError, match="final_intent_id must match"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": "event-1",
                "final_intent_id": "intent-2",
                "execution_command_id": "cmd-1",
                "pre_submit_event_hash": pre_submit.event_hash,
                "live_cap_reserved_event_hash": live_cap.event_hash,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


def test_execution_command_binds_pre_submit_and_live_cap_event_hashes():
    ledger = LiveOrderAggregateLedger(_conn())
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},
        occurred_at=NOW,
        source_authority="decision_kernel",
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
        payload={"event_id": "event-1", "final_intent_id": "intent-2", "usage_id": "usage-1"},
        occurred_at=NOW,
        source_authority="live_cap_ledger",
    )

    with pytest.raises(LiveOrderAggregateError, match="live cap reservation"):
        ledger.append_event(
            aggregate_id="event-1:intent-1",
            event_type="ExecutionCommandCreated",
            payload={
                "event_id": "event-1",
                "final_intent_id": "intent-1",
                "execution_command_id": "cmd-1",
                "pre_submit_event_hash": pre_submit.event_hash,
                "live_cap_reserved_event_hash": live_cap.event_hash,
            },
            occurred_at=NOW,
            source_authority="engine_adapter",
        )


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


def _pre_submit_payload(**overrides):
    payload = {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "condition_id": "condition-1",
        "token_id": "token-yes",
        "side": "BUY",
        "direction": "buy_yes",
        "order_type": "LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "checked_at": "2026-05-25T18:00:00+00:00",
        "quote_seen_at": "2026-05-25T17:59:59.950000+00:00",
        "quote_age_ms": 50,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash-1",
        "current_best_bid": 0.41,
        "current_best_ask": 0.43,
        "limit_price": 0.40,
        "size": 10.0,
        "q_live": 0.70,
        "q_lcb_5pct": 0.60,
        "expected_edge": 0.10,
        "selection_authority_applied": "qkernel_spine",
        "min_entry_price": 0.10,
        "min_expected_profit_usd": 1.0,
        "min_submit_edge_density": 0.05,
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
        "book_captured_at": "2026-05-25T17:59:59.950000+00:00",
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": "2026-05-25T18:00:00+00:00",
        "user_ws_authority_id": "ws_gap_guard",
        "user_ws_checked_at": "2026-05-25T18:00:00+00:00",
        "venue_connectivity_authority_id": "polymarket_public_orderbook",
        "venue_connectivity_checked_at": "2026-05-25T18:00:00+00:00",
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": "2026-05-25T18:00:00+00:00",
        "expected_edge_source_certificate_hash": "actionable-hash-1",
        "cost_basis_source_certificate_hash": "cost-hash-1",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "route_id": "DIRECT_YES:b20@proof",
            "route_type": "direct",
            "side": "YES",
            "payoff_q_point": 0.70,
            "payoff_q_lcb": 0.60,
            "cost": 0.40,
            "edge_lcb": 0.20,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 10.0,
            "optimal_delta_u": 0.01,
            "false_edge_rate": 0.02,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.60,
        },
    }
    payload.update(overrides)
    return payload


def _day0_lcb_transform(condition_id: str = "condition-1", q_lcb: float = 0.60):
    return {
        "yes_lcb_by_condition": {condition_id: q_lcb},
        "no_lcb_by_condition": {condition_id: 0.20},
        "mask": [1.0],
        "absorbing_yes_conditions": [],
        "absorbing_no_conditions": [],
        "staleness_suppressed_conditions": [],
        "immature_finite_yes_suppressed_conditions": [],
        "day0_exit_authority_status": "mature",
        "day0_exit_authority_reason": "day0_high_extreme_post_peak",
        "rounded_extreme": 20.0,
        "metric": "high",
    }


def _day0_probability_authority(
    condition_id: str = "condition-1",
    q_lcb: float = 0.60,
    *,
    remaining_models: int | None = 3,
):
    payload = {
        "q_source": "day0_remaining_day",
        "q_mode": "remaining_day",
        "remaining_model_names": ["ecmwf", "gfs", "icon"],
        "remaining_source_cycle_time_utc": "2026-05-25T12:00:00+00:00",
        "remaining_capture_times_utc": ["2026-05-25T12:20:00+00:00"],
        "exit_authority_status": "mature",
        "exit_authority_reason": "day0_high_extreme_post_peak",
        "observed_extreme_native": 20.0,
        "rounded_value": 20,
        "observation_time": "2026-05-25T17:30:00+00:00",
        "observation_available_at": "2026-05-25T17:35:00+00:00",
        "lcb_transform": _day0_lcb_transform(condition_id, q_lcb),
    }
    if remaining_models is not None:
        payload["remaining_models"] = remaining_models
    return payload

def _day0_pre_submit_payload(**overrides):
    condition_id = str(overrides.get("condition_id") or "condition-1")
    q_live = float(overrides.get("q_live") or 0.70)
    q_lcb = float(overrides.get("q_lcb_5pct") or 0.60)
    remaining_models = overrides.pop("remaining_models", 3)
    day0_probability = _day0_probability_authority(
        condition_id,
        q_lcb,
        remaining_models=remaining_models,
    )
    payload = _pre_submit_payload(
        q_live=q_live,
        q_lcb_5pct=q_lcb,
        qkernel_execution_economics=_day0_qkernel_economics(q_live=q_live, q_lcb=q_lcb),
        event_type="DAY0_EXTREME_UPDATED",
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
        raw_value=20.0,
        rounded_value=20,
        high_so_far=20.0,
        observation_time="2026-05-25T17:30:00+00:00",
        observation_available_at="2026-05-25T17:35:00+00:00",
        day0_probability_authority=day0_probability,
        _edli_q_source="day0_remaining_day",
        _edli_day0_q_mode="remaining_day",
        _edli_day0_remaining_models=remaining_models,
        _edli_day0_remaining_model_names=["ecmwf", "gfs", "icon"],
        _edli_day0_remaining_source_cycle_time_utc="2026-05-25T12:00:00+00:00",
        _edli_day0_remaining_capture_times_utc=["2026-05-25T12:20:00+00:00"],
        _edli_day0_exit_authority_status="mature",
        _edli_day0_exit_authority_reason="day0_high_extreme_post_peak",
        _edli_day0_lcb_transform=day0_probability["lcb_transform"],
    )
    payload.update(overrides)
    return payload


def _day0_qkernel_economics(*, q_live: float = 0.70, q_lcb: float = 0.60) -> dict:
    economics = dict(_pre_submit_payload()["qkernel_execution_economics"])
    economics.update(
        {
            "payoff_q_point": q_live,
            "payoff_q_lcb": q_lcb,
            "edge_lcb": q_lcb - float(economics["cost"]),
            "q_lcb_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "day0_remaining_day_q_lcb",
            "selection_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "selection_guard_abstained": False,
            "selection_guard_cell_key": "day0_remaining_day_q_lcb",
            "selection_guard_n": 0,
            "selection_guard_q_safe": q_lcb,
        }
    )
    return economics


def _seed_command_with_submit_attempt(ledger: LiveOrderAggregateLedger) -> None:
    _seed_command_without_submit_attempt(ledger)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="VenueSubmitAttempted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1", "execution_command_id": "cmd-1"},
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
            "execution_command_id": "cmd-1",
            "pre_submit_event_hash": pre_submit.event_hash,
            "live_cap_reserved_event_hash": live_cap.event_hash,
        },
        occurred_at=NOW,
        source_authority="engine_adapter",
    )


# --------------------------------------------------------------------------- #
# H2_E2E (REAUDIT_0_1.md §2/§4): the projection's typed posterior trace is
# populated from the REAL DecisionProofAccepted event payload (the decision_audit
# block event_reactor_adapter.py:2818-2819 writes), via the production
# rebuild_projection path — NOT a manual column write. Relationship test: when
# the aggregate's DecisionProofAccepted payload carries a decision_audit posterior
# trace, the projection row must reconstruct it WITHOUT JSON_EXTRACT.
# --------------------------------------------------------------------------- #

def _decision_proof_payload_with_audit(*, posterior_id, probability_authority):
    """Mirror the production DecisionProofAccepted payload shape (decision_audit
    block carries the receipt's posterior trace — see _live_decision_audit_payload
    in event_reactor_adapter.py)."""
    return {
        "event_id": "event-1",
        "final_intent_id": "intent-1",
        "no_submit_certificate_count": 3,
        "no_submit_receipt_event_id": "evt-receipt-1",
        "decision_audit": {
            "schema": "edli_live_decision_audit_v1",
            "event_id": "event-1",
            "final_intent_id": "intent-1",
            "posterior_id": posterior_id,
            "probability_authority": probability_authority,
            "q_lcb_calibration_source": "FORECAST_BOOTSTRAP",
        },
    }


def test_projection_populates_posterior_trace_from_decision_audit_payload():
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    # Production append → rebuild_projection runs inside append_event. The
    # posterior trace is read from the decision_audit payload, not injected.
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload=_decision_proof_payload_with_audit(
            posterior_id=4242, probability_authority="replacement_0_1"
        ),
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    # Reconstruct the live-order aggregate -> posterior link in SQL (typed
    # columns, NO JSON_EXTRACT).
    row = conn.execute(
        """
        SELECT posterior_id, probability_authority
        FROM edli_live_order_projection
        WHERE aggregate_id = ?
          AND probability_authority = 'replacement_0_1'
          AND posterior_id IS NOT NULL
        """,
        ("event-1:intent-1",),
    ).fetchone()
    assert row is not None, (
        "edli_live_order_projection.posterior_id must be populated by the "
        "production rebuild from the decision_audit payload — dead-column antibody"
    )
    assert row["posterior_id"] == 4242
    assert row["probability_authority"] == "replacement_0_1"


def test_projection_posterior_trace_is_sticky_across_later_events():
    """COALESCE proof: a later event whose payload lacks a decision_audit block
    (e.g. a reconcile re-projection) must NOT clear the posterior link set by
    DecisionProofAccepted."""
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload=_decision_proof_payload_with_audit(
            posterior_id=777, probability_authority="replacement_0_1"
        ),
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="PreSubmitRevalidated",
        payload=_pre_submit_payload(),  # no decision_audit block
        occurred_at=NOW,
        source_authority="engine_adapter",
    )
    row = conn.execute(
        "SELECT posterior_id, probability_authority FROM edli_live_order_projection WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    ).fetchone()
    assert row["posterior_id"] == 777
    assert row["probability_authority"] == "replacement_0_1"


def test_projection_posterior_trace_null_for_canonical_order():
    """Observability only: a canonical order (no decision_audit posterior block)
    leaves the typed columns NULL — never changes order state, excluded from the
    replacement_0_1 query."""
    conn = _conn()
    ledger = LiveOrderAggregateLedger(conn)
    ledger.append_event(
        aggregate_id="event-1:intent-1",
        event_type="DecisionProofAccepted",
        payload={"event_id": "event-1", "final_intent_id": "intent-1"},  # no audit
        occurred_at=NOW,
        source_authority="decision_kernel",
    )
    row = conn.execute(
        "SELECT posterior_id, probability_authority FROM edli_live_order_projection WHERE aggregate_id = ?",
        ("event-1:intent-1",),
    ).fetchone()
    assert row["posterior_id"] is None
    assert row["probability_authority"] is None
    excluded = conn.execute(
        "SELECT * FROM edli_live_order_projection WHERE probability_authority = 'replacement_0_1'"
    ).fetchall()
    assert excluded == []


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
