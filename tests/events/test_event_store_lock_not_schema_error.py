# Created: 2026-05-31
# Last reused/audited: 2026-05-31
# Authority basis: EDLI live-canary reactor crash 2026-05-31 — transient world-DB
#   "database is locked" was mislabeled as EventStoreSchemaError("table missing"),
#   crashing every reactor cycle (edli_event_reactor status=FAILED). Fix in
#   src/events/event_store.py distinguishes a GENUINE schema fault ("no such table")
#   from TRANSIENT WAL multi-writer contention ("database is locked"/"busy").
#   See /tmp/reactor_crash_fix_2026_05_31.md.
"""Relationship test: EventStore.insert_or_ignore must NOT convert a transient
write-lock on an EXISTING table into a (fatal-looking) schema error.

The cross-module invariant under test: the EventStore (writer) and the reactor
cycle (caller) communicate failure *category* through the exception type.

  - A locked DB whose ``opportunity_events`` table EXISTS is a TRANSIENT,
    retryable contention event → must surface as ``sqlite3.OperationalError``
    so the reactor's fail-soft boundary skips this cycle's emit and survives.
  - A genuinely MISSING table is a permanent schema fault → must surface as
    ``EventStoreSchemaError`` so the caller fails loudly.

Pre-fix this distinction was lost: every ``OperationalError`` (including a mere
lock) was re-wrapped as ``EventStoreSchemaError("table missing")``, which
propagated uncaught and killed the whole reactor cycle.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.events.event_store import EventStore, EventStoreSchemaError
from src.events.opportunity_event import make_opportunity_event, ForecastSnapshotReadyPayload
from src.state.db import init_schema


def _payload() -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-31",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id="snap-lock",
        snapshot_hash="snap-lock",
        captured_at="2026-05-31T04:10:00+00:00",
        available_at="2026-05-31T04:15:00+00:00",
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
        entity_key="Chicago|2026-05-31|high|snap-lock",
        source="forecast",
        observed_at="2026-05-31T04:10:00+00:00",
        available_at="2026-05-31T04:15:00+00:00",
        received_at="2026-05-31T04:16:00+00:00",
        causal_snapshot_id="snap-lock",
        payload=_payload(),
        priority=0,
    )


def test_locked_existing_table_raises_operational_not_schema_error(tmp_path):
    """A locked DB whose opportunity_events table EXISTS must raise the raw
    transient OperationalError, NOT EventStoreSchemaError('table missing')."""
    db = tmp_path / "world.db"
    # Build a full world schema so the table genuinely exists.
    seed = sqlite3.connect(str(db))
    init_schema(seed)
    seed.commit()
    assert seed.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='opportunity_events'"
    ).fetchone() is not None, "fixture must have the table present"
    seed.close()

    # Hold an exclusive write lock from a second connection.
    locker = sqlite3.connect(str(db), timeout=0)
    locker.execute("PRAGMA journal_mode=WAL")
    locker.execute("BEGIN EXCLUSIVE")
    try:
        store_conn = sqlite3.connect(str(db), timeout=0)
        store_conn.execute("PRAGMA journal_mode=WAL")
        store_conn.execute("PRAGMA busy_timeout=0")  # immediate 'database is locked'
        store = EventStore(store_conn)
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            store.insert_or_ignore(_event())
        assert "lock" in str(exc_info.value).lower()
        # And it must NOT be the schema error subclass / message.
        assert not isinstance(exc_info.value, EventStoreSchemaError)
        store_conn.close()
    finally:
        locker.close()


def test_missing_table_still_raises_schema_error(tmp_path):
    """Genuine missing-table fault must STILL raise EventStoreSchemaError so the
    caller fails loudly (the fix must not swallow real schema faults)."""
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")  # no init_schema → no event tables
    store = EventStore(conn)
    with pytest.raises(EventStoreSchemaError):
        store.insert_or_ignore(_event())
    conn.close()
