# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_live_entry_data_contract/PLAN_v4.md Phase 3 source_run_coverage contract.
"""source_run_coverage schema and repo contract tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from src.state.db import init_schema
from src.state.source_run_coverage_repo import (
    get_latest_source_run_coverage,
    get_source_run_coverage,
    write_source_run_coverage,
)

UTC = timezone.utc


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _coverage_kwargs(**overrides: object) -> dict[str, object]:
    computed_at = datetime(2026, 5, 3, 9, tzinfo=UTC)
    data: dict[str, object] = {
        "coverage_id": "coverage-1",
        "source_run_id": "src-run-20260503-00z-high",
        "source_id": "ecmwf_open_data",
        "source_transport": "ensemble_snapshots_v2_db_reader",
        "release_calendar_key": "ecmwf_open_data_mx2t6_high:00z-full",
        "track": "mx2t6_high_full_horizon",
        "city_id": "LONDON",
        "city": "London",
        "city_timezone": "Europe/London",
        "target_local_date": date(2026, 5, 8),
        "temperature_metric": "high",
        "physical_quantity": "temperature_2m",
        "observation_field": "high_temp",
        "data_version": "ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        "expected_members": 51,
        "observed_members": 51,
        "expected_steps_json": [126, 132, 138, 144],
        "observed_steps_json": [126, 132, 138, 144],
        "snapshot_ids_json": [101],
        "target_window_start_utc": datetime(2026, 5, 7, 23, tzinfo=UTC),
        "target_window_end_utc": datetime(2026, 5, 8, 23, tzinfo=UTC),
        "completeness_status": "COMPLETE",
        "readiness_status": "LIVE_ELIGIBLE",
        "reason_code": None,
        "computed_at": computed_at,
        "expires_at": computed_at + timedelta(hours=1),
    }
    data.update(overrides)
    return data


def test_source_run_coverage_schema_keys_future_target_date() -> None:
    conn = _conn()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_run_coverage)")}

    assert {
        "coverage_id",
        "source_run_id",
        "source_id",
        "source_transport",
        "release_calendar_key",
        "track",
        "city_id",
        "city_timezone",
        "target_local_date",
        "temperature_metric",
        "expected_steps_json",
        "observed_steps_json",
        "snapshot_ids_json",
        "completeness_status",
        "readiness_status",
        "expires_at",
    } <= columns


def test_source_run_coverage_repo_round_trips_future_scope() -> None:
    conn = _conn()

    write_source_run_coverage(conn, **_coverage_kwargs())

    row = get_source_run_coverage(conn, "coverage-1")
    assert row is not None
    assert row["target_local_date"] == "2026-05-08"
    assert row["source_transport"] == "ensemble_snapshots_v2_db_reader"
    assert json.loads(row["expected_steps_json"]) == [126, 132, 138, 144]
    assert json.loads(row["snapshot_ids_json"]) == [101]


def test_source_run_coverage_unique_scope_keeps_transport_and_track_dimensions() -> None:
    conn = _conn()

    write_source_run_coverage(conn, **_coverage_kwargs())
    write_source_run_coverage(
        conn,
        **_coverage_kwargs(
            coverage_id="coverage-2",
            source_transport="shadow_transport",
            track="mx2t6_high_shadow",
        ),
    )

    count = conn.execute("SELECT COUNT(*) FROM source_run_coverage").fetchone()[0]
    assert count == 2


def test_latest_source_run_coverage_filters_executable_identity() -> None:
    conn = _conn()
    write_source_run_coverage(conn, **_coverage_kwargs())
    write_source_run_coverage(
        conn,
        **_coverage_kwargs(
            coverage_id="coverage-short-cycle",
            release_calendar_key="ecmwf_open_data_mx2t6_high:06z-short",
            track="mx2t6_high_short_horizon",
            computed_at=datetime(2026, 5, 3, 10, tzinfo=UTC),
            expires_at=datetime(2026, 5, 3, 11, tzinfo=UTC),
        ),
    )

    row = get_latest_source_run_coverage(
        conn,
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 8),
        temperature_metric="high",
        source_id="ecmwf_open_data",
        source_transport="ensemble_snapshots_v2_db_reader",
        data_version="ecmwf_opendata_mx2t6_local_calendar_day_max_v1",
        track="mx2t6_high_full_horizon",
        release_calendar_key="ecmwf_open_data_mx2t6_high:00z-full",
    )

    assert row is not None
    assert row["coverage_id"] == "coverage-1"


def test_live_eligible_source_run_coverage_requires_expiry() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="LIVE_ELIGIBLE"):
        write_source_run_coverage(conn, **_coverage_kwargs(expires_at=None))


def test_live_eligible_source_run_coverage_requires_timezone_aware_expiry() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="expires_at must be timezone-aware"):
        write_source_run_coverage(
            conn,
            **_coverage_kwargs(expires_at=datetime(2026, 5, 3, 10)),
        )


def test_high_and_low_tracks_same_cycle_keep_independent_coverage_rows() -> None:
    conn = _conn()
    write_source_run_coverage(conn, **_coverage_kwargs())
    write_source_run_coverage(
        conn,
        **_coverage_kwargs(
            coverage_id="coverage-low-1",
            source_run_id="src-run-20260503-00z-low",
            track="mn2t6_low_full_horizon",
            temperature_metric="low",
            physical_quantity="mn2t6_local_calendar_day_min",
            observation_field="low_temp",
            data_version="ecmwf_opendata_mn2t6_local_calendar_day_min_v1",
        ),
    )

    count = conn.execute("SELECT COUNT(*) FROM source_run_coverage").fetchone()[0]
    tracks = {
        row["track"]
        for row in conn.execute("SELECT track FROM source_run_coverage")
    }
    assert count == 2
    assert tracks == {"mx2t6_high_full_horizon", "mn2t6_low_full_horizon"}


def test_source_run_coverage_rejects_unknown_completeness_status() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="completeness_status"):
        write_source_run_coverage(
            conn,
            **_coverage_kwargs(
                coverage_id="coverage-bad",
                completeness_status="STALE",
                readiness_status="BLOCKED",
                expires_at=None,
            ),
        )
