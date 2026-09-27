# Created: 2026-05-03
# Last reused/audited: 2026-09-27
# Lifecycle: created=2026-05-03; last_reviewed=2026-09-27; last_reused=2026-09-27
# Purpose: Lock executable forecast bundle source-run, coverage, readiness, and snapshot coherence.
# Reuse: Run for live-entry forecast reader, producer-readiness, source-cycle, or coverage-window changes.
# Authority basis: docs/archive/2026-Q2/task_2026-05-14_data_daemon_live_efficiency/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md
#   Phase 3 producer-readiness-only data daemon cutover path.
"""Executable forecast reader relationship tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

from src.contracts.ensemble_snapshot_provenance import (
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
)
from src.data import executable_forecast_reader
from src.data.executable_forecast_reader import read_executable_forecast, read_executable_forecast_snapshot
from src.data.forecast_target_contract import build_forecast_target_scope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state import db as state_db
from src.state.db import init_schema, init_schema_trade_only, init_schema_forecasts
from src.state.readiness_repo import write_readiness_state
from src.state.schema.v2_schema import apply_canonical_schema
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import write_source_run

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    apply_canonical_schema(conn)
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
    source_transport: str | None = "ensemble_snapshots_db_reader",
    source_run_id: str | None = "source-run-1",
    release_calendar_key: str | None = "ecmwf_open_data:mx2t6_high:full",
    source_cycle_time: str | None = "2026-05-03T00:00:00+00:00",
    source_release_time: str | None = "2026-05-03T08:05:00+00:00",
    available_at: str = "2026-05-03T08:10:00+00:00",
    authority: str = "VERIFIED",
    causality_status: str = "OK",
    boundary_ambiguous: int = 0,
    local_day_start_utc: str | None = None,
    contributes_to_target_extrema: int | None = 1,
    forecast_window_attribution_status: str | None = "FULLY_INSIDE_TARGET_LOCAL_DAY",
) -> None:
    scope = _scope()
    from tests.test_ingest_grib_source_run_context import _land_grid_proof
    surface = _land_grid_proof()
    surface["mask_source_fetched_at"] = "2026-05-03T00:00:00+00:00"
    provenance = {
        "city": "London", "nearest_grid_lat": 51.5, "nearest_grid_lon": 0.0,
        "contract_outcome_evidence": {"settlement_station_id": "EGLC"},
        "grid_surface_evidence": surface,
    }
    conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            city, target_date, temperature_metric, physical_quantity,
            observation_field, issue_time, valid_time, available_at, fetch_time,
            lead_hours, members_json, model_version, dataset_id,
            source_id, source_transport, source_run_id, release_calendar_key,
            source_cycle_time, source_release_time, source_available_at,
            training_allowed, causality_status, boundary_ambiguous,
            ambiguous_member_count, manifest_hash, provenance_json, authority,
            members_unit, local_day_start_utc, step_horizon_hours,
            contributes_to_target_extrema, forecast_window_attribution_status
        ) VALUES (
            :city, :target_date, :temperature_metric, :physical_quantity,
            :observation_field, :issue_time, :valid_time, :available_at, :fetch_time,
            :lead_hours, :members_json, :model_version, :data_version,
            :source_id, :source_transport, :source_run_id, :release_calendar_key,
            :source_cycle_time, :source_release_time, :source_available_at,
            :training_allowed, :causality_status, :boundary_ambiguous,
            :ambiguous_member_count, :manifest_hash, :provenance_json, :authority,
            :members_unit, :local_day_start_utc, :step_horizon_hours,
            :contributes_to_target_extrema, :forecast_window_attribution_status
        )
        """,
        {
            "city": scope.city_name,
            "target_date": scope.target_local_date.isoformat(),
            "temperature_metric": scope.temperature_metric,
            "physical_quantity": "mx2t6_local_calendar_day_max",
            "observation_field": "high_temp",
            "issue_time": source_cycle_time or "2026-05-03T00:00:00+00:00",
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
            "provenance_json": json.dumps(provenance),
            "authority": authority,
            "members_unit": "degC",
            "local_day_start_utc": local_day_start_utc or scope.target_window_start_utc.isoformat(),
            "step_horizon_hours": 144.0,
            "contributes_to_target_extrema": contributes_to_target_extrema,
            "forecast_window_attribution_status": forecast_window_attribution_status,
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
        source_transport="ensemble_snapshots_db_reader",
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


def _read_full(conn: sqlite3.Connection, *, require_entry_readiness: bool = True):
    scope = _scope()
    return read_executable_forecast(
        conn,
        city_id=scope.city_id,
        city_name=scope.city_name,
        city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric,
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_db_reader",
        data_version=scope.data_version,
        track="mx2t6_high_full_horizon",
        strategy_key="entry_forecast",
        market_family="family-1",
        condition_id="condition-123",
        decision_time=_utc(2026, 5, 3, 10),
        require_entry_readiness=require_entry_readiness,
    )


def test_reader_returns_only_source_linked_executable_snapshot() -> None:
    conn = _conn()
    _insert_snapshot(conn)

    result = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_db_reader",
        now_utc=_utc(2026, 5, 3, 9),
    )

    assert result.ok
    assert result.reason_code == "EXECUTABLE_FORECAST_READY"
    assert result.snapshot is not None
    assert result.snapshot.source_run_id == "source-run-1"
    assert len(result.snapshot.members) == 51


