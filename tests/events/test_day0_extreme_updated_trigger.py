# Created: 2026-05-24
# Last reused/audited: 2026-06-17
# Authority basis: EDLI v1 implementation prompt §9 Day0 trigger availability and hard-fact gates.
from __future__ import annotations

import json
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
        "live_authority_status": "live",
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
    assert "defaults to blocked" in trigger_source
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
    assert observation["live_authority_status"] == "live"
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
    assert observation["live_authority_status"] == "blocked"


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
    insert_sql = """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority,
            data_version, provenance_json, training_allowed, causality_status, source_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    conn.execute(
        insert_sql,
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
            "v1.wu-native",
            '{"source_url":"redacted","station_id":"LFPB"}',
            1,
            "OK",
            "historical_hourly",
        ),
    )
    conn.execute(
        insert_sql,
        (
            "Paris",
            "2026-06-06",
            "wu_icao_history",
            "Europe/Paris",
            7.0,
            "2026-06-06T07:00:00+02:00",
            "2026-06-06T05:00:00+00:00",
            120,
            1,
            0,
            0,
            "observed",
            13.0,
            13.0,
            11.0,
            "C",
            "LFPB",
            1,
            "2026-06-06T05:15:00+00:00",
            "VERIFIED",
            "v1.wu-native",
            '{"source_url":"redacted","station_id":"LFPB"}',
            1,
            "OK",
            "historical_hourly",
        ),
    )

    results = Day0ExtremeUpdatedTrigger(EventWriter(conn)).scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(14),
        decision_time=datetime(2026, 6, 6, 5, 20, tzinfo=timezone.utc),
        received_at="2026-06-06T05:20:00+00:00",
    )

    assert len(results) == 2
    payloads = [row[0] for row in conn.execute("SELECT payload_json FROM opportunity_events").fetchall()]
    assert {'"metric":"high"', '"metric":"low"'} == {
        '"metric":"high"' if '"metric":"high"' in payload else '"metric":"low"'
        for payload in payloads
    }
    high_payload = next(payload for payload in payloads if '"metric":"high"' in payload)
    low_payload = next(payload for payload in payloads if '"metric":"low"' in payload)
    assert '"raw_value":14.0' in high_payload
    assert '"high_so_far":14.0' in high_payload
    assert '"low_so_far":11.0' in high_payload
    assert '"raw_value":11.0' in low_payload
    assert '"high_so_far":14.0' in low_payload
    assert '"low_so_far":11.0' in low_payload
    assert all('"event_type":"DAY0_EXTREME_UPDATED"' not in payload for payload in payloads)
    assert all('"live_authority_status":"live"' in payload for payload in payloads)
    assert all('"source_authorized_status":"AUTHORIZED"' in payload for payload in payloads)


def test_scan_observation_instants_tokyo_low_uses_aggregate_target_day_min():
    """Tokyo regression: the first target-local-day LOW feeds the EDLI event lane.

    2026-06-17T15:00Z is 2026-06-18T00:00 Asia/Tokyo. The 00:00 row records
    low=20, while the later 01:00 row reports running_min=21. EDLI must emit
    the aggregate target-day LOW=20 for probability calculation and order
    selection, not the latest row's LOW=21.
    """
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    insert_sql = """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority,
            data_version, provenance_json, training_allowed, causality_status, source_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    base = {
        "city": "Tokyo",
        "target_date": "2026-06-18",
        "source": "wu_icao_history",
        "timezone_name": "Asia/Tokyo",
        "utc_offset_minutes": 540,
        "temp_unit": "C",
        "station_id": "RJTT",
        "authority": "VERIFIED",
        "data_version": "v1.wu-native",
        "provenance_json": '{"source_url":"redacted","station_id":"RJTT"}',
        "training_allowed": 1,
        "causality_status": "OK",
        "source_role": "historical_hourly",
    }
    rows = (
        {
            **base,
            "local_hour": 0.0,
            "local_timestamp": "2026-06-18T00:00:00+09:00",
            "utc_timestamp": "2026-06-17T15:00:00+00:00",
            "temp_current": 20.0,
            "running_max": 20.0,
            "running_min": 20.0,
            "observation_count": 1,
            "imported_at": "2026-06-17T15:15:34.807336+00:00",
        },
        {
            **base,
            "local_hour": 1.0,
            "local_timestamp": "2026-06-18T01:00:00+09:00",
            "utc_timestamp": "2026-06-17T16:00:00+00:00",
            "temp_current": 21.0,
            "running_max": 21.0,
            "running_min": 21.0,
            "observation_count": 1,
            "imported_at": "2026-06-17T16:15:57.241581+00:00",
        },
    )
    for row in rows:
        conn.execute(
            insert_sql,
            (
                row["city"], row["target_date"], row["source"], row["timezone_name"],
                row["local_hour"], row["local_timestamp"], row["utc_timestamp"],
                row["utc_offset_minutes"], 0, 0, 0, "observed", row["temp_current"],
                row["running_max"], row["running_min"], row["temp_unit"],
                row["station_id"], row["observation_count"], row["imported_at"],
                row["authority"], row["data_version"], row["provenance_json"],
                row["training_allowed"], row["causality_status"], row["source_role"],
            ),
        )

    results = Day0ExtremeUpdatedTrigger(EventWriter(conn)).scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(20),
        decision_time=datetime(2026, 6, 17, 16, 20, tzinfo=timezone.utc),
        received_at="2026-06-17T16:20:00+00:00",
    )

    assert len(results) == 2
    payloads = [
        json.loads(row[0])
        for row in conn.execute("SELECT payload_json FROM opportunity_events").fetchall()
    ]
    low_payload = next(payload for payload in payloads if payload["metric"] == "low")
    high_payload = next(payload for payload in payloads if payload["metric"] == "high")

    assert low_payload["city"] == "Tokyo"
    assert low_payload["target_date"] == "2026-06-18"
    assert low_payload["station_id"] == "RJTT"
    assert low_payload["local_date_status"] == "MATCH"
    assert low_payload["source_authorized_status"] == "AUTHORIZED"
    assert low_payload["live_authority_status"] == "live"
    assert low_payload["raw_value"] == 20.0
    assert low_payload["low_so_far"] == 20.0
    assert high_payload["high_so_far"] == 21.0


