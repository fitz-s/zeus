# Created: 2026-05-02
# Last reused/audited: 2026-09-23
# Authority basis: docs/operations/task_2026-05-02_data_daemon_readiness/PLAN.md PR45b source-run provenance contract.

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

import pytest

from src.state.db import init_schema
from src.state.source_run_repo import get_latest_source_run, get_source_run, write_source_run


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def test_source_run_schema_creates_scope_and_completeness_columns() -> None:
    conn = _conn()

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_run)")}

    assert {
        "source_run_id",
        "source_id",
        "track",
        "source_cycle_time",
        "expected_members",
        "observed_members",
        "completeness_status",
        "partial_run",
    } <= columns


def test_source_run_coverage_hwm_uses_run_identity_index() -> None:
    from src.data.replacement_input_hwm import (
        ensemble_source_authority_predicate,
    )

    conn = _conn()
    conn.execute(
        "CREATE TABLE ensemble_snapshots ("
        "snapshot_id INTEGER PRIMARY KEY, source_run_id TEXT, city TEXT, "
        "target_date TEXT, temperature_metric TEXT)"
    )
    predicate = ensemble_source_authority_predicate(
        conn,
        ensemble_alias="ensemble_snapshot",
        decision_time=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    assert predicate is not None
    sql, params = predicate
    plan = " ".join(
        str(row[3])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM ensemble_snapshots "
            f"AS ensemble_snapshot WHERE {sql}",
            params,
        )
    )
    assert "sqlite_autoindex_source_run_coverage_2" in plan


def test_ensemble_source_authority_rejects_complete_run_without_target_coverage() -> None:
    from src.data.replacement_input_hwm import ensemble_source_authority_sql

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE source_run (
            source_run_id TEXT PRIMARY KEY,
            status TEXT,
            completeness_status TEXT,
            partial_run INTEGER,
            imported_at TEXT
        );
        CREATE TABLE ensemble_snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            source_run_id TEXT
        );
        INSERT INTO source_run VALUES (
            'complete-but-uncovered', 'SUCCESS', 'COMPLETE', 0,
            '2026-09-22T18:30:00+00:00'
        );
        INSERT INTO ensemble_snapshots VALUES (1365052, 'complete-but-uncovered');
        """
    )
    predicate, params = ensemble_source_authority_sql(
        ensemble_alias="ensemble_snapshot",
        source_run_ref="source_run",
        source_run_clock_columns=("imported_at",),
        coverage_ref=None,
        decision_time=datetime(2026, 9, 22, 19, tzinfo=timezone.utc),
    )
    assert conn.execute(
        "SELECT snapshot_id FROM ensemble_snapshots AS ensemble_snapshot WHERE "
        + predicate,
        params,
    ).fetchone() is None
    conn.close()


def test_source_run_repo_round_trips_complete_run() -> None:
    conn = _conn()
    cycle = datetime(2026, 5, 2, 0, tzinfo=timezone.utc)

    write_source_run(
        conn,
        source_run_id="src-run-1",
        source_id="ecmwf_open_data",
        track="mx2t6_high",
        release_calendar_key="ecmwf_open_data:mx2t6_high",
        source_cycle_time=cycle,
        status="SUCCESS",
        completeness_status="COMPLETE",
        city_id="LONDON",
        city_timezone="Europe/London",
        target_local_date=date(2026, 5, 3),
        temperature_metric="high",
        data_version="forecast_v2",
        expected_members=51,
        observed_members=51,
        expected_steps_json=[6, 12, 18, 24],
        observed_steps_json=[6, 12, 18, 24],
        raw_payload_hash="sha256:abc",
    )

    row = get_source_run(conn, "src-run-1")
    assert row is not None
    assert row["source_cycle_time"] == cycle.isoformat()
    assert row["completeness_status"] == "COMPLETE"
    assert json.loads(row["expected_steps_json"]) == [6, 12, 18, 24]


def test_source_run_partial_flag_requires_partial_completeness() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="partial_run requires"):
        write_source_run(
            conn,
            source_run_id="src-run-bad",
            source_id="ecmwf_open_data",
            track="mx2t6_high",
            release_calendar_key="ecmwf_open_data:mx2t6_high",
            source_cycle_time=datetime(2026, 5, 2, 0, tzinfo=timezone.utc),
            status="SUCCESS",
            completeness_status="COMPLETE",
            partial_run=True,
        )


def test_latest_source_run_orders_by_source_cycle_time() -> None:
    conn = _conn()
    for hour in (0, 6):
        write_source_run(
            conn,
            source_run_id=f"src-run-{hour}",
            source_id="ecmwf_open_data",
            track="mx2t6_high",
            release_calendar_key="ecmwf_open_data:mx2t6_high",
            source_cycle_time=datetime(2026, 5, 2, hour, tzinfo=timezone.utc),
            status="SUCCESS",
            completeness_status="COMPLETE",
        )

    assert get_latest_source_run(conn, "ecmwf_open_data", "mx2t6_high")["source_run_id"] == "src-run-6"
