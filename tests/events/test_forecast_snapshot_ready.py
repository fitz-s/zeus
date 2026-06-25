# Created: 2026-05-24
# Last reused/audited: 2026-06-19
# Authority basis: EDLI v1 implementation prompt §8 ForecastSnapshotReadyTrigger contract.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

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


@pytest.fixture(autouse=True)
def _replacement_authority_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_live_enabled",
        lambda: False,
    )


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
        "source_transport": "ensemble_snapshots_db_reader",
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


def test_opendata_t3_data_version_normalizes_legacy_track_label():
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(conn),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    source_run = _source_run()
    source_run["source_id"] = "ecmwf_open_data"
    source_run["source_run_id"] = "ecmwf_open_data:mx2t6_high:2026-06-05T12Z"
    source_run["track"] = "mx2t6_high_full_horizon"
    coverage = _coverage()
    coverage["source_id"] = "ecmwf_open_data"
    coverage["source_run_id"] = source_run["source_run_id"]
    coverage["track"] = "mx2t6_high_full_horizon"
    coverage["data_version"] = "ecmwf_opendata_mx2t3_local_calendar_day_max"
    snapshot = _snapshot()
    snapshot["source_run_id"] = source_run["source_run_id"]
    snapshot["data_version"] = "ecmwf_opendata_mx2t3_local_calendar_day_max"

    trigger.emit_from_rows(
        source_run=source_run,
        coverage=coverage,
        snapshot=snapshot,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )

    import json as _json

    payload = _json.loads(conn.execute("SELECT payload_json FROM opportunity_events").fetchone()[0])
    assert payload["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-06-05T12Z"
    assert payload["track"] == "mx2t3_high_full_horizon"


def test_replacement_authority_track_masks_legacy_t3_carrier(monkeypatch):
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_live_enabled",
        lambda: True,
    )
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(conn),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    source_run = _source_run()
    source_run["source_id"] = "ecmwf_open_data"
    source_run["source_run_id"] = "ecmwf_open_data:mx2t6_high:2026-06-05T12Z"
    source_run["track"] = "mx2t6_high_full_horizon"
    coverage = _coverage()
    coverage["source_id"] = "ecmwf_open_data"
    coverage["source_run_id"] = source_run["source_run_id"]
    coverage["track"] = "mx2t6_high_full_horizon"
    coverage["data_version"] = "ecmwf_opendata_mx2t3_local_calendar_day_max"
    snapshot = _snapshot()
    snapshot["source_run_id"] = source_run["source_run_id"]
    snapshot["data_version"] = "ecmwf_opendata_mx2t3_local_calendar_day_max"

    trigger.emit_from_rows(
        source_run=source_run,
        coverage=coverage,
        snapshot=snapshot,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )

    import json as _json

    payload = _json.loads(conn.execute("SELECT payload_json FROM opportunity_events").fetchone()[0])
    assert payload["source_run_id"] == "ecmwf_open_data:mx2t6_high:2026-06-05T12Z"
    assert payload["track"] == "replacement_0_1_openmeteo_bayes_fusion"
    assert "t3" not in payload["track"]
    assert "t6" not in payload["track"]


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


def test_empty_expected_steps_without_target_window_fails_closed():
    source_run = _source_run(completeness="COMPLETE")
    coverage = _coverage(completeness="COMPLETE", readiness="LIVE_ELIGIBLE", members=51)
    source_run["expected_steps_json"] = "[]"
    coverage["expected_steps_json"] = "[]"
    coverage["observed_steps_json"] = "[0,3,6]"
    source_run["observed_steps_json"] = "[0,3,6]"

    result = classify_forecast_snapshot(
        source_run=source_run,
        coverage=coverage,
        snapshot=_snapshot(),
        decision_time=_decision_time(),
    )

    assert result.completeness_status == "PARTIAL_BLOCKED"
    assert result.required_steps_present is False
    assert result.reason == "EXPECTED_STEPS_UNKNOWN"


