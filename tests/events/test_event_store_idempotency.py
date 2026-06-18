# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §7 EventStore acceptance A01-A04.
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from src.events.event_store import EventStore, EventStoreSchemaError
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.state.db import init_schema


def _payload(
    snapshot_id: str = "snap-1",
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-24",
    metric: str = "high",
) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
        metric=metric,
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


def _fsr_entity_event(
    entity_key: str,
    snapshot_id: str,
    available_at: str,
    received_at: str,
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-24",
    metric: str = "high",
    source_run_completeness_status: str = "COMPLETE",
    coverage_completeness_status: str | None = None,
    coverage_readiness_status: str | None = None,
):
    payload = _payload(
        snapshot_id,
        city=city,
        target_date=target_date,
        metric=metric,
    )
    if source_run_completeness_status != "COMPLETE":
        payload = dataclasses.replace(
            payload,
            completeness_status=coverage_completeness_status or "PARTIAL_ALLOWED",
            source_run_completeness_status=source_run_completeness_status,
            coverage_completeness_status=coverage_completeness_status or source_run_completeness_status,
            coverage_readiness_status=coverage_readiness_status or "NOT_ELIGIBLE",
        )
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=entity_key,
        source="cycle",
        observed_at=available_at,
        available_at=available_at,
        received_at=received_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=50,
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


