# Created: 2026-05-15
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_data_pipeline_live_rootfix/DATA_PIPELINE_ROOTFIX_PLAN.md live verifier process-ownership gate.
"""Tests for the live data-pipeline E2E verifier."""

from __future__ import annotations

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
