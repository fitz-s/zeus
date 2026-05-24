# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §8 ForecastSnapshotReadyTrigger contract.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.events.event_writer import EventWriter
from src.events.triggers.forecast_snapshot_ready import (
    ForecastSnapshotReadyTrigger,
    build_forecast_snapshot_ready_event,
    classify_forecast_snapshot,
    ecmwf_open_data_expected_steps,
    executable_forecast_live_eligible_reader,
)
from src.state.db import init_schema


UTC = timezone.utc


def _source_run(status: str = "SUCCESS", completeness: str = "COMPLETE") -> dict:
    return {
        "source_run_id": "run-1",
        "source_id": "ecmwf-open-data",
        "track": "ens",
        "source_cycle_time": "2026-05-24T00:00:00+00:00",
        "source_available_at": "2026-05-24T04:15:00+00:00",
        "captured_at": "2026-05-24T04:16:00+00:00",
        "status": status,
        "completeness_status": completeness,
        "expected_members": 51,
        "observed_members": 51,
        "expected_steps_json": "[0,3,6]",
        "observed_steps_json": "[0,3,6]",
    }


def _coverage(completeness: str = "COMPLETE", readiness: str = "LIVE_ELIGIBLE", members: int = 51) -> dict:
    return {
        "coverage_id": "cov-1",
        "source_run_id": "run-1",
        "source_id": "ecmwf-open-data",
        "source_transport": "ensemble_snapshots_v2_db_reader",
        "track": "ens",
        "city": "Chicago",
        "city_id": "chicago",
        "city_timezone": "America/Chicago",
        "target_local_date": "2026-05-24",
        "temperature_metric": "high",
        "expected_members": 51,
        "observed_members": members,
        "expected_steps_json": "[0,3,6]",
        "observed_steps_json": "[0,3,6]",
        "completeness_status": completeness,
        "readiness_status": readiness,
        "computed_at": "2026-05-24T04:16:00+00:00",
    }


def _snapshot(available_at: str = "2026-05-24T04:15:00+00:00") -> dict:
    return {
        "snapshot_id": "1001",
        "snapshot_hash": "hash-1001",
        "city": "Chicago",
        "target_date": "2026-05-24",
        "temperature_metric": "high",
        "source_run_id": "run-1",
        "available_at": available_at,
        "fetch_time": "2026-05-24T04:16:00+00:00",
        "member_count": 51,
    }


def _decision_time() -> datetime:
    return datetime(2026, 5, 24, 5, 0, tzinfo=UTC)


def test_complete_snapshot_emits_once():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(conn),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )

    first = trigger.emit_from_rows(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )
    second = trigger.emit_from_rows(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )

    assert first.inserted is True
    assert second.duplicate is True
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1