def test_scan_observation_instants_london_excludes_tminus1_23_from_target_low_aggregate():
    """Europe boundary regression: a mis-tagged T-1 23:00 local row must not be
    aggregated into the target day's LOW just because a later T0 00:00 row makes
    the grouped observation_time pass the local-date gate."""

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    insert_sql = """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority,
            data_version, provenance_json, training_allowed, causality_status, source_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    base = {
        "city": "London",
        "target_date": "2026-06-18",
        "source": "wu_icao_history",
        "timezone_name": "Europe/London",
        "utc_offset_minutes": 60,
        "temp_unit": "C",
        "station_id": "EGLC",
        "authority": "VERIFIED",
        "data_version": "v1.wu-native",
        "provenance_json": '{"source_url":"redacted","station_id":"EGLC"}',
        "training_allowed": 1,
        "causality_status": "OK",
        "source_role": "historical_hourly",
    }
    rows = (
        {
            **base,
            "local_hour": 23.0,
            "local_timestamp": "2026-06-17T23:00:00+01:00",
            "utc_timestamp": "2026-06-17T22:00:00+00:00",
            "temp_current": 10.0,
            "running_max": 10.0,
            "running_min": 10.0,
            "observation_count": 1,
            "imported_at": "2026-06-17T22:05:00+00:00",
        },
        {
            **base,
            "local_hour": 0.0,
            "local_timestamp": "2026-06-18T00:00:00+01:00",
            "utc_timestamp": "2026-06-17T23:00:00+00:00",
            "temp_current": 16.0,
            "running_max": 16.0,
            "running_min": 16.0,
            "observation_count": 1,
            "imported_at": "2026-06-17T23:05:00+00:00",
        },
    )
    for row in rows:
        conn.execute(
            insert_sql,
            (
                row["city"], row["target_date"], row["source"], row["timezone_name"],
                row["local_hour"], row["local_timestamp"], row["utc_timestamp"],
                row["utc_offset_minutes"], 1, 0, 0, "observed", row["temp_current"],
                row["running_max"], row["running_min"], row["temp_unit"],
                row["station_id"], row["observation_count"], row["imported_at"],
                row["authority"], row["data_version"], row["provenance_json"],
                row["training_allowed"], row["causality_status"], row["source_role"],
            ),
        )

    results = Day0ExtremeUpdatedTrigger(EventWriter(conn)).scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(16),
        decision_time=datetime(2026, 6, 17, 23, 10, tzinfo=timezone.utc),
        received_at="2026-06-17T23:10:00+00:00",
    )

    assert len(results) == 2
    payloads = [
        json.loads(row[0])
        for row in conn.execute("SELECT payload_json FROM opportunity_events").fetchall()
    ]
    low_payload = next(payload for payload in payloads if payload["metric"] == "low")
    assert low_payload["city"] == "London"
    assert low_payload["target_date"] == "2026-06-18"
    assert low_payload["local_date_status"] == "MATCH"
    assert low_payload["raw_value"] == 16.0
    assert low_payload["low_so_far"] == 16.0
    assert low_payload["observation_time"] == "2026-06-17T23:00:00+00:00"


def test_scan_observation_instants_london_rejects_wrong_station_and_source_family():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    insert_sql = """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority,
            data_version, provenance_json, training_allowed, causality_status, source_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    rows = (
        {
            "source": "wu_icao_history",
            "station_id": "XXXX",
            "provenance_json": '{"source_url":"redacted","station_id":"XXXX"}',
        },
        {
            "source": "ogimet_metar_EGLC",
            "station_id": "EGLC",
            "provenance_json": '{"source_url":"redacted","station_id":"EGLC"}',
        },
    )
    for row in rows:
        conn.execute(
            insert_sql,
            (
                "London",
                "2026-06-18",
                row["source"],
                "Europe/London",
                0.0,
                "2026-06-18T00:00:00+01:00",
                "2026-06-17T23:00:00+00:00",
                60,
                1,
                0,
                0,
                "observed",
                9.0,
                16.0,
                9.0,
                "C",
                row["station_id"],
                1,
                "2026-06-17T23:05:00+00:00",
                "VERIFIED",
                "v1.wu-native",
                row["provenance_json"],
                1,
                "OK",
                "historical_hourly",
            ),
        )

    results = Day0ExtremeUpdatedTrigger(EventWriter(conn)).scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(9),
        decision_time=datetime(2026, 6, 17, 23, 10, tzinfo=timezone.utc),
        received_at="2026-06-17T23:10:00+00:00",
    )

    assert results == []
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0


