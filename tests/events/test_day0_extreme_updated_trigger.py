# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §9 Day0 trigger availability and hard-fact gates.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.events.event_writer import EventWriter
from src.events.triggers.day0_extreme_updated import (
    Day0ExtremeUpdatedTrigger,
    authority_row_to_observation,
    build_day0_extreme_updated_event,
    observation_context_to_live_observation,
)
from src.state.db import init_schema


class FakeSettlementSemantics:
    def __init__(self, rounded: int) -> None:
        self.rounded = rounded
        self.calls: list[float] = []

    def round_single(self, value: float) -> int:
        self.calls.append(value)
        return self.rounded


def _observation(**overrides):
    base = {
        "city": "Chicago",
        "target_date": "2026-05-24",
        "metric": "high",
        "settlement_source": "WU",
        "station_id": "KMDW",
        "observation_time": "2026-05-24T18:00:00+00:00",
        "observation_available_at": "2026-05-24T18:07:00+00:00",
        "raw_value": 74.2,
        "high_so_far": 74.2,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "LIVE_AUTHORITY",
        "observation_context_id": "obsctx-1",
    }
    base.update(overrides)
    return base


def test_day0_online_hook_is_trigger_local_not_cycle_runtime_side_effect():
    cycle_runtime_source = Path("src/engine/cycle_runtime.py").read_text()
    trigger_source = Path("src/events/triggers/day0_extreme_updated.py").read_text()
    main_source = Path("src/main.py").read_text()

    assert "_queue_edli_day0_observation_event" not in cycle_runtime_source
    assert "def observation_context_to_live_observation(" in trigger_source
    assert "defaults to OBSERVABILITY_ONLY" in trigger_source
    assert "day0_authority_catchup_scanner_enabled" in main_source


def test_observation_context_live_hook_marks_wu_station_match_live_authority():
    city = SimpleNamespace(
        name="Chicago",
        timezone="America/Chicago",
        settlement_unit="F",
        settlement_source_type="wu_icao",
        wu_station="KMDW",
    )
    context = SimpleNamespace(
        source="wu_api",
        station_id="KMDW:9",
        observation_time="2026-05-24T18:00:00+00:00",
        observation_available_at="2026-05-24T18:07:00+00:00",
        high_so_far=74.2,
        low_so_far=61.0,
        current_temp=73.0,
        unit="F",
        coverage_status="OK",
    )

    observation = observation_context_to_live_observation(
        city=city,
        target_date="2026-05-24",
        metric="high",
        observation=context,
        observation_context_id="ctx-1",
    )

    assert observation["source_match_status"] == "MATCH"
    assert observation["station_match_status"] == "MATCH"
    assert observation["local_date_status"] == "MATCH"
    assert observation["dst_status"] == "UNAMBIGUOUS"
    assert observation["rounding_status"] == "MATCH"
    assert observation["source_authorized_status"] == "AUTHORIZED"
    assert observation["live_authority_status"] == "LIVE_AUTHORITY"
    assert observation["observation_available_at"] == "2026-05-24T18:07:00+00:00"


def test_observation_context_live_hook_blocks_diagnostic_fallback():
    city = SimpleNamespace(
        name="Chicago",
        timezone="America/Chicago",
        settlement_unit="F",
        settlement_source_type="wu_icao",
        wu_station="KMDW",
    )
    context = SimpleNamespace(
        source="openmeteo_hourly",
        station_id="",
        observation_time="2026-05-24T18:00:00+00:00",
        observation_available_at="2026-05-24T18:07:00+00:00",
        high_so_far=74.2,
        low_so_far=61.0,
        current_temp=73.0,
        unit="F",
        coverage_status="DIAGNOSTIC_FALLBACK",
    )

    observation = observation_context_to_live_observation(
        city=city,
        target_date="2026-05-24",
        metric="high",
        observation=context,
    )

    assert observation["source_match_status"] == "MISMATCH"
    assert observation["station_match_status"] == "MISMATCH"
    assert observation["live_authority_status"] == "NON_LIVE_AUTHORITY"


def test_day0_event_uses_observation_available_at():
    sem = FakeSettlementSemantics(74)
    event = build_day0_extreme_updated_event(
        observation=_observation(),
        settlement_semantics=sem,
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        received_at="2026-05-24T18:08:00+00:00",
    )
    assert event.available_at == "2026-05-24T18:07:00+00:00"
    assert sem.calls == [74.2]


