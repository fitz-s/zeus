# Created: 2026-05-24
# Last reused/audited: 2026-07-28
# Authority basis: EDLI v1 implementation prompt §7 EventWriter single-writer contract.
from __future__ import annotations

import sqlite3

import pytest

from src.events.event_store import EventStoreSchemaError
from src.events.event_writer import EventWriter
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


def _event():
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-1",
        payload=_payload(),
    )


def test_event_writer_reports_duplicate_idempotency_key():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    writer = EventWriter(conn)
    event = _event()

    first = writer.write(event)
    duplicate = writer.write(event)

    assert first.inserted is True
    assert first.duplicate is False
    assert duplicate.inserted is False
    assert duplicate.duplicate is True
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1


def test_event_writer_requires_world_event_tables():
    conn = sqlite3.connect(":memory:")
    writer = EventWriter(conn)

    with pytest.raises(EventStoreSchemaError):
        writer.write(_event())


def test_event_writer_rollback_can_retry_as_pending():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.commit()
    event = _event()

    assert EventWriter(conn).write_many([event])[0].inserted is True
    conn.rollback()

    retry = EventWriter(conn).write_many([event])[0]
    conn.commit()

    assert retry.inserted is True
    assert conn.execute(
        "SELECT processing_status FROM opportunity_event_processing WHERE event_id = ?",
        (event.event_id,),
    ).fetchone()[0] == "pending"
