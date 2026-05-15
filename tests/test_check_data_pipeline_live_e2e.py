# Created: 2026-05-15
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_data_pipeline_live_rootfix/DATA_PIPELINE_ROOTFIX_PLAN.md live verifier process-ownership gate.
"""Tests for the live data-pipeline E2E verifier."""

from __future__ import annotations

from datetime import date
import sqlite3

from scripts import check_data_pipeline_live_e2e as checker


def test_forecast_live_owner_matcher_ignores_pytest_command() -> None:
    command = (
        "python3 -m pytest -q tests/test_forecast_live_daemon.py "
        "tests/test_check_data_pipeline_live_e2e.py"
    )

    assert checker._is_forecast_live_owner_command(command) is False
    assert checker._is_tracked_live_process_command(command) is False


def test_forecast_live_owner_matcher_accepts_daemon_module_launch() -> None:
    command = "/opt/homebrew/bin/python3 -m src.ingest.forecast_live_daemon"

    assert checker._is_forecast_live_owner_command(command) is True
    assert checker._is_tracked_live_process_command(command) is True


def test_forecast_owner_gate_does_not_count_test_command_as_dedicated_owner() -> None:
    processes = [
        {
            "command": "/opt/homebrew/bin/python3 -m src.main",
            "cwd": "/repo",
        },
        {
            "command": "/opt/homebrew/bin/python3 -m src.ingest_main",
            "cwd": "/repo",
        },
        {
            "command": "python3 -m pytest -q tests/test_forecast_live_daemon.py",
            "cwd": "/repo",
        },
    ]

    checks = {
        check.name: check
        for check in checker._build_checks(
            processes=processes,
            latest_source_run=None,
            target_range={},
            reader_probe={"ok": False, "status": "BLOCKED", "reason_code": "TEST", "elapsed_ms": 0.0},
            evaluator_guard={"ok": True, "reason_code": "OK", "path": "/repo/src/engine/evaluator.py"},
        )
    }

    assert checks["single_forecast_owner"].status == "PASS"
    assert checks["dedicated_forecast_owner"].status == "FAIL"


def test_forecast_owner_gate_treats_demoted_ingest_as_non_owner() -> None:
    processes = [
        {
            "command": "/opt/homebrew/bin/python3 -m src.main",
            "cwd": "/repo",
        },
        {
            "command": "/opt/homebrew/bin/python3 -m src.ingest_main",
            "cwd": "/repo",
            "env": {"ZEUS_FORECAST_LIVE_OWNER": "forecast_live"},
        },
        {
            "command": "/opt/homebrew/bin/python3 -m src.ingest.forecast_live_daemon",
            "cwd": "/repo",
        },
    ]

    checks = {
        check.name: check
        for check in checker._build_checks(
            processes=processes,
            latest_source_run=None,
            target_range={},
            reader_probe={"ok": False, "status": "BLOCKED", "reason_code": "TEST", "elapsed_ms": 0.0},
            evaluator_guard={"ok": True, "reason_code": "OK", "path": "/repo/src/engine/evaluator.py"},
        )
    }

    assert checks["single_forecast_owner"].status == "PASS"
    assert checks["dedicated_forecast_owner"].status == "PASS"
    assert checks["legacy_ingest_opendata_demoted"].status == "PASS"


def test_candidate_snapshot_uses_live_eligible_coverage_not_first_snapshot() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ':memory:' AS forecasts")
    conn.executescript(
        """
        CREATE TABLE forecasts.source_run (
            source_run_id TEXT PRIMARY KEY,
            captured_at TEXT,
            source_cycle_time TEXT
        );
        CREATE TABLE forecasts.source_run_coverage (
            source_run_id TEXT,
            source_id TEXT,
            source_transport TEXT,
            city TEXT,
            target_local_date TEXT,
            temperature_metric TEXT,
            data_version TEXT,
            readiness_status TEXT
        );
        CREATE TABLE forecasts.ensemble_snapshots_v2 (
            snapshot_id INTEGER PRIMARY KEY,
            source_run_id TEXT,
            source_id TEXT,
            source_transport TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            data_version TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO forecasts.source_run VALUES (?, ?, ?)",
        ("ecmwf_open_data:mn2t6_low:2026-05-15T00Z", "2026-05-15T10:12:11+00:00", "2026-05-15T00:00:00+00:00"),
    )
    rows = [
        ("London", "BLOCKED", 1),
        ("New York", "LIVE_ELIGIBLE", 2),
    ]
    for city, status, snapshot_id in rows:
        conn.execute(
            "INSERT INTO forecasts.source_run_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ecmwf_open_data:mn2t6_low:2026-05-15T00Z",
                "ecmwf_open_data",
                "ensemble_snapshots_v2_db_reader",
                city,
                "2026-05-15",
                "low",
                "ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
                status,
            ),
        )
        conn.execute(
            "INSERT INTO forecasts.ensemble_snapshots_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id,
                "ecmwf_open_data:mn2t6_low:2026-05-15T00Z",
                "ecmwf_open_data",
                "ensemble_snapshots_v2_db_reader",
                city,
                "2026-05-15",
                "low",
                "ecmwf_opendata_mn2t3_local_calendar_day_min_v1",
            ),
        )

    candidate = checker._candidate_snapshot(
        conn,
        "ecmwf_open_data:mn2t6_low:2026-05-15T00Z",
        date(2026, 5, 15),
    )

    assert candidate is not None
    assert candidate["city"] == "New York"
    assert candidate["snapshot_id"] == 2
