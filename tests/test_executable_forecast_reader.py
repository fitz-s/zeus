# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 8 executable forecast reader.
"""Executable forecast reader relationship tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.executable_forecast_reader import read_executable_forecast, read_executable_forecast_snapshot
from src.data.forecast_target_contract import build_forecast_target_scope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema
from src.state.readiness_repo import write_readiness_state
from src.state.schema.v2_schema import apply_v2_schema
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _scope():
    return build_forecast_target_scope(
        city_id="LONDON",
        city_name="London",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_cycle_time=_utc(2026, 5, 3),
        data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION,
        market_refs=("condition-123",),
    )


def _insert_snapshot(
    conn: sqlite3.Connection,
    *,
    source_id: str | None = "ecmwf_open_data",
    source_transport: str | None = "ensemble_snapshots_v2_db_reader",
    source_run_id: str | None = "source-run-1",
    release_calendar_key: str | None = "ecmwf_open_data:mx2t6_high:full",
    source_cycle_time: str | None = "2026-05-03T00:00:00+00:00",
    source_release_time: str | None = "2026-05-03T08:05:00+00:00",
    available_at: str = "2026-05-03T08:10:00+00:00",
    authority: str = "VERIFIED",
    causality_status: str = "OK",
    boundary_ambiguous: int = 0,
) -> None:
    scope = _scope()
    conn.execute(
        """
        INSERT INTO ensemble_snapshots_v2 (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, model_version, data_version,
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, source_available_at,
            training_allowed, causality_status, boundary_ambiguous,
            ambiguous_member_count, manifest_hash, provenance_json, authority,
            members_unit, local_day_start_utc, step_horizon_hours
        ) VALUES (
            :city, :target_date, :temperature_metric, :physical_quantity,
            :observation_field, :issue_time, :valid_time, :available_at, :fetch_time,
            :lead_hours, :members_json, :model_version, :data_version,
            :source_id, :source_transport, :source_run_id, :release_calendar_key,
            :source_cycle_time, :source_release_time, :source_available_at,
            :training_allowed, :causality_status, :boundary_ambiguous,
            :ambiguous_member_count, :manifest_hash, :provenance_json, :authority,
            :members_unit, :local_day_start_utc, :step_horizon_hours
        )
        """,
        {
            "city": scope.city_name,
            "target_date": scope.target_local_date.isoformat(),
            "temperature_metric": scope.temperature_metric,
            "physical_quantity": "mx2t6_local_calendar_day_max",
            "observation_field": "high_temp",
            "issue_time": "2026-05-03T00:00:00+00:00",
            "valid_time": scope.target_local_date.isoformat(),
            "available_at": available_at,
            "fetch_time": "2026-05-03T08:15:00+00:00",
            "lead_hours": 120.0,
            "members_json": json.dumps([18.0 + i * 0.1 for i in range(51)]),
            "model_version": "ecmwf_ens",
            "data_version": scope.data_version,
            "source_id": source_id,
            "source_transport": source_transport,
            "source_run_id": source_run_id,
            "release_calendar_key": release_calendar_key,
            "source_cycle_time": source_cycle_time,
            "source_release_time": source_release_time,
            "source_available_at": available_at,
            "training_allowed": 1,
            "causality_status": causality_status,
            "boundary_ambiguous": boundary_ambiguous,
            "ambiguous_member_count": 0,
            "manifest_hash": "2" * 64,
            "provenance_json": "{}",
            "authority": authority,
            "members_unit": "degC",
            "local_day_start_utc": scope.target_window_start_utc.isoformat(),
            "step_horizon_hours": 144.0,
        },
    )


def _insert_source_run(
    conn: sqlite3.Connection,
    *,
    status: str = "SUCCESS",
    completeness_status: str = "COMPLETE",
    captured_at: datetime | None = _utc(2026, 5, 3, 8, 20),
    source_available_at: datetime = _utc(2026, 5, 3, 8, 10),
) -> None:
    scope = _scope()
    write_source_run(
        conn,
        source_run_id="source-run-1",
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        source_cycle_time=_utc(2026, 5, 3),
        source_issue_time=_utc(2026, 5, 3),
        source_release_time=_utc(2026, 5, 3, 8, 5),
        source_available_at=source_available_at,
        fetch_started_at=_utc(2026, 5, 3, 8, 10),
        fetch_finished_at=_utc(2026, 5, 3, 8, 15),
        captured_at=captured_at,
        imported_at=_utc(2026, 5, 3, 8, 30),
        target_local_date=scope.target_local_date,
        city_id=scope.city_id,
        city_timezone=scope.city_timezone,
        temperature_metric=scope.temperature_metric,
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=scope.data_version,
        expected_members=51,
        observed_members=51,
        expected_steps_json=scope.required_step_hours,
        observed_steps_json=scope.required_step_hours,
        completeness_status=completeness_status,
        status=status,
        raw_payload_hash="a" * 64,
        manifest_hash="b" * 64,
    )


def _insert_coverage(
    conn: sqlite3.Connection,
    *,
    completeness_status: str = "COMPLETE",
    readiness_status: str = "LIVE_ELIGIBLE",
    observed_steps_json: list[int] | None = None,
    observed_members: int = 51,
) -> None:
    scope = _scope()
    write_source_run_coverage(
        conn,
        coverage_id="coverage-1",
        source_run_id="source-run-1",
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        track="mx2t6_high_full_horizon",
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=scope.data_version,
        expected_members=51,
        observed_members=observed_members,
        expected_steps_json=scope.required_step_hours,
        observed_steps_json=observed_steps_json if observed_steps_json is not None else list(scope.required_step_hours),
        snapshot_ids_json=[1],
        target_window_start_utc=scope.target_window_start_utc,
        target_window_end_utc=scope.target_window_end_utc,
        completeness_status=completeness_status,
        readiness_status=readiness_status,
        computed_at=_utc(2026, 5, 3, 8, 45),
        expires_at=_utc(2026, 5, 3, 12) if readiness_status == "LIVE_ELIGIBLE" else None,
    )


def _insert_readiness(
    conn: sqlite3.Connection,
    *,
    strategy_key: str,
    readiness_id: str,
    market_family: str | None = None,
    condition_id: str | None = None,
    computed_at: datetime = _utc(2026, 5, 3, 9),
    expires_at: datetime | None = _utc(2026, 5, 3, 12),
    dependency_json: dict | None = None,
) -> None:
    scope = _scope()
    write_readiness_state(
        conn,
        readiness_id=readiness_id,
        scope_type="city_metric",
        status="LIVE_ELIGIBLE",
        computed_at=computed_at,
        expires_at=expires_at,
        city_id=scope.city_id,
        city=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp",
        data_version=scope.data_version,
        source_id="ecmwf_open_data",
        track="mx2t6_high_full_horizon",
        source_run_id="source-run-1",
        strategy_key=strategy_key,
        market_family=market_family,
        condition_id=condition_id,
        reason_codes_json=["READY"],
        dependency_json=dependency_json or {},
        provenance_json={"contract": "LiveEntryForecastTargetContract.v1"},
    )


def _insert_full_reader_fixture(conn: sqlite3.Connection) -> None:
    _insert_snapshot(conn)
    _insert_source_run(conn)
    _insert_coverage(conn)
    _insert_readiness(
        conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-1",
        dependency_json={"coverage_id": "coverage-1"},
    )
    _insert_readiness(
        conn,
        strategy_key="entry_forecast",
        readiness_id="entry-readiness-1",
        market_family="family-1",
        condition_id="condition-123",
    )


def _read_full(conn: sqlite3.Connection):
    scope = _scope()
    return read_executable_forecast(
        conn,
        city_id=scope.city_id,
        city_name=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        data_version=scope.data_version,
        track="mx2t6_high_full_horizon",
        strategy_key="entry_forecast",
        market_family="family-1",
        condition_id="condition-123",
        decision_time=_utc(2026, 5, 3, 10),
    )


def test_reader_returns_only_source_linked_executable_snapshot() -> None:
    conn = _conn()
    _insert_snapshot(conn)

    result = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        now_utc=_utc(2026, 5, 3, 9),
    )

    assert result.ok
    assert result.reason_code == "EXECUTABLE_FORECAST_READY"
    assert result.snapshot is not None
    assert result.snapshot.source_run_id == "source-run-1"
    assert len(result.snapshot.members) == 51


def test_reader_blocks_legacy_rows_without_source_linkage() -> None:
    conn = _conn()
    _insert_snapshot(
        conn,
        source_id=None,
        source_transport=None,
        source_run_id=None,
        release_calendar_key=None,
        source_cycle_time=None,
        source_release_time=None,
    )

    result = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
    )

    assert not result.ok
    assert result.reason_code == "NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET"
    assert result.snapshot is None


def test_reader_blocks_wrong_transport_even_with_same_source_family() -> None:
    conn = _conn()
    _insert_snapshot(conn, source_transport="direct_fetch")

    result = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET"


def test_reader_blocks_rows_not_available_at_decision_time() -> None:
    conn = _conn()
    _insert_snapshot(conn, available_at="2026-05-03T10:00:00+00:00")

    result = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        now_utc=_utc(2026, 5, 3, 9),
    )

    assert result.status == "BLOCKED"
    assert result.reason_code == "EXECUTABLE_FORECAST_NOT_AVAILABLE_YET"


def test_reader_blocks_non_verified_or_non_causal_rows() -> None:
    conn = _conn()
    _insert_snapshot(conn, authority="UNVERIFIED")

    unverified = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
    )
    assert unverified.reason_code == "EXECUTABLE_FORECAST_AUTHORITY_NOT_VERIFIED"

    conn = _conn()
    _insert_snapshot(conn, causality_status="REJECTED_BOUNDARY_AMBIGUOUS", boundary_ambiguous=1)
    non_causal = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
    )
    assert non_causal.reason_code == "EXECUTABLE_FORECAST_CAUSALITY_NOT_OK"


def test_full_reader_returns_evidence_bundle_with_separate_readiness_ids() -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    evidence = result.bundle.evidence
    assert evidence.coverage_id == "coverage-1"
    assert evidence.producer_readiness_id == "producer-readiness-1"
    assert evidence.entry_readiness_id == "entry-readiness-1"
    assert evidence.producer_readiness_id != evidence.entry_readiness_id
    ens_result = result.bundle.to_ens_result()
    assert ens_result["period_extrema_source"] == "local_calendar_day_member_extrema"
    assert ens_result["raw_payload_hash"] == "a" * 64


def test_full_reader_blocks_missing_entry_readiness() -> None:
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(conn)
    _insert_coverage(conn)
    _insert_readiness(
        conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-1",
        dependency_json={"coverage_id": "coverage-1"},
    )

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "READINESS_MISSING"


def test_full_reader_blocks_failed_source_run() -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)
    _insert_source_run(conn, status="FAILED", completeness_status="MISSING")

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "SOURCE_RUN_FAILED"


def test_full_reader_blocks_missing_required_steps() -> None:
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(conn)
    _insert_coverage(conn, observed_steps_json=list(_scope().required_step_hours[:-1]))
    _insert_readiness(
        conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-1",
        dependency_json={"coverage_id": "coverage-1"},
    )
    _insert_readiness(
        conn,
        strategy_key="entry_forecast",
        readiness_id="entry-readiness-1",
        market_family="family-1",
        condition_id="condition-123",
    )

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "MISSING_REQUIRED_STEPS"


def test_full_reader_blocks_expired_entry_readiness() -> None:
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(conn)
    _insert_coverage(conn)
    _insert_readiness(
        conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-1",
        dependency_json={"coverage_id": "coverage-1"},
    )
    _insert_readiness(
        conn,
        strategy_key="entry_forecast",
        readiness_id="entry-readiness-1",
        market_family="family-1",
        condition_id="condition-123",
        expires_at=_utc(2026, 5, 3, 9),
    )

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "READINESS_EXPIRED"


def test_full_reader_blocks_source_available_after_capture() -> None:
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(
        conn,
        source_available_at=_utc(2026, 5, 3, 9),
        captured_at=_utc(2026, 5, 3, 8, 20),
    )
    _insert_coverage(conn)
    _insert_readiness(
        conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-1",
        dependency_json={"coverage_id": "coverage-1"},
    )
    _insert_readiness(
        conn,
        strategy_key="entry_forecast",
        readiness_id="entry-readiness-1",
        market_family="family-1",
        condition_id="condition-123",
    )

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "SOURCE_AVAILABLE_AFTER_CAPTURE"