def test_live_reader_rejects_historical_opendata_and_unproven_current_shape() -> None:
    from dataclasses import replace
    from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION_V2

    conn = _conn()
    _insert_snapshot(conn)
    historical = read_executable_forecast_snapshot(
        conn, scope=replace(_scope(), data_version=ECMWF_OPENDATA_HIGH_DATA_VERSION_V2),
        source_id="ecmwf_open_data",
    )
    assert historical.status == "BLOCKED"
    assert historical.reason_code == "EXECUTABLE_FORECAST_GRID_SURFACE_REVISION_MISSING"
    conn.execute("UPDATE ensemble_snapshots SET provenance_json = '{}' ")
    unproven = read_executable_forecast_snapshot(
        conn, scope=_scope(), source_id="ecmwf_open_data",
    )
    assert unproven.status == "BLOCKED"
    assert unproven.reason_code == "EXECUTABLE_FORECAST_GRID_SURFACE_PROOF_MISSING"


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
        source_transport="ensemble_snapshots_db_reader",
    )

    assert not result.ok
    # Phase B7: reason now distinguishes "row exists but unlinked" from
    # "no row at all" instead of collapsing both into NO_EXECUTABLE_*.
    # SQL filters on source_id/source_transport prevent legacy NULL rows
    # from matching; row-exists-with-mismatch is the new shape this test
    # should verify, but the legacy fixture (source_id=None) still falls
    # to NO_ROWS because source_id="ecmwf_open_data" never matches NULL.
    assert result.reason_code == "NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET"
    assert result.snapshot is None


def test_reader_blocks_when_linked_columns_are_partially_null() -> None:
    """Phase B7: legacy row with source_id+transport set but later linkage
    columns NULL is now caught by the post-check as
    FORECAST_SOURCE_LINKAGE_MISSING (reachable reason code) rather than
    being silently filtered out by the SQL pre-filter.
    """

    conn = _conn()
    _insert_snapshot(
        conn,
        source_run_id=None,
        release_calendar_key=None,
        source_cycle_time=None,
        source_release_time=None,
    )

    result = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_db_reader",
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
        source_transport="ensemble_snapshots_db_reader",
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
        source_transport="ensemble_snapshots_db_reader",
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
        source_transport="ensemble_snapshots_db_reader",
    )
    assert unverified.reason_code == "EXECUTABLE_FORECAST_AUTHORITY_NOT_VERIFIED"

    conn = _conn()
    _insert_snapshot(conn, causality_status="REJECTED_BOUNDARY_AMBIGUOUS", boundary_ambiguous=1)
    non_causal = read_executable_forecast_snapshot(
        conn,
        scope=_scope(),
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_db_reader",
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
    # P0 follow-up §1.3: the bundle layer DERIVES producer_readiness_id from the
    # selected candidate's coverage_id (writer formula producer_readiness:{cov}),
    # so evidence coherence holds even though only the latest readiness_state row
    # survives in the DB.
    assert evidence.producer_readiness_id == "producer_readiness:coverage-1"
    assert evidence.entry_readiness_id == "entry-readiness-1"
    assert evidence.producer_readiness_id != evidence.entry_readiness_id
    ens_result = result.bundle.to_ens_result()
    assert ens_result["period_extrema_source"] == "local_calendar_day_member_extrema"
    assert ens_result["raw_payload_hash"] == "a" * 64
    assert ens_result["first_member_observed_time"] == "2026-05-03T08:10:00+00:00"
    assert ens_result["run_complete_time"] == "2026-05-03T08:15:00+00:00"
    assert ens_result["target_day_valid_window"] == (
        "2026-05-07T23:00:00+00:00",
        "2026-05-08T22:00:00+00:00",
    )
    assert ens_result["target_window_start_utc"] == "2026-05-07T23:00:00+00:00"
    assert ens_result["target_window_end_utc"] == "2026-05-08T22:00:00+00:00"


def test_bundle_valid_window_uses_coverage_end_not_fixed_24h_day() -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)
    conn.execute(
        """
        UPDATE source_run_coverage
        SET target_window_end_utc = ?
        WHERE coverage_id = ?
        """,
        ("2026-05-08T22:00:00+00:00", "coverage-1"),
    )

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    ens_result = result.bundle.to_ens_result()
    assert ens_result["target_day_valid_window"] == (
        "2026-05-07T23:00:00+00:00",
        "2026-05-08T21:00:00+00:00",
    )