def _insert_observation_instant(conn, *, running_max, running_min, imported_at, station_id="LFPB", city="Paris", target_date="2026-06-06", source="wu_icao_history"):
    conn.execute(
        """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority,
            data_version, provenance_json, training_allowed, causality_status, source_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city, target_date, source, "Europe/Paris", 6.0, "2026-06-06T06:00:00+02:00",
            imported_at.replace("T04:15", "T04:00").replace("T05:15", "T05:00").replace("T06:15", "T06:00"),
            120, 1, 0, 0, "observed", running_max, running_max, running_min,
            "C", station_id, 1, imported_at, "VERIFIED", "v1.wu-native",
            '{"source_url":"redacted","station_id":"LFPB"}', 1, "OK", "historical_hourly",
        ),
    )


def test_scan_observation_instants_change_gate_suppresses_unchanged_extreme():
    """FIREHOSE ANTIBODY (2026-06-15): a re-scan whose running extreme is UNCHANGED emits
    NOTHING, even though MAX(imported_at) advanced (which would otherwise mint a new event
    via the available_at-keyed idempotency). The unchanged-extreme firehose that starved
    the FORECAST_SNAPSHOT_READY/spine lane at Tier-0 is suppressed."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    trigger = Day0ExtremeUpdatedTrigger(EventWriter(conn))

    _insert_observation_instant(conn, running_max=14.0, running_min=11.0, imported_at="2026-06-06T04:15:00+00:00")
    first = trigger.scan_observation_instants_rows(
        observation_conn=conn, settlement_semantics=FakeSettlementSemantics(14),
        decision_time=datetime(2026, 6, 6, 5, 20, tzinfo=timezone.utc), received_at="2026-06-06T05:20:00+00:00",
    )
    assert len(first) == 2  # first-ever high+low
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 2

    # Later import, SAME running extreme → available_at advances but the extreme did not.
    _insert_observation_instant(conn, running_max=14.0, running_min=11.0, imported_at="2026-06-06T05:15:00+00:00")
    second = trigger.scan_observation_instants_rows(
        observation_conn=conn, settlement_semantics=FakeSettlementSemantics(14),
        decision_time=datetime(2026, 6, 6, 5, 20, tzinfo=timezone.utc), received_at="2026-06-06T05:20:00+00:00",
    )
    assert second == [], "unchanged extreme must not re-emit (firehose suppressed)"
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 2


def test_scan_observation_instants_change_gate_emits_on_extreme_advance():
    """The gate still emits when the running extreme ADVANCES: a higher running_max emits a
    new 'high' event; the unchanged low is suppressed."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    trigger = Day0ExtremeUpdatedTrigger(EventWriter(conn))

    _insert_observation_instant(conn, running_max=14.0, running_min=11.0, imported_at="2026-06-06T04:15:00+00:00")
    trigger.scan_observation_instants_rows(
        observation_conn=conn, settlement_semantics=FakeSettlementSemantics(14),
        decision_time=datetime(2026, 6, 6, 5, 20, tzinfo=timezone.utc), received_at="2026-06-06T05:20:00+00:00",
    )
    # New high (16 > 14); low unchanged (11).
    _insert_observation_instant(conn, running_max=16.0, running_min=11.0, imported_at="2026-06-06T05:15:00+00:00")
    second = trigger.scan_observation_instants_rows(
        observation_conn=conn, settlement_semantics=FakeSettlementSemantics(16),
        decision_time=datetime(2026, 6, 6, 5, 20, tzinfo=timezone.utc), received_at="2026-06-06T05:20:00+00:00",
    )
    assert len(second) == 1, "only the advanced high re-emits; the unchanged low is suppressed"
    payloads = [r[0] for r in conn.execute("SELECT payload_json FROM opportunity_events").fetchall()]
    assert sum('"high_so_far":16.0' in p for p in payloads) == 1


def test_day0_write_suppresses_recent_same_payload_no_value_refutation():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    base_trigger = Day0ExtremeUpdatedTrigger(EventWriter(conn))
    obs = _observation()
    prior = base_trigger.emit_from_observation(
        observation=obs,
        settlement_semantics=FakeSettlementSemantics(74),
        decision_time=datetime(2026, 5, 24, 18, 10, tzinfo=timezone.utc),
        received_at="2026-05-24T18:10:00+00:00",
    )
    conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason, regret_bucket,
            decision_time, city, target_date, metric, family_id, causal_snapshot_id,
            created_at, schema_version
        ) VALUES (?, ?, 'TRADE_SCORE', 'EVENT_BOUND_ALL_CANDIDATES_REJECTED:none', 'NO_EDGE',
                  '2026-05-24T18:11:00+00:00', 'Chicago', '2026-05-24', 'high',
                  'family-chicago-high', 'obsctx-1', '2026-05-24T18:11:00+00:00', 1)
        """,
        ("regret-" + prior.event_id, prior.event_id),
    )

    suppressing_trigger = Day0ExtremeUpdatedTrigger(
        EventWriter(conn),
        suppress_recent_no_value_refutations=True,
    )
    result = suppressing_trigger._write_observation_if_admitted(
        observation=obs,
        settlement_semantics=FakeSettlementSemantics(74),
        decision_time=datetime(2026, 5, 24, 18, 15, tzinfo=timezone.utc),
        received_at="2026-05-24T18:15:00+00:00",
    )

    assert result is None


