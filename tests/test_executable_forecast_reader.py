# Created: 2026-05-03
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 8 executable forecast reader; docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md Phase 5 forecast authority chain ownership.
"""Executable forecast reader relationship tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from src.contracts.ensemble_snapshot_provenance import ECMWF_OPENDATA_HIGH_DATA_VERSION
from src.data.executable_forecast_reader import read_executable_forecast, read_executable_forecast_snapshot
from src.data.forecast_target_contract import build_forecast_target_scope
from src.data.producer_readiness import PRODUCER_READINESS_STRATEGY_KEY
from src.state.db import init_schema, init_schema_forecasts
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


def _file_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _forecasts_file_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    return conn


def _attached_trade_world_conn(tmp_path) -> tuple[sqlite3.Connection, sqlite3.Connection, Path]:
    trade_path = tmp_path / "trade.db"
    world_path = tmp_path / "world.db"
    trade_conn = _file_conn(trade_path)
    world_conn = _file_conn(world_path)
    return trade_conn, world_conn, world_path


def _attached_trade_world_forecasts_conn(
    tmp_path,
) -> tuple[sqlite3.Connection, sqlite3.Connection, sqlite3.Connection, Path, Path]:
    trade_path = tmp_path / "trade.db"
    world_path = tmp_path / "world.db"
    forecasts_path = tmp_path / "forecasts.db"
    trade_conn = _file_conn(trade_path)
    world_conn = _file_conn(world_path)
    forecasts_conn = _forecasts_file_conn(forecasts_path)
    return trade_conn, world_conn, forecasts_conn, world_path, forecasts_path


def _attach_world(trade_conn: sqlite3.Connection, world_conn: sqlite3.Connection, world_path: Path) -> sqlite3.Connection:
    world_conn.commit()
    world_conn.close()
    trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    return trade_conn


def _attach_world_and_forecasts(
    trade_conn: sqlite3.Connection,
    world_conn: sqlite3.Connection,
    forecasts_conn: sqlite3.Connection,
    world_path: Path,
    forecasts_path: Path,
) -> sqlite3.Connection:
    world_conn.commit()
    world_conn.close()
    forecasts_conn.commit()
    forecasts_conn.close()
    trade_conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
    trade_conn.execute("ATTACH DATABASE ? AS forecasts", (str(forecasts_path),))
    return trade_conn


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
    local_day_start_utc: str | None = None,
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
            "local_day_start_utc": local_day_start_utc or scope.target_window_start_utc.isoformat(),
            "step_horizon_hours": 144.0,
        },
    )


def _insert_source_run(
    conn: sqlite3.Connection,
    *,
    source_run_id: str = "source-run-1",
    status: str = "SUCCESS",
    completeness_status: str = "COMPLETE",
    captured_at: datetime | None = _utc(2026, 5, 3, 8, 20),
    source_available_at: datetime = _utc(2026, 5, 3, 8, 10),
) -> None:
    scope = _scope()
    write_source_run(
        conn,
        source_run_id=source_run_id,
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
    coverage_id: str = "coverage-1",
    source_run_id: str = "source-run-1",
    completeness_status: str = "COMPLETE",
    readiness_status: str = "LIVE_ELIGIBLE",
    observed_steps_json: list[int] | None = None,
    observed_members: int = 51,
) -> None:
    scope = _scope()
    write_source_run_coverage(
        conn,
        coverage_id=coverage_id,
        source_run_id=source_run_id,
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
    source_run_id: str = "source-run-1",
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
        source_run_id=source_run_id,
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
    _insert_entry_readiness(conn)


def _insert_world_owned_fixture(
    conn: sqlite3.Connection,
    *,
    source_run_id: str = "source-run-1",
    coverage_id: str = "coverage-1",
    producer_readiness_id: str = "producer-readiness-1",
    include_snapshot: bool = True,
    include_source_run: bool = True,
    include_coverage: bool = True,
    include_producer_readiness: bool = True,
) -> None:
    if include_snapshot:
        _insert_snapshot(conn, source_run_id=source_run_id)
    if include_source_run:
        _insert_source_run(conn, source_run_id=source_run_id)
    if include_coverage:
        _insert_coverage(conn, coverage_id=coverage_id, source_run_id=source_run_id)
    if include_producer_readiness:
        _insert_readiness(
            conn,
            strategy_key=PRODUCER_READINESS_STRATEGY_KEY,
            readiness_id=producer_readiness_id,
            source_run_id=source_run_id,
            dependency_json={"coverage_id": coverage_id},
        )


def _insert_entry_readiness(conn: sqlite3.Connection) -> None:
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


def test_full_reader_prefers_attached_world_for_source_authority_and_keeps_entry_local(tmp_path) -> None:
    trade_conn, world_conn, world_path = _attached_trade_world_conn(tmp_path)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn)
    conn = _attach_world(trade_conn, world_conn, world_path)

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    evidence = result.bundle.evidence
    assert evidence.producer_readiness_id == "producer-readiness-1"
    assert evidence.coverage_id == "coverage-1"
    assert evidence.source_run_id == "source-run-1"
    assert evidence.entry_readiness_id == "entry-readiness-1"


def test_full_reader_prefers_attached_forecasts_for_source_authority_and_keeps_entry_local(tmp_path) -> None:
    trade_conn, world_conn, forecasts_conn, world_path, forecasts_path = _attached_trade_world_forecasts_conn(tmp_path)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(
        world_conn,
        source_run_id="world-source-run-1",
        coverage_id="world-coverage-1",
        producer_readiness_id="world-producer-readiness-1",
    )
    _insert_world_owned_fixture(
        forecasts_conn,
        source_run_id="forecast-source-run-1",
        coverage_id="forecast-coverage-1",
        producer_readiness_id="forecast-producer-readiness-1",
    )
    conn = _attach_world_and_forecasts(trade_conn, world_conn, forecasts_conn, world_path, forecasts_path)

    result = _read_full(conn)

    assert result.ok
    assert result.bundle is not None
    evidence = result.bundle.evidence
    assert evidence.producer_readiness_id == "forecast-producer-readiness-1"
    assert evidence.coverage_id == "forecast-coverage-1"
    assert evidence.source_run_id == "forecast-source-run-1"
    assert evidence.entry_readiness_id == "entry-readiness-1"


def test_full_reader_does_not_fallback_to_world_shadow_when_forecasts_producer_missing(tmp_path) -> None:
    trade_conn, world_conn, forecasts_conn, world_path, forecasts_path = _attached_trade_world_forecasts_conn(tmp_path)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn)
    _insert_world_owned_fixture(forecasts_conn, include_producer_readiness=False)
    conn = _attach_world_and_forecasts(trade_conn, world_conn, forecasts_conn, world_path, forecasts_path)

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "PRODUCER_READINESS_MISSING"


def test_full_reader_does_not_fallback_to_world_shadow_when_forecasts_coverage_missing(tmp_path) -> None:
    trade_conn, world_conn, forecasts_conn, world_path, forecasts_path = _attached_trade_world_forecasts_conn(tmp_path)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn)
    _insert_world_owned_fixture(forecasts_conn, include_coverage=False)
    conn = _attach_world_and_forecasts(trade_conn, world_conn, forecasts_conn, world_path, forecasts_path)

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "SOURCE_RUN_COVERAGE_MISSING"


def test_full_reader_does_not_fallback_to_world_shadow_when_forecasts_snapshot_missing(tmp_path) -> None:
    trade_conn, world_conn, forecasts_conn, world_path, forecasts_path = _attached_trade_world_forecasts_conn(tmp_path)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn)
    _insert_world_owned_fixture(forecasts_conn, include_snapshot=False)
    conn = _attach_world_and_forecasts(trade_conn, world_conn, forecasts_conn, world_path, forecasts_path)

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET"


def test_full_reader_does_not_fallback_to_trade_shadow_when_world_producer_missing(tmp_path) -> None:
    trade_conn, world_conn, world_path = _attached_trade_world_conn(tmp_path)
    _insert_world_owned_fixture(trade_conn)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn, include_producer_readiness=False)
    conn = _attach_world(trade_conn, world_conn, world_path)

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "PRODUCER_READINESS_MISSING"


def test_full_reader_does_not_fallback_to_trade_shadow_when_world_schema_lacks_tables(tmp_path) -> None:
    trade_conn = _file_conn(tmp_path / "trade.db")
    _insert_world_owned_fixture(trade_conn)
    _insert_entry_readiness(trade_conn)
    empty_world_path = tmp_path / "empty-world.db"
    empty_world = sqlite3.connect(empty_world_path)
    empty_world.close()
    trade_conn.execute("ATTACH DATABASE ? AS world", (str(empty_world_path),))

    result = _read_full(trade_conn)

    assert not result.ok
    assert result.reason_code == "PRODUCER_READINESS_MISSING"


def test_full_reader_does_not_fallback_to_trade_shadow_when_world_coverage_missing(tmp_path) -> None:
    trade_conn, world_conn, world_path = _attached_trade_world_conn(tmp_path)
    _insert_world_owned_fixture(trade_conn)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn, include_coverage=False)
    conn = _attach_world(trade_conn, world_conn, world_path)

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "SOURCE_RUN_COVERAGE_MISSING"


def test_full_reader_does_not_fallback_to_trade_shadow_when_world_source_run_missing(tmp_path) -> None:
    trade_conn, world_conn, world_path = _attached_trade_world_conn(tmp_path)
    _insert_world_owned_fixture(trade_conn)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn, include_source_run=False)
    conn = _attach_world(trade_conn, world_conn, world_path)

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "SOURCE_RUN_MISSING"


def test_full_reader_does_not_fallback_to_trade_shadow_when_world_snapshot_missing(tmp_path) -> None:
    trade_conn, world_conn, world_path = _attached_trade_world_conn(tmp_path)
    _insert_world_owned_fixture(trade_conn)
    _insert_entry_readiness(trade_conn)
    _insert_world_owned_fixture(world_conn, include_snapshot=False)
    conn = _attach_world(trade_conn, world_conn, world_path)

    result = _read_full(conn)

    assert not result.ok
    assert result.reason_code == "NO_EXECUTABLE_FORECAST_ROWS_FOR_TARGET"


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