def test_empty_expected_steps_derives_target_window_steps_only():
    source_run = _source_run(completeness="COMPLETE")
    coverage = _coverage(completeness="COMPLETE", readiness="LIVE_ELIGIBLE", members=51)
    source_run["expected_steps_json"] = "[]"
    coverage["expected_steps_json"] = "[]"
    coverage["target_window_start_utc"] = "2026-05-24T06:00:00+00:00"
    coverage["target_window_end_utc"] = "2026-05-24T12:00:00+00:00"
    coverage["observed_steps_json"] = "[6,9,12]"
    source_run["observed_steps_json"] = "[6,9,12]"

    result = classify_forecast_snapshot(
        source_run=source_run,
        coverage=coverage,
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )

    assert result.completeness_status == "COMPLETE"
    assert result.required_steps_present is True


def test_available_at_is_source_available_not_issue_time():
    # C1-AVAIL-CLOCK (2026-06-16): the event's available_at is PROOF OF POSSESSION first.
    # build_forecast_snapshot_ready_event now prefers the snapshot's real fetch_time (the
    # wall-clock we held the data, 04:16 in _snapshot()) over the snapshot's available_at
    # (04:15) and over source_available_at. fetch_time is the strongest possession stamp,
    # so it (not the cycle/issue time, and not the weaker available_at) is what the event
    # carries. This still satisfies the original intent of this test — available_at must be
    # a real possession clock, never the issue/cycle time.
    event = build_forecast_snapshot_ready_event(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(available_at="2026-05-24T04:15:00+00:00"),
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    assert event.available_at == "2026-05-24T04:16:00+00:00"


def test_read_executable_forecast_blocks_future_available_at():
    result = classify_forecast_snapshot(
        source_run=_source_run(),
        coverage=_coverage(),
        snapshot=_snapshot(available_at="2026-05-24T06:00:00+00:00"),
        decision_time=_decision_time(),
    )
    assert result.completeness_status == "PARTIAL_BLOCKED"
    assert result.reason == "AVAILABLE_AT_IN_FUTURE"


def test_future_coverage_computed_at_blocks_even_when_snapshot_available():
    coverage = _coverage()
    coverage["computed_at"] = "2026-05-24T06:00:00+00:00"

    result = classify_forecast_snapshot(
        source_run=_source_run(),
        coverage=coverage,
        snapshot=_snapshot(available_at="2026-05-24T04:15:00+00:00"),
        decision_time=_decision_time(),
    )

    assert result.completeness_status == "PARTIAL_BLOCKED"
    assert result.reason == "COVERAGE_COMPUTED_AT_IN_FUTURE"


def test_all_cycles_cap_at_144_under_5day_horizon():
    """5-day cap (2026-05-29): Polymarket retired >5-day markets, so all four cycles
    now share the same 0..144h candidate grid. The former 0/12 long tail (150-360h)
    is no longer fetched and must not be expected (else fallback completeness is
    permanently fail-closed)."""
    steps_00 = ecmwf_open_data_expected_steps(0)
    steps_06 = ecmwf_open_data_expected_steps(6)
    assert max(steps_00) == 144
    assert max(steps_06) == 144
    assert 150 not in steps_00
    assert steps_00 == steps_06


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
            city_id, city_timezone, temperature_metric, dataset_id,
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
            'cov-1', 'run-1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', '2026-05-24T00', 'ens',
            'chicago', 'Chicago', 'America/Chicago', '2026-05-24', 'high', 'temperature',
            'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', '[1]',
            '2026-05-24T05:00:00+00:00', '2026-05-25T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', '2026-05-24T04:16:00+00:00', '2026-05-25T04:16:00+00:00'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, dataset_id, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
            forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
            members_unit, raw_orderbook_hash_transition_delta_ms
        ) VALUES (
            1, 'Chicago', '2026-05-24', 'high', 'temperature', 'high_temp',
            '2026-05-24T00:00:00+00:00', '2026-05-24T06:00:00+00:00',
            '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00', 6,
            '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]',
            'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', 'run-1',
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


def test_build_committed_snapshot_events_does_not_write_world_rows():
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    _seed_committed_chicago_2026_05_24(forecasts_conn)

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
    )

    events = trigger.build_committed_snapshot_events(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )

    assert len(events) == 1
    assert world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    written = EventWriter(world_conn).write_many(events)
    assert len(written) == 1
    assert written[0].inserted is True
    assert world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1


