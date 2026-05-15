# Created: 2026-05-02
# Last reused/audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b job-run provenance contract; docs/operations/task_2026-05-08_deep_alignment_audit/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md Phase 6 forecast-live durable work journaling.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.db import init_schema, init_schema_forecasts
from src.state.job_run_repo import get_job_run, get_latest_job_run, write_job_run


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_job_run_schema_creates_required_columns() -> None:
    conn = _conn()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_run)")}

    assert {"job_run_id", "job_name", "plane", "scheduled_for", "status", "source_run_id"} <= columns


def test_forecasts_schema_creates_job_run_for_forecast_live_work_journal() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)

    write_job_run(
        conn,
        job_run_id="forecast-live-job-1",
        job_name="forecast_live_opendata_mx2t6_high",
        plane="forecast",
        scheduled_for=datetime(2026, 5, 14, 0, tzinfo=timezone.utc),
        status="SKIPPED_NOT_RELEASED",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        release_calendar_key="ecmwf_open_data:mx2t6_high:full",
        safe_fetch_not_before=datetime(2026, 5, 14, 8, 5, tzinfo=timezone.utc),
        expected_scope_json={"source_cycle_time": "2026-05-14T00:00:00+00:00"},
    )

    row = get_job_run(conn, "forecast-live-job-1")
    assert row is not None
    assert row["job_name"] == "forecast_live_opendata_mx2t6_high"
    assert row["source_id"] == "ecmwf_open_data"
    assert row["release_calendar_key"] == "ecmwf_open_data:mx2t6_high:full"


def test_job_run_logical_scope_includes_release_calendar_key() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    scheduled_for = datetime(2026, 5, 14, 0, tzinfo=timezone.utc)

    for key in ("ecmwf_open_data:mx2t6_high:full", "ecmwf_open_data:mx2t6_high:short"):
        write_job_run(
            conn,
            job_run_id=f"job-{key.rsplit(':', 1)[-1]}",
            job_name="forecast_live_opendata_mx2t6_high",
            plane="forecast",
            scheduled_for=scheduled_for,
            status="SUCCESS",
            source_id="ecmwf_open_data",
            track="mx2t6_high",
            release_calendar_key=key,
        )

    rows = conn.execute(
        """
        SELECT job_run_id, job_run_key, release_calendar_key
        FROM job_run
        WHERE job_name = ?
        ORDER BY release_calendar_key
        """,
        ("forecast_live_opendata_mx2t6_high",),
    ).fetchall()

    assert [row["job_run_id"] for row in rows] == ["job-full", "job-short"]
    assert all(row["release_calendar_key"] in row["job_run_key"] for row in rows)


def test_job_run_schema_rebuilds_legacy_unique_scope_to_release_key_identity() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE job_run (
            job_run_id TEXT PRIMARY KEY,
            job_run_key TEXT NOT NULL UNIQUE,
            job_name TEXT NOT NULL,
            plane TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            missed_from TEXT,
            started_at TEXT,
            finished_at TEXT,
            lock_key TEXT,
            lock_acquired_at TEXT,
            status TEXT NOT NULL,
            reason_code TEXT,
            rows_written INTEGER NOT NULL DEFAULT 0,
            rows_failed INTEGER NOT NULL DEFAULT 0,
            source_run_id TEXT,
            source_id TEXT,
            track TEXT,
            release_calendar_key TEXT,
            safe_fetch_not_before TEXT,
            expected_scope_json TEXT NOT NULL DEFAULT '{}',
            affected_scope_json TEXT NOT NULL DEFAULT '{}',
            readiness_impacts_json TEXT NOT NULL DEFAULT '[]',
            readiness_recomputed_at TEXT,
            meta_json TEXT NOT NULL DEFAULT '{}',
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_name, scheduled_for, source_id, track)
        );
    """)

    init_schema_forecasts(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='job_run'"
    ).fetchone()[0]

    assert "UNIQUE(job_name, scheduled_for, source_id, track, release_calendar_key)" in " ".join(sql.split())


def test_job_run_repo_round_trips_scope_and_readiness_impacts() -> None:
    conn = _conn()
    scheduled_for = datetime(2026, 5, 2, 8, tzinfo=timezone.utc)

    write_job_run(
        conn,
        job_run_id="job-1",
        job_name="forecast_ecmwf_high",
        plane="forecast",
        scheduled_for=scheduled_for,
        status="SUCCESS",
        rows_written=24,
        source_run_id="src-run-1",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        expected_scope_json={"cities": ["LONDON"]},
        affected_scope_json={"city_id": "LONDON"},
        readiness_impacts_json=[{"readiness_id": "ready-1", "status": "LIVE_ELIGIBLE"}],
        meta_json={"lock_owner": "test"},
    )

    row = get_job_run(conn, "job-1")
    assert row is not None
    assert row["scheduled_for"] == scheduled_for.isoformat()
    assert row["rows_written"] == 24
    assert json.loads(row["readiness_impacts_json"])[0]["readiness_id"] == "ready-1"


def test_latest_job_run_orders_by_scheduled_window() -> None:
    conn = _conn()
    for hour in (6, 8):
        write_job_run(
            conn,
            job_run_id=f"job-{hour}",
            job_name="forecast_ecmwf_high",
            plane="forecast",
            scheduled_for=datetime(2026, 5, 2, hour, tzinfo=timezone.utc),
            status="SUCCESS",
        )

    assert get_latest_job_run(conn, "forecast_ecmwf_high")["job_run_id"] == "job-8"


def test_job_run_logical_scope_upsert_overwrites_same_window() -> None:
    conn = _conn()
    scheduled_for = datetime(2026, 5, 2, 8, tzinfo=timezone.utc)

    write_job_run(
        conn,
        job_run_id="job-green",
        job_name="forecast_ecmwf_high",
        plane="forecast",
        scheduled_for=scheduled_for,
        status="SUCCESS",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
    )
    write_job_run(
        conn,
        job_run_id="job-red",
        job_name="forecast_ecmwf_high",
        plane="forecast",
        scheduled_for=scheduled_for,
        status="FAILED",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        reason_code="FETCH_FAILED",
    )

    rows = conn.execute("SELECT * FROM job_run WHERE job_name = 'forecast_ecmwf_high'").fetchall()
    assert len(rows) == 1
    assert rows[0]["job_run_id"] == "job-red"
    assert rows[0]["status"] == "FAILED"


def test_job_run_repo_rejects_unknown_status_before_sqlite() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="invalid job_run status"):
        write_job_run(
            conn,
            job_run_id="job-bad",
            job_name="forecast_ecmwf_high",
            plane="forecast",
            scheduled_for=datetime(2026, 5, 2, 8, tzinfo=timezone.utc),
            status="DONE",
        )
