# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/entry.py + §3.5
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"Concurrent-tick lockfile"
"""
Tests for maintenance_worker.cli.entry and maintenance_worker.cli.scheduler_detect.

Covers:
- scheduler_detect.detect(): IN_PROCESS > SCHEDULED > MANUAL_CLI > default SCHEDULED
- cmd_run: returns EXIT_OK on clean tick, EXIT_LOCK_CONTENTION on contention
- cmd_dry_run: forces MANUAL_CLI invocation mode
- cmd_status: reads lockfile + sentinels, returns EXIT_OK
- cmd_init: creates dirs, returns EXIT_OK
- _TickLock: exclusive non-blocking flock, cleans up lockfile on exit
- main(): argparse subcommand routing
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maintenance_worker.cli.entry import (
    EXIT_LOCK_CONTENTION,
    EXIT_OK,
    EXIT_NO_CONFIG,
    LockContention,
    _TickLock,
    cmd_dry_run,
    cmd_init,
    cmd_run,
    cmd_status,
    main,
)
from maintenance_worker.cli.scheduler_detect import detect
from maintenance_worker.types.modes import InvocationMode
from maintenance_worker.types.specs import EngineConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, live_default: bool = False) -> EngineConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    git_dir = tmp_path / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    return EngineConfig(
        repo_root=tmp_path,
        state_dir=state_dir,
        evidence_dir=evidence_dir,
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.md",
        live_default=live_default,
        scheduler="launchd",
        notification_channel="file",
    )


def _mock_run_tick():
    """Return a context manager patch for run_tick that returns a clean TickResult mock."""
    mock_result = MagicMock()
    mock_result.skipped = False
    mock_result.summary_path = None
    return patch(
        "maintenance_worker.cli.entry.run_tick",
        return_value=mock_result,
    )


# ---------------------------------------------------------------------------
# scheduler_detect.detect()
# ---------------------------------------------------------------------------


def test_detect_in_process_takes_priority() -> None:
    """MAINTENANCE_IN_PROCESS=1 → IN_PROCESS regardless of other env."""
    with patch.dict(os.environ, {"MAINTENANCE_IN_PROCESS": "1", "MAINTENANCE_SCHEDULER": "1"}):
        assert detect() == InvocationMode.IN_PROCESS


def test_detect_scheduled_from_env() -> None:
    """MAINTENANCE_SCHEDULER=1 (no IN_PROCESS) → SCHEDULED."""
    env = {"MAINTENANCE_SCHEDULER": "1"}
    env.pop("MAINTENANCE_IN_PROCESS", None)
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("MAINTENANCE_IN_PROCESS", None)
        assert detect() == InvocationMode.SCHEDULED


def test_detect_manual_cli_from_tty() -> None:
    """sys.stdin.isatty() True (no scheduler env) → MANUAL_CLI."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MAINTENANCE_IN_PROCESS", None)
        os.environ.pop("MAINTENANCE_SCHEDULER", None)
        with patch("maintenance_worker.cli.scheduler_detect.sys") as mock_sys:
            mock_sys.stdin = MagicMock()
            mock_sys.stdin.isatty.return_value = True
            assert detect() == InvocationMode.MANUAL_CLI


def test_detect_scheduled_default_no_tty() -> None:
    """No env vars, no TTY → default SCHEDULED."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MAINTENANCE_IN_PROCESS", None)
        os.environ.pop("MAINTENANCE_SCHEDULER", None)
        with patch("maintenance_worker.cli.scheduler_detect.sys") as mock_sys:
            mock_sys.stdin = MagicMock()
            mock_sys.stdin.isatty.return_value = False
            assert detect() == InvocationMode.SCHEDULED


def test_detect_returns_invocation_mode_enum() -> None:
    """detect() returns an InvocationMode instance."""
    with patch.dict(os.environ, {"MAINTENANCE_IN_PROCESS": "1"}):
        result = detect()
    assert isinstance(result, InvocationMode)


# ---------------------------------------------------------------------------
# _TickLock
# ---------------------------------------------------------------------------


def test_tick_lock_acquires_and_releases(tmp_path: Path) -> None:
    """_TickLock creates lockfile, writes PID, removes on exit."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lockfile = state_dir / "maintenance_worker.pid"

    with _TickLock(state_dir):
        assert lockfile.exists(), "Lockfile must exist while lock is held"
        pid_text = lockfile.read_text().strip()
        assert pid_text == str(os.getpid()), f"Lockfile must contain current PID, got {pid_text!r}"

    assert not lockfile.exists(), "Lockfile must be removed after lock release"