def _seed_committed_chicago_2026_05_24(forecasts_conn) -> None:
    """Insert a COMPLETE/LIVE_ELIGIBLE Chicago high coverage for target 2026-05-24
    (the same shape as test_scan_committed_snapshots_emits_from_source_run_coverage)."""
    from src.state.db import init_schema_forecasts

    init_schema_forecasts(forecasts_conn)
    forecasts_conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_available_at, captured_at, target_local_date,
            city_id, city_timezone, temperature_metric, dataset_id,
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
            'cov-1', 'run-1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', '2026-05-24T00', 'ens',
            'chicago', 'Chicago', 'America/Chicago', '2026-05-24', 'high', 'temperature',
            'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', '[1]',
            '2026-05-24T05:00:00+00:00', '2026-05-25T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', '2026-05-24T04:16:00+00:00', '2026-05-25T04:16:00+00:00'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, dataset_id, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
            forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
            members_unit, raw_orderbook_hash_transition_delta_ms
        ) VALUES (
            1, 'Chicago', '2026-05-24', 'high', 'temperature', 'high_temp',
            '2026-05-24T00:00:00+00:00', '2026-05-24T06:00:00+00:00',
            '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00', 6,
            '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]',
            'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', 'run-1',
            '2026-05-24T00', '2026-05-24T00:00:00+00:00', '2026-05-24T03:00:00+00:00',
            '2026-05-24T04:15:00+00:00', 'VERIFIED', 'OK', 0, 1,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-05-24T05:00:00+00:00', 6, 'F', 0
        )
        """
    )


def test_scan_emits_zero_for_strictly_past_target_local_day():
    """T1 (STEP 2 emission floor S1): a committed coverage row whose target LOCAL
    day is already STRICTLY PAST at decision_time emits ZERO opportunity_events.

    The same COMPLETE/LIVE_ELIGIBLE row emits exactly one event when decided on
    its own local day (proven by
    test_scan_committed_snapshots_emits_from_source_run_coverage); here, deciding
    a full day later (Chicago-local 2026-06-05) makes target 2026-05-24
    already-settled, so the emission floor drops it at the SOURCE — it never
    reaches the reactor's bounded decision-proof budget. This is the highest-
    leverage point-fix: stop manufacturing stale candidates at emission.
    """
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    _seed_committed_chicago_2026_05_24(forecasts_conn)

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
    )

    # Decide a full local day after target 2026-05-24 → strictly past.
    results = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=datetime(2026, 5, 25, 12, 0, tzinfo=UTC),  # Chicago local 2026-05-25 07:00
        received_at="2026-05-25T12:01:00+00:00",
    )

    assert results == [], "strictly-past target must emit ZERO events at the source"
    count = world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0]
    assert count == 0, "no opportunity_event row may be written for an already-settled target"


def test_replacement_authority_scan_requires_matching_0_1_posterior(monkeypatch):
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_live_enabled",
        lambda: True,
    )
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    _seed_committed_chicago_2026_05_24(forecasts_conn)

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
    )

    without_posterior = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )
    assert without_posterior == []

    forecasts_conn.execute(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
            q_ucb_json, posterior_method, dependency_source_run_ids_json,
            provenance_json, runtime_layer, training_allowed
        ) VALUES (
            'openmeteo_ecmwf_ifs9_bayes_fusion',
            'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
            'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
            'Chicago', '2026-05-24', 'high',
            '2026-05-24T00:00:00+00:00',
            '2026-05-24T04:15:00+00:00',
            '2026-05-24T04:16:00+00:00',
            '{"bin:28":0.42}', NULL, NULL,
            'openmeteo_ecmwf_ifs9_bayes_fusion',
            '[]', '{}', 'live', 0
        )
        """
    )
    forecasts_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_model_forecasts (
            model TEXT,
            city TEXT,
            target_date TEXT,
            metric TEXT,
            source_cycle_time TEXT,
            source_available_at TEXT,
            forecast_value_c REAL
        )
        """
    )
    forecasts_conn.execute(
        "DELETE FROM raw_model_forecasts WHERE city='Chicago' AND target_date='2026-05-24' AND metric='high'"
    )
    forecasts_conn.executemany(
        """
        INSERT INTO raw_model_forecasts (
            model, city, target_date, metric, source_cycle_time,
            source_available_at, captured_at, lead_days, forecast_value_c, endpoint
        ) VALUES (?, 'Chicago', '2026-05-24', 'high',
                  '2026-05-24T00:00:00+00:00',
                  '2026-05-24T04:15:00+00:00',
                  '2026-05-24T04:16:00+00:00', 0, ?, 'single_runs')
        """,
        [("ecmwf_ifs", 22.0), ("gfs_global", 22.5), ("icon_global", 21.8)],
    )

    with_posterior = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:18:00+00:00",
    )
    assert len(with_posterior) == 1
    import json

    payload = json.loads(world_conn.execute("SELECT payload_json FROM opportunity_events").fetchone()[0])
    assert payload["track"] == "replacement_0_1_openmeteo_bayes_fusion"
    assert payload["member_count"] == 3
    assert payload["expected_members"] == 3


def test_restricted_redecision_counts_raw_members_only_for_screened_family(monkeypatch):
    """A screened redecision must not raw-member scan every latest posterior family."""

    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_live_enabled",
        lambda: True,
    )
    counted: list[tuple[str, str, str]] = []

    def _fake_count(_conn, row, *, decision_iso):
        counted.append((row["city"], row["target_local_date"], row["temperature_metric"]))
        return 3

    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._raw_model_member_count_for_posterior_row",
        _fake_count,
    )
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema_forecasts

    init_schema_forecasts(forecasts_conn)
    forecasts_conn.executemany(
        """
        INSERT INTO forecast_posteriors (
            source_id, product_id, data_version, city, target_date, temperature_metric,
            source_cycle_time, source_available_at, computed_at, q_json, q_lcb_json,
            q_ucb_json, posterior_method, dependency_source_run_ids_json,
            provenance_json, runtime_layer, training_allowed
        ) VALUES (
            'openmeteo_ecmwf_ifs9_bayes_fusion',
            'openmeteo_ecmwf_ifs9_bayes_fusion_v1',
            'openmeteo_ecmwf_ifs9_bayes_fusion_high_v1',
            ?, '2026-05-24', 'high',
            '2026-05-24T00:00:00+00:00',
            '2026-05-24T04:15:00+00:00',
            ?,
            '{"bin:28":0.42}', NULL, NULL,
            'openmeteo_ecmwf_ifs9_bayes_fusion',
            '[]', '{}', 'live', 0
        )
        """,
        [
            ("Chicago", "2026-05-24T04:16:00+00:00"),
            ("Denver", "2026-05-24T04:17:00+00:00"),
        ],
    )

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )

    results = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:18:00+00:00",
        source="cycle-1",
        event_type="EDLI_REDECISION_PENDING",
        restrict_to_families={("Chicago", "2026-05-24", "high")},
        phase_filter_exempt_families={("Chicago", "2026-05-24", "high")},
    )

    assert len(results) == 1
    assert counted == [("Chicago", "2026-05-24", "high")]
    import json

    payload = json.loads(world_conn.execute("SELECT payload_json FROM opportunity_events").fetchone()[0])
    assert payload["city"] == "Chicago"


def test_scan_emits_only_for_families_with_a_market_when_markets_exist():
    """Decision-first emission relationship.

    A committed forecast family that has NO Polymarket market (no market_events row) must NOT
    emit a decision event once any market exists — it can never trade, so it must not consume
    the reactor's bounded decision-proof budget. Fail-open is covered by the other tests
    (empty market_events -> emit all). Once the family's market appears, it emits.
    """
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema_forecasts

    init_schema_forecasts(forecasts_conn)
    forecasts_conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_available_at, captured_at, target_local_date,
            city_id, city_timezone, temperature_metric, dataset_id,
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
            'cov-1', 'run-1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', '2026-05-24T00', 'ens',
            'chicago', 'Chicago', 'America/Chicago', '2026-05-24', 'high', 'temperature',
            'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', '[1]',
            '2026-05-24T05:00:00+00:00', '2026-05-25T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', '2026-05-24T04:16:00+00:00', '2026-05-25T04:16:00+00:00'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, dataset_id, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
            forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
            members_unit, raw_orderbook_hash_transition_delta_ms
        ) VALUES (
            1, 'Chicago', '2026-05-24', 'high', 'temperature', 'high_temp',
            '2026-05-24T00:00:00+00:00', '2026-05-24T06:00:00+00:00',
            '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00', 6,
            '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]',
            'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', 'run-1',
            '2026-05-24T00', '2026-05-24T00:00:00+00:00', '2026-05-24T03:00:00+00:00',
            '2026-05-24T04:15:00+00:00', 'VERIFIED', 'OK', 0, 1,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-05-24T05:00:00+00:00', 6, 'F', 0
        )
        """
    )
    # A market exists, but for a DIFFERENT family — Chicago|2026-05-24|high has none.
    forecasts_conn.execute(
        "INSERT INTO market_events (market_slug, city, target_date, temperature_metric) VALUES (?, ?, ?, ?)",
        ("denver-high-2026-05-24", "Denver", "2026-05-24", "high"),
    )

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=executable_forecast_live_eligible_reader(forecasts_conn),
    )

    # Market exists for another family -> filter active -> Chicago (no market) is NOT emitted.
    results = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )
    assert results == []
    assert world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0

    # Once Chicago's market appears, the same family emits on the next scan.
    forecasts_conn.execute(
        "INSERT INTO market_events (market_slug, city, target_date, temperature_metric) VALUES (?, ?, ?, ?)",
        ("chicago-high-2026-05-24", "Chicago", "2026-05-24", "high"),
    )
    results = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:18:00+00:00",
    )
    assert len(results) == 1
    assert world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1