def test_observation_available_at_future_blocks():
    sem = FakeSettlementSemantics(74)
    with pytest.raises(ValueError, match="after decision_time"):
        build_day0_extreme_updated_event(
            observation=_observation(observation_available_at="2026-05-24T18:11:00+00:00"),
            settlement_semantics=sem,
            decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
            received_at="2026-05-24T18:08:00+00:00",
        )


def test_trigger_emit_idempotent():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    trigger = Day0ExtremeUpdatedTrigger(EventWriter(conn))
    sem = FakeSettlementSemantics(74)
    first = trigger.emit_from_observation(
        observation=_observation(),
        settlement_semantics=sem,
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        received_at="2026-05-24T18:08:00+00:00",
    )
    second = trigger.emit_from_observation(
        observation=_observation(),
        settlement_semantics=sem,
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        received_at="2026-05-24T18:08:00+00:00",
    )
    assert first.inserted is True
    assert second.duplicate is True
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1


def test_scan_authority_rows_uses_recorded_available_at_and_extreme_changes():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        CREATE TABLE settlement_day_observation_authority (
            authority_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            decision_time_utc TEXT,
            market_phase TEXT,
            source TEXT,
            station_id TEXT,
            observation_time_utc TEXT,
            first_sample_time_utc TEXT,
            last_sample_time_utc TEXT,
            high_so_far REAL,
            low_so_far REAL,
            current_temp REAL,
            sample_count INTEGER,
            coverage_status TEXT,
            freshness_status TEXT,
            local_date_matches_target INTEGER,
            source_authorized_for_settlement INTEGER,
            persisted_surface_available INTEGER,
            payload_json TEXT,
            recorded_at TEXT NOT NULL
        )
        """
    )
    payload = '{"dst_status":"UNAMBIGUOUS","rounding_status":"MATCH"}'
    conn.execute(
        "INSERT INTO settlement_day_observation_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "auth-1", "Chicago", "2026-05-24", "high", "2026-05-24T18:00:00+00:00",
            "day0", "WU", "KMDW", "2026-05-24T18:00:00+00:00", None, None,
            74.2, None, 74.2, 1, "OK", "FRESH", 1, 1, 1, payload,
            "2026-05-24T18:07:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO settlement_day_observation_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "auth-2", "Chicago", "2026-05-24", "high", "2026-05-24T18:05:00+00:00",
            "day0", "WU", "KMDW", "2026-05-24T18:05:00+00:00", None, None,
            75.1, None, 75.1, 1, "OK", "FRESH", 1, 1, 1, payload,
            "2026-05-24T18:12:00+00:00",
        ),
    )
    trigger = Day0ExtremeUpdatedTrigger(EventWriter(conn))

    results = trigger.scan_authority_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(75),
        decision_time=datetime(2026, 5, 24, 18, 15, tzinfo=timezone.utc),
        received_at="2026-05-24T18:13:00+00:00",
    )

    assert len(results) == 2
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 2
    assert "2026-05-24T18:07:00+00:00" in conn.execute(
        "SELECT payload_json FROM opportunity_events ORDER BY available_at LIMIT 1"
    ).fetchone()[0]


def test_day0_scanner_uses_semantics_callable_and_skips_missing_values():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        CREATE TABLE settlement_day_observation_authority (
            authority_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            decision_time_utc TEXT,
            source TEXT,
            station_id TEXT,
            observation_time_utc TEXT,
            high_so_far REAL,
            low_so_far REAL,
            current_temp REAL,
            local_date_matches_target INTEGER,
            source_authorized_for_settlement INTEGER,
            payload_json TEXT,
            recorded_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO settlement_day_observation_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "missing", "Chicago", "2026-05-24", "high", "2026-05-24T18:00:00+00:00",
            "WU", "KMDW", "2026-05-24T18:00:00+00:00", None, None, None, 1, 1,
            "{}", "2026-05-24T18:01:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO settlement_day_observation_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "valid", "Chicago", "2026-05-24", "high", "2026-05-24T18:05:00+00:00",
            "WU", "KMDW", "2026-05-24T18:05:00+00:00", 74.2, None, None, 1, 1,
            '{"dst_status":"UNAMBIGUOUS","rounding_status":"MATCH"}',
            "2026-05-24T18:06:00+00:00",
        ),
    )
    trigger = Day0ExtremeUpdatedTrigger(EventWriter(conn))

    results = trigger.scan_authority_rows(
        observation_conn=conn,
        settlement_semantics=lambda _observation: FakeSettlementSemantics(74),
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        received_at="2026-05-24T18:10:00+00:00",
    )

    assert len(results) == 1
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1


def test_day0_scanner_limit_prioritizes_newest_rows_not_old_duplicates():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        CREATE TABLE settlement_day_observation_authority (
            authority_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            decision_time_utc TEXT,
            source TEXT,
            station_id TEXT,
            observation_time_utc TEXT,
            high_so_far REAL,
            low_so_far REAL,
            current_temp REAL,
            local_date_matches_target INTEGER,
            source_authorized_for_settlement INTEGER,
            payload_json TEXT,
            recorded_at TEXT NOT NULL
        )
        """
    )
    payload = '{"dst_status":"UNAMBIGUOUS","rounding_status":"MATCH"}'
    for authority_id, high, recorded_at in (
        ("old", 70.0, "2026-05-24T17:00:00+00:00"),
        ("new", 75.0, "2026-05-24T18:00:00+00:00"),
    ):
        conn.execute(
            "INSERT INTO settlement_day_observation_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                authority_id, "Chicago", "2026-05-24", "high", recorded_at,
                "WU", "KMDW", recorded_at, high, None, None, 1, 1, payload, recorded_at,
            ),
        )

    results = Day0ExtremeUpdatedTrigger(EventWriter(conn)).scan_authority_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(75),
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        received_at="2026-05-24T18:10:00+00:00",
        limit=1,
    )

    assert len(results) == 1
    stored = conn.execute("SELECT entity_key FROM opportunity_events").fetchone()[0]
    assert "Chicago|2026-05-24|high|KMDW" == stored
    assert "75.0" in conn.execute("SELECT payload_json FROM opportunity_events").fetchone()[0]


