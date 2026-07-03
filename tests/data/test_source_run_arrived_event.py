# Created: 2026-07-02
# Last reused/audited: 2026-07-02
# Authority basis: order-engine rebuild W0.3 acceptance (docs/operations/current/plans/
#                  order_engine_rebuild_execution_plan_2026-07-02.md) — "event rows appear
#                  on real cycle advance; idempotency key proven by replay test".
"""SOURCE_RUN_ARRIVED emission from the source-clock probe.

Emit-only packet: no consumer reads this event yet (W4 wires the staleness path).
These tests prove (a) a new usable run emits exactly one event with the expected
payload shape, and (b) replaying the probe over the SAME undelivered run (the real
``advance_cursor=False`` live shape — see scripts/source_clock_live_replacement_cycle.py)
emits zero additional rows.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from src.data.openmeteo_model_updates import OpenMeteoModelUpdate, write_model_updates_jsonl
from src.data.source_clock_update_probe import probe_openmeteo_source_clock_updates
from src.events.event_writer import EventWriter
from src.state.db import init_schema


def _writer_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


def _write_ecmwf_update(updates_path) -> None:
    write_model_updates_jsonl(
        updates_path,
        [
            OpenMeteoModelUpdate(
                model="ecmwf_ifs",
                last_run_initialisation_time=datetime(2000, 1, 1, 0, 0, tzinfo=UTC),
                last_run_availability_time=datetime(2000, 1, 1, 4, 0, tzinfo=UTC),
            )
        ],
    )


def test_new_run_detection_emits_exactly_one_event_with_correct_payload(tmp_path) -> None:
    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    _write_ecmwf_update(updates_path)
    conn = _writer_conn()
    writer = EventWriter(conn)

    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
        event_writer=writer,
    )

    assert report.status == "SOURCE_CLOCK_UPDATES_CHANGED"
    assert len(report.emitted_event_ids) == 1
    rows = conn.execute(
        "SELECT event_type, entity_key, source, available_at, payload_json FROM opportunity_events"
    ).fetchall()
    assert len(rows) == 1
    event_type, entity_key, source, available_at, payload_json = rows[0]
    assert event_type == "SOURCE_RUN_ARRIVED"
    assert source == "ecmwf_ifs"
    assert entity_key == "ecmwf_ifs|2000-01-01T00:00:00+00:00"
    # source_publicly_usable_at = run_availability_time + consistency wait, NOT probe wall clock.
    assert available_at != ""
    assert '"source":"ecmwf_ifs"' in payload_json
    assert '"affected_cities"' in payload_json
    assert '"source_cycle_time":"2000-01-01T00:00:00+00:00"' in payload_json


def test_replaying_same_undelivered_run_emits_zero_additional_rows(tmp_path) -> None:
    """Mirrors the real live shape: advance_cursor=False, so the SAME run can be
    reported as usable_changed across multiple probe calls until the cursor is
    externally committed (advance_source_clock_cursor)."""

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    _write_ecmwf_update(updates_path)
    conn = _writer_conn()
    writer = EventWriter(conn)

    first = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
        event_writer=writer,
    )
    second = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
        event_writer=writer,
    )

    assert first.status == "SOURCE_CLOCK_UPDATES_CHANGED"
    assert second.status == "SOURCE_CLOCK_UPDATES_CHANGED"
    assert first.emitted_event_ids == second.emitted_event_ids
    n = conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0]
    assert n == 1, "replaying the probe over the same undelivered run must not stack duplicate rows"


def test_no_event_writer_preserves_prior_behavior(tmp_path) -> None:
    """Backward-compat: existing callers that never pass event_writer see no new writes."""

    updates_path = tmp_path / "updates.jsonl"
    cursor_path = tmp_path / "cursor.json"
    _write_ecmwf_update(updates_path)

    report = probe_openmeteo_source_clock_updates(
        model_updates_path=updates_path,
        cursor_path=cursor_path,
        use_network=False,
        advance_cursor=False,
    )

    assert report.status == "SOURCE_CLOCK_UPDATES_CHANGED"
    assert report.emitted_event_ids == ()