def test_tick_lock_contention_raises(tmp_path: Path) -> None:
    """Second _TickLock on same state_dir raises LockContention."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    with _TickLock(state_dir):
        with pytest.raises(LockContention):
            with _TickLock(state_dir):
                pass  # should not reach here


def test_tick_lock_cleanup_on_exception(tmp_path: Path) -> None:
    """Lockfile is removed even if the body raises."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lockfile = state_dir / "maintenance_worker.pid"

    with pytest.raises(ValueError):
        with _TickLock(state_dir):
            raise ValueError("body error")

    assert not lockfile.exists(), "Lockfile must be cleaned up even on exception"


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------


def test_cmd_run_returns_exit_ok(tmp_path: Path) -> None:
    """cmd_run returns EXIT_OK on successful tick."""
    config = _make_config(tmp_path)
    guards_patch = patch(
        "maintenance_worker.core.guards.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="", stderr=""),
    )
    disk_patch = patch(
        "maintenance_worker.core.guards.shutil.disk_usage",
        return_value=MagicMock(free=50_000_000_000, total=100_000_000_000),
    )
    scheduler_patch = patch(
        "maintenance_worker.core.engine.check_scheduler_invocation",
        return_value="SCHEDULED",
    )
    with guards_patch, disk_patch, scheduler_patch:
        result = cmd_run(config)
    assert result == EXIT_OK


def test_cmd_run_lock_contention_returns_exit_code(tmp_path: Path) -> None:
    """cmd_run returns EXIT_LOCK_CONTENTION when lock is already held."""
    config = _make_config(tmp_path)

    # Hold the lock in a thread
    lock = _TickLock(config.state_dir)
    lock.__enter__()
    try:
        result = cmd_run(config)
        assert result == EXIT_LOCK_CONTENTION
    finally:
        lock.__exit__(None, None, None)


def test_cmd_run_propagates_system_exit_nonzero(tmp_path: Path) -> None:
    """cmd_run returns non-zero when engine raises SystemExit with non-zero code."""
    config = _make_config(tmp_path)

    with patch("maintenance_worker.cli.entry.run_tick", side_effect=SystemExit(3)):
        result = cmd_run(config)

    assert result != 0


# ---------------------------------------------------------------------------
# cmd_dry_run
# ---------------------------------------------------------------------------


def test_cmd_dry_run_forces_manual_cli_mode(tmp_path: Path) -> None:
    """cmd_dry_run passes MANUAL_CLI invocation mode to cmd_run."""
    config = _make_config(tmp_path, live_default=True)

    called_with_mode: list[InvocationMode] = []

    def fake_cmd_run(cfg, invocation_mode=None):
        called_with_mode.append(invocation_mode)
        return EXIT_OK

    with patch("maintenance_worker.cli.entry.cmd_run", side_effect=fake_cmd_run):
        cmd_dry_run(config)

    assert called_with_mode == [InvocationMode.MANUAL_CLI], (
        f"cmd_dry_run must pass MANUAL_CLI, got {called_with_mode}"
    )


