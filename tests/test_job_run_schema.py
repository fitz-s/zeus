# Created: 2026-05-02
# Last reused/audited: 2026-05-02
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b job-run provenance contract.

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.db import init_schema
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
