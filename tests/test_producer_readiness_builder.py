# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 7 producer-readiness builder.
"""Producer-readiness builder relationship tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.forecast_target_contract import build_forecast_target_scope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY, build_producer_readiness_for_scope
from src.state.db import init_schema
from src.state.readiness_repo import get_readiness_state
from src.state.source_run_coverage_repo import write_source_run_coverage

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
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


def _write_coverage(
    conn: sqlite3.Connection,
    *,
    completeness_status: str = "COMPLETE",
    readiness_status: str = "LIVE_ELIGIBLE",
    reason_code: str | None = None,
    coverage_id: str = "coverage-live-1",
    expires_at: datetime | None = _utc(2026, 5, 3, 12),
    source_run_id: str = "source-run-1",
    release_calendar_key: str = "ecmwf_open_data:mx2t6_high:full",
    computed_at: datetime = _utc(2026, 5, 3, 9),
) -> None:
    scope = _scope()
    write_source_run_coverage(
        conn,
        coverage_id=coverage_id,
        source_run_id=source_run_id,
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        release_calendar_key=release_calendar_key,
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
        observed_members=51 if completeness_status == "COMPLETE" else 40,
        expected_steps_json=scope.required_step_hours,
        observed_steps_json=scope.required_step_hours if completeness_status == "COMPLETE" else scope.required_step_hours[:-1],
        snapshot_ids_json=["snap-1"],
        target_window_start_utc=scope.target_window_start_utc,
        target_window_end_utc=scope.target_window_end_utc,
        completeness_status=completeness_status,
        readiness_status=readiness_status,
        reason_code=reason_code,
        computed_at=computed_at,
        expires_at=expires_at,
    )


def test_complete_live_coverage_writes_live_eligible_producer_readiness() -> None:
    conn = _conn()
    _write_coverage(conn)

    decision = build_producer_readiness_for_scope(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        track="mx2t6_high_full_horizon",
        computed_at=_utc(2026, 5, 3, 10),
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.coverage_id == "coverage-live-1"
    assert decision.reason_codes == ("PRODUCER_COVERAGE_READY",)
    row = get_readiness_state(conn, decision.readiness_id)
    assert row is not None
    assert row["strategy_key"] == PRODUCER_READINESS_STRATEGY_KEY
    assert row["source_run_id"] == "source-run-1"
    assert row["expires_at"] == "2026-05-03T12:00:00+00:00"
    dependency = json.loads(row["dependency_json"])
    assert dependency["coverage_id"] == "coverage-live-1"
    assert dependency["source_transport"] == "ensemble_snapshots_v2_db_reader"


def test_release_calendar_key_keeps_same_track_coverage_from_crossing_profiles() -> None:
    conn = _conn()
    _write_coverage(conn)
    _write_coverage(
        conn,
        coverage_id="coverage-short-newer",
        source_run_id="source-run-short",
        release_calendar_key="ecmwf_open_data:mx2t6_high:short",
        computed_at=_utc(2026, 5, 3, 11),
        expires_at=_utc(2026, 5, 3, 13),
    )

    decision = build_producer_readiness_for_scope(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        track="mx2t6_high_full_horizon",
        computed_at=_utc(2026, 5, 3, 11),
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
    )

    assert decision.status == "LIVE_ELIGIBLE"
    assert decision.coverage_id == "coverage-live-1"


def test_missing_future_target_coverage_writes_blocked_producer_readiness() -> None:
    conn = _conn()

    decision = build_producer_readiness_for_scope(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        track="mx2t6_high_full_horizon",
        computed_at=_utc(2026, 5, 3, 10),
    )

    assert decision.status == "BLOCKED"
    assert decision.coverage_id is None
    assert decision.reason_codes == ("NO_FUTURE_TARGET_DATE_COVERAGE",)
    row = get_readiness_state(conn, decision.readiness_id)
    assert row is not None
    assert row["status"] == "BLOCKED"
    assert json.loads(row["reason_codes_json"]) == ["NO_FUTURE_TARGET_DATE_COVERAGE"]
    assert json.loads(row["dependency_json"])["required_step_hours"] == list(_scope().required_step_hours)


def test_partial_coverage_overwrites_producer_readiness_to_blocked() -> None:
    conn = _conn()
    _write_coverage(
        conn,
        completeness_status="PARTIAL",
        readiness_status="BLOCKED",
        reason_code="MISSING_REQUIRED_STEPS",
        coverage_id="coverage-partial-1",
        expires_at=None,
    )

    decision = build_producer_readiness_for_scope(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        track="mx2t6_high_full_horizon",
        computed_at=_utc(2026, 5, 3, 10),
    )

    assert decision.status == "BLOCKED"
    assert decision.reason_codes == ("MISSING_REQUIRED_STEPS",)
    row = get_readiness_state(conn, decision.readiness_id)
    assert row is not None
    assert row["expires_at"] is None
    assert json.loads(row["reason_codes_json"]) == ["MISSING_REQUIRED_STEPS"]


def test_shadow_only_coverage_is_not_live_eligible() -> None:
    conn = _conn()
    _write_coverage(
        conn,
        readiness_status="SHADOW_ONLY",
        reason_code="CALIBRATION_TRANSFER_SHADOW_ONLY",
        coverage_id="coverage-shadow-1",
        expires_at=None,
    )

    decision = build_producer_readiness_for_scope(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        track="mx2t6_high_full_horizon",
        computed_at=_utc(2026, 5, 3, 10),
    )

    assert decision.status == "SHADOW_ONLY"
    assert decision.reason_codes == ("CALIBRATION_TRANSFER_SHADOW_ONLY",)
    row = get_readiness_state(conn, decision.readiness_id)
    assert row is not None
    assert row["status"] == "SHADOW_ONLY"
