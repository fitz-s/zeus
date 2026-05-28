# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §7 EventStore acceptance A01-A04.
from __future__ import annotations

import sqlite3

import pytest

from src.events.event_store import EventStore, EventStoreSchemaError
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.state.db import init_schema


def _payload(snapshot_id: str = "snap-1") -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def _event(snapshot_id: str, priority: int, available_at: str, received_at: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"Chicago|2026-05-24|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=_payload(snapshot_id),
        priority=priority,
    )


def _world_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_insert_or_ignore_duplicate():
    conn = _world_conn()
    store = EventStore(conn)
    event = _event("snap-1", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    assert store.insert_or_ignore(event) is True
    assert store.insert_or_ignore(event) is False
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM opportunity_event_processing").fetchone()[0] == 1


def test_processing_state_separate_from_event_row():
    conn = _world_conn()
    store = EventStore(conn)
    event = _event("snap-1", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    store.insert_or_ignore(event)
    assert store.claim(event.event_id, claimed_at="2026-05-24T04:17:00+00:00") is True
    row = conn.execute(
        "SELECT processing_status, attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()
    assert dict(row) == {"processing_status": "processing", "attempt_count": 1}
    assert conn.execute("SELECT payload_hash FROM opportunity_events WHERE event_id = ?", (event.event_id,)).fetchone()[0] == event.payload_hash


def test_world_conn_required_for_event_tables():
    conn = _world_conn()
    store = EventStore(conn)
    assert store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00") == []


def test_trade_conn_wrong_db_fails_loud():
    conn = sqlite3.connect(":memory:")
    store = EventStore(conn)
    event = _event("snap-1", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    with pytest.raises(EventStoreSchemaError, match="world DB|EDLI event tables"):
        store.insert_or_ignore(event)


def test_replay_order_deterministic():
    conn = _world_conn()
    store = EventStore(conn)
    events = [
        _event("snap-low-priority", 0, "2026-05-24T04:00:00+00:00", "2026-05-24T04:01:00+00:00"),
        _event("snap-newer", 5, "2026-05-24T04:10:00+00:00", "2026-05-24T04:11:00+00:00"),
        _event("snap-older", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00"),
    ]
    for event in events:
        store.insert_or_ignore(event)
    ordered = store.replay_events()
    assert [event.causal_snapshot_id for event in ordered] == [
        "snap-older",
        "snap-newer",
        "snap-low-priority",
    ]
    from src.events.replay import replay_all_events

    assert replay_all_events(store).event_ids == tuple(event.event_id for event in ordered)


def test_pending_fetch_excludes_future_available_at():
    conn = _world_conn()
    store = EventStore(conn)
    future = _event("snap-future", 5, "2026-05-24T06:00:00+00:00", "2026-05-24T04:11:00+00:00")
    ready = _event("snap-ready", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    store.insert_or_ignore(future)
    store.insert_or_ignore(ready)
    ordered = store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00")
    assert [event.causal_snapshot_id for event in ordered] == ["snap-ready"]


def test_pending_fetch_excludes_future_received_at():
    conn = _world_conn()
    store = EventStore(conn)
    future_received = _event("snap-received-future", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T05:30:00+00:00")
    ready = _event("snap-ready", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    store.insert_or_ignore(future_received)
    store.insert_or_ignore(ready)
    ordered = store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00")
    assert [event.causal_snapshot_id for event in ordered] == ["snap-ready"]


def test_stale_processing_claim_is_reclaimed_after_lease():
    conn = _world_conn()
    store = EventStore(conn, processing_lease_seconds=60)
    event = _event("snap-stale", 5, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    store.insert_or_ignore(event)
    assert store.claim(event.event_id, claimed_at="2026-05-24T04:10:00+00:00") is True

    pending = store.fetch_pending(decision_time="2026-05-24T04:12:00+00:00")
    assert [row.event_id for row in pending] == [event.event_id]
    assert store.claim(event.event_id, claimed_at="2026-05-24T04:12:00+00:00") is True
    assert conn.execute(
        "SELECT attempt_count FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0] == 2


def test_dead_letter_writes_separate_evidence_and_terminal_status():
    conn = _world_conn()
    store = EventStore(conn)
    event = _event("snap-bad", 0, "2026-05-24T04:15:00+00:00", "2026-05-24T04:16:00+00:00")
    store.insert_or_ignore(event)
    store.claim(event.event_id, claimed_at="2026-05-24T04:17:00+00:00")

    from src.events.dead_letter import dead_letter_event

    record = dead_letter_event(
        store,
        event,
        failure_stage="SOURCE_TRUTH",
        error_message="source mismatch",
        created_at="2026-05-24T04:18:00+00:00",
    )

    assert record.event_id == event.event_id
    assert conn.execute("SELECT COUNT(*) FROM event_dead_letters").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
        == "dead_letter"
    )