def test_full_reader_prefers_attached_forecasts_authority_and_keeps_entry_local(tmp_path) -> None:
    conn = _conn()
    _insert_readiness(
        conn,
        strategy_key="entry_forecast",
        readiness_id="entry-readiness-1",
        market_family="family-1",
        condition_id="condition-123",
    )
    forecasts_path = tmp_path / "forecasts.db"
    forecasts_conn = sqlite3.connect(forecasts_path)
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)
    _insert_snapshot(forecasts_conn)
    _insert_source_run(forecasts_conn)
    _insert_coverage(forecasts_conn)
    _insert_readiness(
        forecasts_conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-forecast",
        dependency_json={"coverage_id": "coverage-1"},
    )
    forecasts_conn.commit()
    forecasts_conn.close()
    conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_path),))

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    evidence = result.bundle.evidence
    # Derived from coverage_id (see test above); the forecasts-attached path
    # resolves the SAME coverage-1 so the derived id is identical.
    assert evidence.producer_readiness_id == "producer_readiness:coverage-1"
    assert evidence.entry_readiness_id == "entry-readiness-1"


def test_full_reader_does_not_fallback_to_main_shadow_when_forecasts_attached(tmp_path) -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)
    forecasts_path = tmp_path / "forecasts.db"
    forecasts_conn = sqlite3.connect(forecasts_path)
    forecasts_conn.row_factory = sqlite3.Row
    init_schema_forecasts(forecasts_conn)
    forecasts_conn.commit()
    forecasts_conn.close()
    conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_path),))

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "PRODUCER_READINESS_MISSING"


def test_full_reader_prefers_canonical_forecasts_main_over_world_ghost(tmp_path, monkeypatch) -> None:
    forecasts_path = tmp_path / "zeus-forecasts.db"
    world_path = tmp_path / "zeus-world.db"
    monkeypatch.setattr(state_db, "ZEUS_FORECASTS_DB_PATH", forecasts_path)

    conn = sqlite3.connect(forecasts_path)
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    _insert_full_reader_fixture(conn)
    conn.commit()
    conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    for table in ("source_run", "source_run_coverage", "readiness_state", "ensemble_snapshots"):
        conn.execute(f"CREATE TABLE world.{table} AS SELECT * FROM main.{table} WHERE 0")

    result = _read_full(conn, require_entry_readiness=False)

    assert result.ok
    assert result.bundle is not None
    assert result.bundle.evidence.source_run_id == "source-run-1"

    conn.execute("DROP TABLE main.source_run")
    assert executable_forecast_reader._authority_table(conn, "source_run") is None
    assert not _read_full(conn, require_entry_readiness=False).ok
    conn.close()


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