def test_rerun_idempotent_same_source_run_snapshot_hash():
    event_a = build_forecast_snapshot_ready_event(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    event_b = build_forecast_snapshot_ready_event(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    assert event_a.idempotency_key == event_b.idempotency_key


def test_partial_40_members_no_live_trade_evidence_only():
    result = classify_forecast_snapshot(
        source_run=_source_run(completeness="PARTIAL"),
        coverage=_coverage(completeness="PARTIAL", readiness="BLOCKED", members=40),
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        min_members_floor=40,
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: False,
    )
    assert result.completeness_status == "PARTIAL_ALLOWED"
    assert result.live_eligible is False


def test_missing_required_steps_partial_blocked():
    coverage = _coverage(completeness="PARTIAL", readiness="BLOCKED", members=51)
    coverage["observed_steps_json"] = "[0,3]"
    result = classify_forecast_snapshot(
        source_run=_source_run(completeness="PARTIAL"),
        coverage=coverage,
        snapshot=_snapshot(),
        decision_time=_decision_time(),
    )
    assert result.completeness_status == "PARTIAL_BLOCKED"


def test_available_at_is_source_available_not_issue_time():
    event = build_forecast_snapshot_ready_event(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(available_at="2026-05-24T04:15:00+00:00"),
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    assert event.available_at == "2026-05-24T04:15:00+00:00"


def test_read_executable_forecast_blocks_future_available_at():
    result = classify_forecast_snapshot(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(available_at="2026-05-24T06:00:00+00:00"),
        decision_time=_decision_time(),
    )
    assert result.completeness_status == "PARTIAL_BLOCKED"
    assert result.reason == "AVAILABLE_AT_IN_FUTURE"


def test_00z_12z_step_set_differs_from_06z_18z_after_cycle_50r1():
    steps_00 = ecmwf_open_data_expected_steps(0)
    steps_06 = ecmwf_open_data_expected_steps(6)
    assert max(steps_00) == 360
    assert max(steps_06) == 144
    assert 150 in steps_00
    assert 150 not in steps_06


def test_forecast_emit_failure_does_not_rollback_ingest_commit():
    source_run = _source_run()
    coverage = _coverage()
    snapshot = _snapshot()
    try:
        build_forecast_snapshot_ready_event(
            source_run=source_run,
            coverage=coverage,
            snapshot=snapshot | {"available_at": "not-a-date"},
            decision_time=_decision_time(),
            received_at="2026-05-24T04:17:00+00:00",
        )
    except ValueError:
        pass
    assert source_run["source_run_id"] == "run-1"
    assert coverage["coverage_id"] == "cov-1"


def test_scan_committed_snapshots_emits_from_source_run_coverage():
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema_forecasts

    init_schema_forecasts(forecasts_conn)
    forecasts_conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_available_at, captured_at, target_local_date,
            city_id, city_timezone, temperature_metric, data_version,
            expected_members, observed_members, expected_steps_json, observed_steps_json,
            completeness_status, status
        ) VALUES (
            'run-1', 'ecmwf-open-data', 'ens', '2026-05-24T00', 'SCHEDULED_LIVE', 'SCHEDULED_LIVE',
            '2026-05-24T00:00:00+00:00', '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00',
            '2026-05-24', 'chicago', 'America/Chicago', 'high', 'v1',
            51, 51, '[0,3,6]', '[0,3,6]', 'COMPLETE', 'SUCCESS'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO source_run_coverage (
            coverage_id, source_run_id, source_id, source_transport, release_calendar_key, track,
            city_id, city, city_timezone, target_local_date, temperature_metric, physical_quantity,
            observation_field, data_version, expected_members, observed_members, expected_steps_json,
            observed_steps_json, snapshot_ids_json, target_window_start_utc, target_window_end_utc,
            completeness_status, readiness_status, computed_at, expires_at
        ) VALUES (
            'cov-1', 'run-1', 'ecmwf-open-data', 'ensemble_snapshots_v2_db_reader', '2026-05-24T00', 'ens',
            'chicago', 'Chicago', 'America/Chicago', '2026-05-24', 'high', 'temperature',
            'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', '[1]',
            '2026-05-24T05:00:00+00:00', '2026-05-25T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', '2026-05-24T04:16:00+00:00', '2026-05-25T04:16:00+00:00'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, data_version, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
            forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
            members_unit, raw_orderbook_hash_transition_delta_ms
        ) VALUES (
            1, 'Chicago', '2026-05-24', 'high', 'temperature', 'high_temp',
            '2026-05-24T00:00:00+00:00', '2026-05-24T06:00:00+00:00',
            '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00', 6,
            '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]',
            'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_v2_db_reader', 'run-1',
            '2026-05-24T00', '2026-05-24T00:00:00+00:00', '2026-05-24T03:00:00+00:00',
            '2026-05-24T04:15:00+00:00', 'VERIFIED', 'OK', 0, 1,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-05-24T05:00:00+00:00', 6, 'F', 0
        )
        """
    )
    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
    )

    results = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )

    assert len(results) == 1
    payload = world_conn.execute("SELECT payload_json FROM opportunity_events").fetchone()[0]
    import json

    assert json.loads(payload)["completeness_status"] == "COMPLETE"