def test_archive_orphan_processing_rows_expires_only_rows_without_event_provenance():
    conn = _world_conn()
    store = EventStore(conn)
    live_event = _event(
        "snap-live",
        0,
        "2026-05-24T04:15:00+00:00",
        "2026-05-24T04:16:00+00:00",
    )
    store.insert_or_ignore(live_event)
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count, updated_at
        ) VALUES (?, ?, 'pending', 2, ?)
        """,
        ("edli_reactor_v1", "missing-event-row", "2026-05-24T04:16:30+00:00"),
    )
    conn.execute(
        """
        INSERT INTO opportunity_event_processing (
            consumer_name, event_id, processing_status, attempt_count, claimed_at, updated_at
        ) VALUES (?, ?, 'processing', 1, ?, ?)
        """,
        (
            "edli_reactor_v1",
            "missing-processing-row",
            "2026-05-24T04:16:30+00:00",
            "2026-05-24T04:16:30+00:00",
        ),
    )

    assert store.archive_orphan_processing_rows(batch_limit=10) == 2

    rows = dict(
        conn.execute(
            """
            SELECT event_id, processing_status || ':' || COALESCE(last_error, '')
              FROM opportunity_event_processing
             WHERE event_id IN (?, ?, ?)
            """,
            (live_event.event_id, "missing-event-row", "missing-processing-row"),
        ).fetchall()
    )
    assert rows[live_event.event_id] == "pending:"
    assert rows["missing-event-row"] == "expired:ORPHAN_EVENT_ROW_MISSING"
    assert rows["missing-processing-row"] == "expired:ORPHAN_EVENT_ROW_MISSING"


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


def test_fetch_pending_prioritizes_fresh_fsr_before_retry_debt_same_target():
    conn = _world_conn()
    store = EventStore(conn)
    old_retry = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-old",
        "snap-old-retry",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
    )
    fresh_redecision = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-new",
        "snap-fresh-redecision",
        "2026-05-24T04:20:00+00:00",
        "2026-05-24T04:21:00+00:00",
    )
    store.insert_or_ignore(old_retry)
    store.insert_or_ignore(fresh_redecision)
    conn.execute(
        """
        UPDATE opportunity_event_processing
           SET attempt_count = 3
         WHERE event_id = ?
        """,
        (old_retry.event_id,),
    )

    ordered = store.fetch_pending(
        decision_time="2026-05-24T05:00:00+00:00",
        limit=2,
    )

    assert [event.event_id for event in ordered] == [
        fresh_redecision.event_id,
        old_retry.event_id,
    ]


def test_archive_superseded_forecast_snapshot_events_keeps_latest_per_family():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    older = _fsr_entity_event(
        entity_key, "snap-old", "2026-05-24T04:00:00+00:00", "2026-05-24T04:01:00+00:00"
    )
    newer = _fsr_entity_event(
        entity_key, "snap-new", "2026-05-24T04:10:00+00:00", "2026-05-24T04:11:00+00:00"
    )
    other = _fsr_entity_event(
        "Denver|2026-05-24|high|source-run-1",
        "snap-other",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
        city="Denver",
    )
    for event in (older, newer, other):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older.event_id] == "expired"
    assert rows[newer.event_id] == "pending"
    assert rows[other.event_id] == "pending"


def test_archive_superseded_forecast_snapshot_events_crosses_source_runs():
    conn = _world_conn()
    store = EventStore(conn)
    older = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-1",
        "snap-run-1",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
    )
    newer = _fsr_entity_event(
        "Chicago|2026-05-24|high|source-run-2",
        "snap-run-2",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
    )
    for event in (older, newer):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older.event_id] == "expired"
    assert rows[newer.event_id] == "pending"


def test_archive_superseded_forecast_snapshot_events_fallback_keeps_entity_keeper():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    older = _fsr_entity_event(
        entity_key,
        "snap-missing-family-old",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
        city="",
    )
    newer = _fsr_entity_event(
        entity_key,
        "snap-missing-family-new",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        city="",
    )
    for event in (older, newer):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older.event_id] == "expired"
    assert rows[newer.event_id] == "pending"


def test_archive_superseded_forecast_snapshot_events_keeps_complete_over_newer_partial():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    complete = _fsr_entity_event(
        entity_key, "snap-complete", "2026-05-24T04:00:00+00:00", "2026-05-24T04:01:00+00:00"
    )
    partial = _fsr_entity_event(
        entity_key,
        "snap-partial",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        source_run_completeness_status="PARTIAL_ALLOWED",
    )
    for event in (complete, partial):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[complete.event_id] == "pending"
    assert rows[partial.event_id] == "expired"


def test_archive_superseded_forecast_snapshot_events_keeps_window_complete_source_partial():
    conn = _world_conn()
    store = EventStore(conn)
    entity_key = "Chicago|2026-05-24|high|source-run-1"
    older_incomplete = _fsr_entity_event(
        entity_key,
        "snap-coverage-partial",
        "2026-05-24T04:00:00+00:00",
        "2026-05-24T04:01:00+00:00",
        source_run_completeness_status="PARTIAL",
        coverage_completeness_status="PARTIAL",
        coverage_readiness_status="NOT_ELIGIBLE",
    )
    window_complete = _fsr_entity_event(
        entity_key,
        "snap-window-complete",
        "2026-05-24T04:10:00+00:00",
        "2026-05-24T04:11:00+00:00",
        source_run_completeness_status="PARTIAL",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    for event in (older_incomplete, window_complete):
        store.insert_or_ignore(event)

    archived = store.archive_superseded_forecast_snapshot_events()

    assert archived == 1
    rows = dict(
        conn.execute(
            "SELECT event_id, processing_status FROM opportunity_event_processing"
        ).fetchall()
    )
    assert rows[older_incomplete.event_id] == "expired"
    assert rows[window_complete.event_id] == "pending"


def test_fetch_pending_prioritizes_day0_hard_fact_over_complete_forecast_backlog():
    from src.events.opportunity_event import Day0ExtremeUpdatedPayload

    conn = _world_conn()
    store = EventStore(conn)
    fsr = _event("snap-ready", 50, "2026-05-24T04:05:00+00:00", "2026-05-24T04:06:00+00:00")
    day0 = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Chicago|2026-05-24|high|82",
        source="day0",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:10:00+00:00",
        received_at="2026-05-24T04:10:00+00:00",
        causal_snapshot_id=None,
        payload=Day0ExtremeUpdatedPayload(
            city="Chicago",
            target_date="2026-05-24",
            metric="high",
            settlement_source="wu_icao_history",
            station_id="KORD",
            observation_time="2026-05-24T04:00:00+00:00",
            observation_available_at="2026-05-24T04:10:00+00:00",
            raw_value=82.0,
            rounded_value=82,
            high_so_far=82.0,
        ),
        priority=20,
    )
    store.insert_or_ignore(fsr)
    store.insert_or_ignore(day0)

    ordered = store.fetch_pending(decision_time="2026-05-24T05:00:00+00:00", limit=2)

    assert [event.event_type for event in ordered] == ["DAY0_EXTREME_UPDATED", "FORECAST_SNAPSHOT_READY"]


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