def test_scan_observation_instants_rows_emits_live_authority_day0_event():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Paris",
            "2026-06-06",
            "wu_icao_history",
            "Europe/Paris",
            6.0,
            "2026-06-06T06:00:00+02:00",
            "2026-06-06T04:00:00+00:00",
            120,
            1,
            0,
            0,
            "observed",
            14.0,
            14.0,
            12.0,
            "C",
            "LFPB",
            1,
            "2026-06-06T04:15:00+00:00",
            "VERIFIED",
        ),
    )

    results = Day0ExtremeUpdatedTrigger(EventWriter(conn)).scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(14),
        decision_time=datetime(2026, 6, 6, 4, 20, tzinfo=timezone.utc),
        received_at="2026-06-06T04:20:00+00:00",
    )

    assert len(results) == 2
    payloads = [row[0] for row in conn.execute("SELECT payload_json FROM opportunity_events").fetchall()]
    assert {'"metric":"high"', '"metric":"low"'} == {
        '"metric":"high"' if '"metric":"high"' in payload else '"metric":"low"'
        for payload in payloads
    }
    assert all('"event_type":"DAY0_EXTREME_UPDATED"' not in payload for payload in payloads)
    assert all('"live_authority_status":"LIVE_AUTHORITY"' in payload for payload in payloads)
    assert all('"source_authorized_status":"AUTHORIZED"' in payload for payload in payloads)


def test_authority_row_missing_temperature_is_not_observation():
    with pytest.raises(ValueError, match="no high/low/current_temp"):
        authority_row_to_observation(
            {
                "payload_json": "{}",
                "temperature_metric": "high",
                "high_so_far": None,
                "low_so_far": None,
                "current_temp": None,
            }
        )


def test_authority_row_scanner_is_observability_only_not_live_authority():
    observation = authority_row_to_observation(
        {
            "payload_json": '{"dst_status":"UNAMBIGUOUS","rounding_status":"MATCH"}',
            "city": "Chicago",
            "target_date": "2026-05-24",
            "temperature_metric": "high",
            "source": "WU",
            "station_id": "KMDW",
            "observation_time_utc": "2026-05-24T18:00:00+00:00",
            "high_so_far": 74.2,
            "low_so_far": None,
            "current_temp": 74.2,
            "local_date_matches_target": 1,
            "source_authorized_for_settlement": 1,
            "recorded_at": "2026-05-24T18:07:00+00:00",
            "authority_id": "auth-obs-only",
        }
    )

    assert observation["live_authority_status"] == "OBSERVABILITY_ONLY"
    assert observation["source_match_status"] == "UNKNOWN"
    assert observation["station_match_status"] == "UNKNOWN"
    assert observation["metric_match_status"] == "UNKNOWN"