def test_coverage_is_completeness_authority_over_partial_source_run():
    """Relationship: source_run_coverage is the WINDOW-SCOPED completeness authority.

    Cross-module invariant (the live Break-1 dual-authority defect): when the coverage
    layer says the target window is COMPLETE/LIVE_ELIGIBLE (window steps present, 51
    members, executable-reader live) the snapshot must classify COMPLETE even though the
    raw whole-run source_run reports completeness_status=PARTIAL/status=PARTIAL (its
    observed_members accounting is orphaned from the snapshot write under the OpenData
    5-day cap). The legacy whole-run authority must NOT veto a window-complete forecast.
    RED before Fix-B (source_complete also required source_run==COMPLETE → PARTIAL_ALLOWED).
    """
    result = classify_forecast_snapshot(
        source_run=_source_run(status="PARTIAL", completeness="PARTIAL"),
        coverage=_coverage(completeness="COMPLETE", readiness="LIVE_ELIGIBLE", members=51),
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        min_members_floor=40,
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    assert result.completeness_status == "COMPLETE"
    assert result.live_eligible is True


def test_window_guards_still_block_when_coverage_lies_about_steps():
    """Fix-B must NOT become a rubber stamp: even with coverage COMPLETE/LIVE_ELIGIBLE,
    if the window steps are NOT actually present the classification must not be COMPLETE.
    Guards required_steps_present / observed_members / reader_live remain load-bearing."""
    coverage = _coverage(completeness="COMPLETE", readiness="LIVE_ELIGIBLE", members=51)
    coverage["observed_steps_json"] = "[0,3]"  # missing step 6 → window incomplete
    result = classify_forecast_snapshot(
        source_run=_source_run(status="PARTIAL", completeness="PARTIAL"),
        coverage=coverage,
        snapshot=_snapshot(),
        decision_time=_decision_time(),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    assert result.completeness_status != "COMPLETE"


def test_scan_prioritizes_live_eligible_over_fresher_blocked_under_limit():
    """Relationship: emit selection must not be starved by recency. A near-date
    LIVE_ELIGIBLE coverage row must be emitted ahead of a FRESHER (newer computed_at)
    far-horizon BLOCKED row when the emit LIMIT is smaller than the candidate set.
    RED before Fix-1 (ORDER BY computed_at DESC picks the fresher BLOCKED row)."""
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    from src.state.db import init_schema_forecasts

    init_schema_forecasts(forecasts_conn)
    _MEMBERS = "[" + ",".join(str(i) for i in range(1, 52)) + "]"

    def _ins_run(run_id, city_id, comp, status):
        forecasts_conn.execute(
            """
            INSERT INTO source_run (
                source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
                source_cycle_time, source_available_at, captured_at, target_local_date,
                city_id, city_timezone, temperature_metric, dataset_id,
                expected_members, observed_members, expected_steps_json, observed_steps_json,
                completeness_status, status
            ) VALUES (?, 'ecmwf-open-data', 'ens', '2026-05-24T00', 'SCHEDULED_LIVE', 'SCHEDULED_LIVE',
                '2026-05-24T00:00:00+00:00', '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00',
                '2026-05-24', ?, 'America/Chicago', 'high', 'v1',
                51, 51, '[0,3,6]', '[0,3,6]', ?, ?)
            """,
            (run_id, city_id, comp, status),
        )

    def _ins_cov(cov_id, run_id, city_id, city, readiness, comp, computed_at):
        forecasts_conn.execute(
            """
            INSERT INTO source_run_coverage (
                coverage_id, source_run_id, source_id, source_transport, release_calendar_key, track,
                city_id, city, city_timezone, target_local_date, temperature_metric, physical_quantity,
                observation_field, data_version, expected_members, observed_members, expected_steps_json,
                observed_steps_json, snapshot_ids_json, target_window_start_utc, target_window_end_utc,
                completeness_status, readiness_status, computed_at, expires_at
            ) VALUES (?, ?, 'ecmwf-open-data', 'ensemble_snapshots_db_reader', '2026-05-24T00', 'ens',
                ?, ?, 'America/Chicago', '2026-05-24', 'high', 'temperature',
                'high_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', ?,
                '2026-05-24T05:00:00+00:00', '2026-05-25T05:00:00+00:00',
                ?, ?, ?, '2026-05-25T04:16:00+00:00')
            """,
            (cov_id, run_id, city_id, city, '[' + cov_id[-1] + ']', comp, readiness, computed_at),
        )

    def _ins_snap(snap_id, run_id, city):
        forecasts_conn.execute(
            """
            INSERT INTO ensemble_snapshots (
                snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
                issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
                model_version, dataset_id, source_id, source_transport, source_run_id,
                release_calendar_key, source_cycle_time, source_release_time, source_available_at,
                authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
                forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
                members_unit, raw_orderbook_hash_transition_delta_ms
            ) VALUES (?, ?, '2026-05-24', 'high', 'temperature', 'high_temp',
                '2026-05-24T00:00:00+00:00', '2026-05-24T06:00:00+00:00',
                '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00', 6, ?,
                'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', ?,
                '2026-05-24T00', '2026-05-24T00:00:00+00:00', '2026-05-24T03:00:00+00:00',
                '2026-05-24T04:15:00+00:00', 'VERIFIED', 'OK', 0, 1,
                'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-05-24T05:00:00+00:00', 6, 'F', 0)
            """,
            (snap_id, city, _MEMBERS, run_id),
        )

    # Eligible near-date row, OLDER computed_at.
    _ins_run("run-elig", "chicago", "COMPLETE", "SUCCESS")
    _ins_cov("cov-1", "run-elig", "chicago", "Chicago", "LIVE_ELIGIBLE", "COMPLETE", "2026-05-24T04:16:00+00:00")
    _ins_snap(1, "run-elig", "Chicago")
    # Far-horizon blocked row, FRESHER computed_at (would win recency ORDER BY).
    _ins_run("run-blk", "seattle", "PARTIAL", "PARTIAL")
    _ins_cov("cov-2", "run-blk", "seattle", "Seattle", "BLOCKED", "PARTIAL", "2026-05-24T04:30:00+00:00")
    _ins_snap(2, "run-blk", "Seattle")

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )
    results = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
        limit=1,
    )
    assert len(results) == 1
    import json as _json

    row = world_conn.execute("SELECT payload_json FROM opportunity_events").fetchone()
    assert row is not None, "no event emitted under limit=1"
    payload = _json.loads(row[0])
    assert payload["city"] == "Chicago", (
        f"emit starved the LIVE_ELIGIBLE near-date row; emitted {payload['city']} instead"
    )