def test_scan_observation_instants_rows_skips_fallback_evidence_rows():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority,
            data_version, provenance_json, training_allowed, causality_status, source_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Hong Kong",
            "2026-06-06",
            "hko_hourly_accumulator",
            "Asia/Hong_Kong",
            12.0,
            "2026-06-06T12:00:00+08:00",
            "2026-06-06T04:00:00+00:00",
            480,
            0,
            0,
            0,
            "observed",
            30.0,
            30.0,
            28.0,
            "C",
            "HKO",
            1,
            "2026-06-06T04:15:00+00:00",
            "ICAO_STATION_NATIVE",
            "v1.hko-native",
            '{"source_url":"redacted","station_id":"HKO"}',
            0,
            "REQUIRES_SOURCE_REAUDIT",
            "fallback_evidence",
        ),
    )

    results = Day0ExtremeUpdatedTrigger(EventWriter(conn)).scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=FakeSettlementSemantics(30),
        decision_time=datetime(2026, 6, 6, 4, 20, tzinfo=timezone.utc),
        received_at="2026-06-06T04:20:00+00:00",
    )

    assert results == []
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0


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

    assert observation["live_authority_status"] == "blocked"
    assert observation["source_match_status"] == "UNKNOWN"
    assert observation["station_match_status"] == "UNKNOWN"
    assert observation["metric_match_status"] == "UNKNOWN"