def test_full_reader_can_consume_producer_readiness_without_entry_readiness() -> None:
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

    result = _read_full(conn, require_entry_readiness=False)

    assert result.ok
    assert result.bundle is not None
    evidence = result.bundle.evidence
    assert evidence.producer_readiness_id == "producer_readiness:coverage-1"
    assert evidence.entry_readiness_id is None


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


def test_full_reader_blocks_source_available_after_candidate_readiness() -> None:
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
    assert result.reason_code == "SOURCE_AVAILABLE_AFTER_PRODUCER_READINESS"


def _reader_for_metric(
    conn: sqlite3.Connection, metric: str, *,
    decision_hour: int = 10, profile: str = "full",
):
    if metric == "low":
        fields = {
            "temperature_metric": "low", "physical_quantity": "mn2t6_local_calendar_day_min",
            "observation_field": "low_temp", "dataset_id": ECMWF_OPENDATA_LOW_DATA_VERSION,
            "data_version": ECMWF_OPENDATA_LOW_DATA_VERSION,
        }
        for table in ("ensemble_snapshots", "source_run", "source_run_coverage", "readiness_state"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            updates = {key: val for key, val in fields.items() if key in columns}
            conn.execute(
                f"UPDATE {table} SET " + ", ".join(f"{key}=?" for key in updates),
                tuple(updates.values()),
            )
            for key in ("track", "release_calendar_key"):
                if key in columns:
                    conn.execute(
                        f"UPDATE {table} SET {key}=REPLACE({key}, ?, ?)",
                        ("mx2t6_high", "mn2t6_low"),
                    )
    return read_executable_forecast(
        conn,
        city_id="LONDON", city_name="London", city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8), temperature_metric=metric,
        source_id="ecmwf_open_data", source_transport="ensemble_snapshots_db_reader",
        data_version=(ECMWF_OPENDATA_HIGH_DATA_VERSION if metric == "high"
                      else ECMWF_OPENDATA_LOW_DATA_VERSION),
        track=("mx2t6_high" if metric == "high" else "mn2t6_low")
              + f"_{profile}_horizon",
        strategy_key="entry_forecast", market_family="family-1",
        condition_id="condition-123", decision_time=_utc(2026, 5, 3, decision_hour),
        require_entry_readiness=False,
    )


@pytest.mark.parametrize("metric", ("high", "low"))
def test_source_possession_after_capture_at_candidate_readiness_is_live(metric: str) -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)
    conn.execute(
        "UPDATE source_run SET source_available_at = ?, captured_at = ?",
        (_utc(2026, 5, 3, 8, 45).isoformat(), _utc(2026, 5, 3, 8, 20).isoformat()),
    )
    result = _reader_for_metric(conn, metric)
    assert result.ok and result.bundle is not None
    assert result.bundle.snapshot.snapshot_id == 1


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize(
    ("available_hour", "expected_reason"),
    ((9, "SOURCE_AVAILABLE_AFTER_PRODUCER_READINESS"),
     (11, "SOURCE_AVAILABLE_AFTER_DECISION_TIME")),
)
def test_source_possession_after_readiness_or_decision_is_blocked(
    metric: str, available_hour: int, expected_reason: str,
) -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)
    conn.execute("UPDATE source_run SET source_available_at = ?", (
        _utc(2026, 5, 3, available_hour).isoformat(),
    ))
    result = _reader_for_metric(conn, metric)
    assert not result.ok
    assert result.reason_code == expected_reason


@pytest.mark.parametrize("metric", ("high", "low"))
def test_future_candidate_coverage_cannot_authorize_past_decision(metric: str) -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)
    conn.execute(
        "UPDATE source_run_coverage SET computed_at = ?",
        (_utc(2026, 5, 3, 10, 1).isoformat(),),
    )
    result = _reader_for_metric(conn, metric)
    assert not result.ok
    assert result.reason_code == "PRODUCER_COVERAGE_AFTER_DECISION_TIME"