def test_scan_committed_snapshot_blocks_future_coverage_computed_at():
    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.execute(
        """
        CREATE TABLE source_run (
            source_run_id TEXT PRIMARY KEY,
            source_id TEXT,
            track TEXT,
            source_cycle_time TEXT,
            source_issue_time TEXT,
            source_release_time TEXT,
            source_available_at TEXT,
            fetch_started_at TEXT,
            fetch_finished_at TEXT,
            captured_at TEXT,
            status TEXT,
            completeness_status TEXT,
            expected_steps_json TEXT,
            observed_steps_json TEXT,
            expected_members INTEGER,
            observed_members INTEGER
        )
        """
    )
    forecasts_conn.execute(
        """
        CREATE TABLE source_run_coverage (
            coverage_id TEXT PRIMARY KEY,
            source_run_id TEXT,
            source_id TEXT,
            source_transport TEXT,
            release_calendar_key TEXT,
            track TEXT,
            city_id TEXT,
            city TEXT,
            city_timezone TEXT,
            target_local_date TEXT,
            temperature_metric TEXT,
            data_version TEXT,
            expected_members INTEGER,
            observed_members INTEGER,
            expected_steps_json TEXT,
            observed_steps_json TEXT,
            snapshot_ids_json TEXT,
            target_window_start_utc TEXT,
            target_window_end_utc TEXT,
            completeness_status TEXT,
            readiness_status TEXT,
            computed_at TEXT,
            expires_at TEXT
        )
        """
    )
    forecasts_conn.execute(
        """
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            source_run_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            available_at TEXT,
            fetch_time TEXT,
            manifest_hash TEXT,
            members_json TEXT
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO source_run VALUES (
            'run-1', 'ecmwf-open-data', 'ens',
            '2026-05-24T00:00:00+00:00', NULL, NULL,
            '2026-05-24T04:15:00+00:00', NULL, NULL,
            '2026-05-24T04:16:00+00:00', 'SUCCESS', 'COMPLETE',
            '[0,3,6]', '[0,3,6]', 51, 51
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO source_run_coverage VALUES (
            'cov-1', 'run-1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader',
            '2026-05-24T00', 'ens', 'chicago', 'Chicago', 'America/Chicago',
            '2026-05-24', 'high', 'v1', 51, 51, '[0,3,6]', '[0,3,6]',
            '[1]', '2026-05-24T05:00:00+00:00', '2026-05-25T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', '2026-05-24T06:00:00+00:00',
            '2026-05-25T04:16:00+00:00'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots VALUES (
            1, 'run-1', 'Chicago', '2026-05-24', 'high',
            '2026-05-24T04:15:00+00:00', '2026-05-24T04:16:00+00:00',
            'hash-1',
            '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]'
        )
        """
    )
    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)
    trigger = ForecastSnapshotReadyTrigger(
        EventWriter(world_conn),
        live_eligibility_reader=lambda _sr, _cov, _snap, _now: True,
    )

    results = trigger.scan_committed_snapshots(
        forecasts_conn=forecasts_conn,
        decision_time=_decision_time(),
        received_at="2026-05-24T04:17:00+00:00",
    )

    assert results == []
    assert world_conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