def test_cmd_dry_run_returns_exit_ok(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    with patch("maintenance_worker.cli.entry.cmd_run", return_value=EXIT_OK):
        assert cmd_dry_run(config) == EXIT_OK


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


def test_cmd_status_returns_exit_ok(tmp_path: Path, capsys) -> None:
    """cmd_status always returns EXIT_OK."""
    config = _make_config(tmp_path)
    result = cmd_status(config)
    assert result == EXIT_OK


def test_cmd_status_reports_idle(tmp_path: Path, capsys) -> None:
    """cmd_status prints 'IDLE' when no lockfile exists."""
    config = _make_config(tmp_path)
    cmd_status(config)
    captured = capsys.readouterr()
    assert "IDLE" in captured.out


def test_cmd_status_reports_tick_in_progress(tmp_path: Path, capsys) -> None:
    """cmd_status prints 'TICK_IN_PROGRESS' when lockfile exists."""
    config = _make_config(tmp_path)
    lockfile = config.state_dir / "maintenance_worker.pid"
    lockfile.write_text("99999\n")
    cmd_status(config)
    captured = capsys.readouterr()
    assert "TICK_IN_PROGRESS" in captured.out


def test_cmd_status_reports_sentinel(tmp_path: Path, capsys) -> None:
    """cmd_status reports KILL_SWITCH when present."""
    config = _make_config(tmp_path)
    (config.state_dir / "KILL_SWITCH").touch()
    cmd_status(config)
    captured = capsys.readouterr()
    assert "KILL_SWITCH" in captured.out


def test_cmd_status_reports_last_trail(tmp_path: Path, capsys) -> None:
    """cmd_status reports most recent evidence trail."""
    config = _make_config(tmp_path)
    trail_dir = config.evidence_dir / "2026-05-15"
    trail_dir.mkdir()
    (trail_dir / "exit_code").write_text("0\n")
    cmd_status(config)
    captured = capsys.readouterr()
    assert "2026-05-15" in captured.out


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------


def test_cmd_init_creates_dirs(tmp_path: Path) -> None:
    """cmd_init creates state_dir and evidence_dir."""
    state_dir = tmp_path / "new_state"
    evidence_dir = tmp_path / "new_evidence"
    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=state_dir,
        evidence_dir=evidence_dir,
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.md",
        live_default=False,
        scheduler="launchd",
        notification_channel="file",
    )
    result = cmd_init(config)
    assert result == EXIT_OK
    assert state_dir.exists()
    assert evidence_dir.exists()


def test_cmd_init_idempotent(tmp_path: Path) -> None:
    """cmd_init succeeds if dirs already exist (exist_ok)."""
    config = _make_config(tmp_path)  # dirs already created
    result = cmd_init(config)
    assert result == EXIT_OK


# ---------------------------------------------------------------------------
# main() argparse routing
# ---------------------------------------------------------------------------


def test_main_no_config_returns_exit_no_config(tmp_path: Path) -> None:
    """main() with nonexistent config returns EXIT_NO_CONFIG."""
    with patch.dict(os.environ, {"MAINTENANCE_WORKER_CONFIG": str(tmp_path / "nonexistent.json")}):
        result = main(["run"])
    assert result == EXIT_NO_CONFIG


def test_main_init_subcommand(tmp_path: Path) -> None:
    """main() with 'init' subcommand calls cmd_init."""
    config_path = tmp_path / "config.json"
    state_dir = tmp_path / "state"
    evidence_dir = tmp_path / "evidence"
    config_path.write_text(
        f'{{"repo_root": "{tmp_path}", "state_dir": "{state_dir}", '
        f'"evidence_dir": "{evidence_dir}", "task_catalog_path": "{tmp_path}/c.yaml", '
        f'"safety_contract_path": "{tmp_path}/s.md", "live_default": false, '
        f'"scheduler": "launchd", "notification_channel": "file"}}'
    )
    result = main(["--config", str(config_path), "init"])
    assert result == EXIT_OK
    assert state_dir.exists()
    assert evidence_dir.exists()


def test_main_status_subcommand(tmp_path: Path) -> None:
    """main() with 'status' subcommand calls cmd_status."""
    config_path = tmp_path / "config.json"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    config_path.write_text(
        f'{{"repo_root": "{tmp_path}", "state_dir": "{state_dir}", '
        f'"evidence_dir": "{evidence_dir}", "task_catalog_path": "{tmp_path}/c.yaml", '
        f'"safety_contract_path": "{tmp_path}/s.md", "live_default": false, '
        f'"scheduler": "launchd", "notification_channel": "file"}}'
    )
    result = main(["--config", str(config_path), "status"])
    assert result == EXIT_OK