@pytest.mark.parametrize("metric", ("high", "low"))
def test_later_short_noncontributor_yields_to_earlier_full_live_candidate(metric: str) -> None:
    """A blocked later run cannot hide an earlier proved, still-live target day."""
    conn = _conn()
    _insert_full_reader_fixture(conn)
    conn.execute(
        "UPDATE source_run SET captured_at=?, source_available_at=?",
        (_utc(2026, 5, 3, 8, 20).isoformat(),
         _utc(2026, 5, 3, 8, 45).isoformat()),
    )
    scope = _scope()
    later = _utc(2026, 5, 3, 6)
    later_scope = build_forecast_target_scope(
        city_id=scope.city_id, city_name=scope.city_name,
        city_timezone=scope.city_timezone, target_local_date=scope.target_local_date,
        temperature_metric=scope.temperature_metric, source_cycle_time=later,
        data_version=scope.data_version,
    )
    _insert_snapshot(
        conn, source_run_id="source-run-short",
        release_calendar_key="ecmwf_open_data:mx2t6_high:short",
        source_cycle_time=later.isoformat(),
        available_at=_utc(2026, 5, 3, 9, 20).isoformat(),
        contributes_to_target_extrema=0,
        forecast_window_attribution_status="UNKNOWN",
    )
    write_source_run(
        conn, source_run_id="source-run-short", source_id="ecmwf_open_data",
        track="mx2t6_high_short_horizon",
        release_calendar_key="ecmwf_open_data:mx2t6_high:short",
        source_cycle_time=later, source_issue_time=later,
        source_release_time=_utc(2026, 5, 3, 9),
        source_available_at=_utc(2026, 5, 3, 9, 20),
        fetch_started_at=_utc(2026, 5, 3, 9),
        fetch_finished_at=_utc(2026, 5, 3, 9, 10),
        captured_at=_utc(2026, 5, 3, 9, 15),
        imported_at=_utc(2026, 5, 3, 9, 25),
        target_local_date=scope.target_local_date,
        city_id=scope.city_id, city_timezone=scope.city_timezone,
        temperature_metric="high", physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp", data_version=scope.data_version,
        expected_members=51, observed_members=51,
        expected_steps_json=later_scope.required_step_hours,
        observed_steps_json=later_scope.required_step_hours,
        completeness_status="COMPLETE", status="SUCCESS",
        raw_payload_hash="a" * 64, manifest_hash="b" * 64,
    )
    snapshot_id = conn.execute(
        "SELECT snapshot_id FROM ensemble_snapshots WHERE source_run_id=?",
        ("source-run-short",),
    ).fetchone()[0]
    write_source_run_coverage(
        conn, coverage_id="coverage-short", source_run_id="source-run-short",
        source_id="ecmwf_open_data", source_transport="ensemble_snapshots_db_reader",
        release_calendar_key="ecmwf_open_data:mx2t6_high:short",
        track="mx2t6_high_short_horizon", city_id=scope.city_id, city=scope.city_name,
        city_timezone=scope.city_timezone, target_local_date=scope.target_local_date,
        temperature_metric="high", physical_quantity="mx2t6_local_calendar_day_max",
        observation_field="high_temp", data_version=scope.data_version,
        expected_members=51, observed_members=51,
        expected_steps_json=later_scope.required_step_hours,
        observed_steps_json=later_scope.required_step_hours,
        snapshot_ids_json=[snapshot_id],
        target_window_start_utc=later_scope.target_window_start_utc,
        target_window_end_utc=later_scope.target_window_end_utc,
        completeness_status="PARTIAL", readiness_status="BLOCKED",
        reason_code="EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA",
        computed_at=_utc(2026, 5, 3, 9, 25), expires_at=None,
    )
    write_readiness_state(
        conn, readiness_id="producer-short", scope_type="city_metric",
        status="BLOCKED", computed_at=_utc(2026, 5, 3, 9, 25),
        city_id=scope.city_id, city=scope.city_name, city_timezone=scope.city_timezone,
        target_local_date=scope.target_local_date, temperature_metric="high",
        physical_quantity="mx2t6_local_calendar_day_max", observation_field="high_temp",
        data_version=scope.data_version, source_id="ecmwf_open_data",
        track="mx2t6_high_short_horizon", source_run_id="source-run-short",
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        reason_codes_json=["EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA"],
        dependency_json={"coverage_id": "coverage-short"},
        provenance_json={"contract": "LiveEntryForecastTargetContract.v1"},
    )

    result = _reader_for_metric(conn, metric, profile="short")
    assert result.ok and result.bundle is not None
    assert result.bundle.evidence.source_run_id == "source-run-1"
    assert result.bundle.snapshot.snapshot_id == 1


