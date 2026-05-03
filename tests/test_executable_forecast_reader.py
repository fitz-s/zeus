# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 8 executable forecast reader.
"""Executable forecast reader relationship tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.executable_forecast_reader import read_executable_forecast_snapshot
from src.data.forecast_target_contract import build_forecast_target_scope
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_v2_schema

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


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
    assert result.reason_code == "FORECAST_SOURCE_LINKAGE_MISSING"
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
    assert result.reason_code == "EXECUTABLE_FORECAST_SOURCE_TRANSPORT_MISMATCH"


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
