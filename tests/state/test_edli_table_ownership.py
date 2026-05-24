# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §5 database ownership acceptance A36-A38.
from __future__ import annotations

import sqlite3

import pytest


EDLI_WORLD_TABLES = {
    "opportunity_events",
    "opportunity_event_processing",
    "event_dead_letters",
    "execution_feasibility_evidence",
    "no_trade_regret_events",
    "edli_live_cap_usage",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def test_world_conn_has_edli_tables_after_init():
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    assert EDLI_WORLD_TABLES <= _table_names(conn)


def test_trade_conn_does_not_silently_write_world_event_tables():
    from src.state.db import init_schema_trade_only

    conn = sqlite3.connect(":memory:")
    init_schema_trade_only(conn)
    assert EDLI_WORLD_TABLES.isdisjoint(_table_names(conn))


def test_db_table_ownership_registers_edli_tables():
    from src.state.table_registry import DBIdentity, tables_for

    assert EDLI_WORLD_TABLES <= tables_for(DBIdentity.WORLD)
    assert EDLI_WORLD_TABLES.isdisjoint(tables_for(DBIdentity.TRADE))


def test_schema_version_check_accepts_edli_bump():
    from src.state.db import SCHEMA_VERSION

    assert SCHEMA_VERSION >= 36


def test_no_cross_db_fk():
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    for table_name in EDLI_WORLD_TABLES:
        assert conn.execute(f"PRAGMA foreign_key_list({table_name})").fetchall() == []


def test_opportunity_events_append_only():
    from src.events.event_store import EventStore
    from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    payload = ForecastSnapshotReadyPayload(
        city="Chicago",
        target_date="2026-05-24",
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id="snap-1",
        snapshot_hash="hash-1",
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
    event = make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key="Chicago|2026-05-24|high",
        source="forecast",
        observed_at="2026-05-24T04:10:00+00:00",
        available_at="2026-05-24T04:15:00+00:00",
        received_at="2026-05-24T04:16:00+00:00",
        causal_snapshot_id="snap-1",
        payload=payload,
    )
    EventStore(conn).insert_or_ignore(event)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE opportunity_events SET priority = 99 WHERE event_id = ?", (event.event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM opportunity_events WHERE event_id = ?", (event.event_id,))