def test_full_reader_blocks_unparseable_source_cycle_time_before_snapshot_read() -> None:
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(conn)
    conn.execute(
        "UPDATE source_run SET source_cycle_time = '' WHERE source_run_id = 'source-run-1'"
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
    assert result.reason_code == "SOURCE_CYCLE_TIME_UNPARSEABLE"


def test_full_reader_preserves_source_cycle_time_distinct_from_decision_time() -> None:
    conn = _conn()
    _insert_full_reader_fixture(conn)

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    assert result.bundle.evidence.source_cycle_time == "2026-05-03T00:00:00+00:00"


def test_full_reader_blocks_snapshot_local_day_window_mismatch() -> None:
    conn = _conn()
    _insert_snapshot(conn, local_day_start_utc="2026-05-07T00:00:00+00:00")
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

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH"


# ---------------------------------------------------------------------------
# Readiness-timing cross-clock race antibody (2026-05-19 alpha-loss postmortem):
# Producer readiness rows are written by ECMWF ingest (a separate cycle from
# the trading cycle); the ON CONFLICT(scope_key) DO UPDATE pattern in
# write_readiness_state UPSERTs `computed_at` in place. When ingest fires
# mid-cycle, the row's computed_at is replaced with the writer's wall-clock
# at write time. The trading cycle holds a frozen `decision_time` from cycle
# start; comparing the row's UPSERTed computed_at against that frozen `now`
# trips a cross-clock race that fires READINESS_TIMING_ORDER_INVALID for
# every candidate market — even though the readiness row was, by design, a
# valid, fresh row that the cycle should consume.
#
# Daemon decision_log id=1116 (2026-05-19T19:36-19:50 opening_hunt) rejected
# 50/52 cities with this code; at probe time the latest readiness row had
# computed_at=2026-05-19T20:05:42, +25 minutes ahead of the cycle's frozen
# decision_time of 19:36:22.
#
# Relationship invariant: _evaluate_executable_forecast's causal-order check
# must enforce only the within-write-transaction relations
# (captured_at <= producer_computed_at <= entry_computed_at). The historical
# clause `entry_computed_at > now` mixed two clocks (writer's wall-clock vs
# cycle's frozen now) and produced false positives; it has been removed.
# Sed-flip: restore `or entry_computed_at > now` to the line-763 conjunction
# → both tests below go RED.
# ---------------------------------------------------------------------------


def test_full_reader_accepts_producer_readiness_upserted_after_decision_time() -> None:
    """Antibody: a producer_readiness row whose computed_at is after the
    cycle's frozen decision_time must still produce LIVE_ELIGIBLE — the row
    was a normal UPSERT by the ingest writer, not a malformed input."""
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(
        conn,
        source_available_at=_utc(2026, 5, 3, 8, 0),
        captured_at=_utc(2026, 5, 3, 8, 20),
    )
    _insert_coverage(conn)
    # Producer readiness written by ingest AFTER cycle.now=10:00 (race window).
    _insert_readiness(
        conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-upserted-future",
        computed_at=_utc(2026, 5, 3, 10, 5),  # +5 minutes ahead of decision_time
        dependency_json={"coverage_id": "coverage-1"},
    )
    _insert_readiness(
        conn,
        strategy_key="entry_forecast",
        readiness_id="entry-readiness-1",
        market_family="family-1",
        condition_id="condition-123",
        computed_at=_utc(2026, 5, 3, 10, 5),
    )

    result = _read_full(conn)  # decision_time = _utc(2026, 5, 3, 10) = 10:00

    assert result.ok, (
        f"Cross-clock race antibody FAIL: result.ok={result.ok}, "
        f"reason_code={result.reason_code!r}. Expected LIVE_ELIGIBLE. "
        f"The historical entry_computed_at > now clause has been restored "
        f"and is rejecting a valid mid-cycle UPSERT."
    )
    assert result.reason_code == "EXECUTABLE_FORECAST_READY"


def test_full_reader_still_blocks_genuine_causal_order_violation() -> None:
    """Antibody: removing `entry_computed_at > now` must NOT weaken the
    genuine causal-order check captured_at <= producer_computed_at. A row
    whose producer claims to have been computed BEFORE its own source was
    captured is a real data-integrity violation and must still BLOCK."""
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(
        conn,
        source_available_at=_utc(2026, 5, 3, 8, 0),
        captured_at=_utc(2026, 5, 3, 9, 30),  # capture at 09:30
    )
    _insert_coverage(conn)
    # Producer claims to have computed BEFORE its own data was captured —
    # genuine causal violation; must block.
    _insert_readiness(
        conn,
        strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
        readiness_id="producer-readiness-acausal",
        computed_at=_utc(2026, 5, 3, 9, 0),  # earlier than captured_at
        dependency_json={"coverage_id": "coverage-1"},
    )
    _insert_readiness(
        conn,
        strategy_key="entry_forecast",
        readiness_id="entry-readiness-1",
        market_family="family-1",
        condition_id="condition-123",
        computed_at=_utc(2026, 5, 3, 9, 0),
    )

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "READINESS_TIMING_ORDER_INVALID", (
        f"Genuine causal-order check FAIL: got reason_code={result.reason_code!r}, "
        f"expected READINESS_TIMING_ORDER_INVALID. The captured_at > producer_computed_at "
        f"check has been weakened along with the > now removal."
    )


# ── F1: coverage target_window relationship tests ─────────────────────────────


def test_coverage_target_window_flows_unchanged_into_scope() -> None:
    """Relationship test: Module A (coverage write) → Module B (reader) window
    invariant. The target_window_start/end_utc stored in the coverage row must
    flow UNCHANGED into the ForecastTargetScope and not be silently replaced by
    the query-time `now`.

    Proven by: happy-path read succeeds when snapshot.local_day_start_utc
    matches the known coverage window (Europe/London 2026-05-08 = 2026-05-07T23:00
    in UTC).  The window is distinct from decision_time (2026-05-03T10:00) — if
    `or now` ever substituted, the downstream snapshot-vs-coverage window equality
    check would BLOCK instead of passing.
    """
    conn = _conn()
    scope = _scope()
    # London BST: 2026-05-08 local midnight = 2026-05-07T23:00:00+00:00 UTC
    expected_window_start = scope.target_window_start_utc
    _insert_snapshot(conn, local_day_start_utc=expected_window_start.isoformat())
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

    result = _read_full(conn)

    assert result.ok, (
        f"F1 relationship invariant FAIL: coverage window={expected_window_start.isoformat()} "
        f"failed to flow through into scope; got reason_code={result.reason_code!r}. "
        f"The `or now` fallback may have substituted the wrong window."
    )
    assert result.reason_code == "EXECUTABLE_FORECAST_READY"


def test_coverage_null_target_window_fail_closes() -> None:
    """Relationship test: when target_window_start/end_utc in the coverage row
    cannot be parsed (empty, malformed, or naive timestamp), the reader MUST
    fail-closed with COVERAGE_WINDOW_UNPARSEABLE rather than silently
    substituting `now` and producing a scope with a wrong window that passes
    all downstream checks.

    This is the F1 category-closing antibody.  Before the fix the silent
    fallback continued with scope.target_window_start_utc = decision_time,
    which emitted SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH (wrong stage/code) at a
    later gate instead of aborting immediately with an honest reason code.
    """
    conn = _conn()
    _insert_snapshot(conn)
    _insert_source_run(conn)
    _insert_coverage(conn)
    # Corrupt both target_window columns to unparseable values (empty strings
    # satisfy NOT NULL but fail _parse_utc — same failure mode as a row written
    # under a schema version that predates the columns being populated).
    conn.execute(
        "UPDATE source_run_coverage SET target_window_start_utc = '', "
        "target_window_end_utc = '' WHERE coverage_id = 'coverage-1'"
    )
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

    assert not result.ok, (
        "F1 fail-closed FAIL: read succeeded with unparseable coverage window. "
        "The `or now` fallback substituted query-time instead of blocking."
    )
    assert result.reason_code == "COVERAGE_WINDOW_UNPARSEABLE", (
        f"F1 fail-closed FAIL: expected reason_code='COVERAGE_WINDOW_UNPARSEABLE', "
        f"got {result.reason_code!r}. The wrong-stage code means the silent "
        f"now-substitution is still active and the early-abort guard is missing."
    )
