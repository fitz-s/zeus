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
    """_TickLock creates lockfile, writes PID; flock released after exit (file stays)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lockfile = state_dir / "maintenance_worker.pid"

    with _TickLock(state_dir):
        assert lockfile.exists(), "Lockfile must exist while lock is held"
        pid_text = lockfile.read_text().strip()
        assert pid_text == str(os.getpid()), f"Lockfile must contain current PID, got {pid_text!r}"

    # SEV-2 #3: flock is the mutex; lockfile is NOT unlinked on exit (avoids TOCTOU race).
    # Verify the flock was released by successfully acquiring it again.
    assert lockfile.exists(), "Lockfile must persist after release (flock is the mutex)"
    with _TickLock(state_dir):
        pass  # second acquisition succeeds → flock was released


def test_tick_lock_contention_raises(tmp_path: Path) -> None:
    """Second _TickLock on same state_dir raises LockContention."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    with _TickLock(state_dir):
        with pytest.raises(LockContention):
            with _TickLock(state_dir):
                pass  # should not reach here


def test_tick_lock_cleanup_on_exception(tmp_path: Path) -> None:
    """Flock is released even if the body raises (lockfile stays on disk)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    lockfile = state_dir / "maintenance_worker.pid"

    with pytest.raises(ValueError):
        with _TickLock(state_dir):
            raise ValueError("body error")

    # SEV-2 #3: lockfile persists (flock is the mutex, not file existence).
    # Verify flock was released by acquiring a second lock successfully.
    assert lockfile.exists(), "Lockfile must persist on disk after exception (flock is mutex)"
    with _TickLock(state_dir):
        pass  # second acquisition succeeds → flock was released on exception


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


# ---------------------------------------------------------------------------
# SEV-1 integration tests
# ---------------------------------------------------------------------------


def _make_apply_result_live(task_id: str = "test_task"):
    """Build a non-dry-run ApplyResult with a move mutation."""
    from maintenance_worker.types.results import ApplyResult
    return ApplyResult(
        task_id=task_id,
        dry_run_only=False,
        moved=[("src/old.py", "src/new.py")],
    )


def _make_install_meta(allowed_url: str = "https://github.com/allowed/repo.git"):
    """Build an InstallMetadata with all required fields."""
    from datetime import datetime, timezone
    from maintenance_worker.core.install_metadata import InstallMetadata, SCHEMA_VERSION
    return InstallMetadata(
        schema_version=SCHEMA_VERSION,
        first_run_at=datetime.now(tz=timezone.utc),
        agent_version="0.1.0",
        install_run_id="test-install-run-id",
        allowed_remote_urls=(allowed_url,),
    )


def _write_install_metadata(state_dir: Path, allowed_url: str = "https://github.com/test/repo.git") -> None:
    """Write a minimal install_metadata.json to state_dir (all required fields)."""
    import json
    from datetime import datetime, timezone
    from maintenance_worker.core.install_metadata import SCHEMA_VERSION, METADATA_FILENAME
    meta = {
        "schema_version": SCHEMA_VERSION,
        "first_run_at": datetime.now(tz=timezone.utc).isoformat(),
        "agent_version": "0.1.0",
        "install_run_id": "test-install-run-id",
        "repo_root_at_install": str(state_dir.parent),
        "allowed_remote_urls": [allowed_url],
    }
    (state_dir / METADATA_FILENAME).write_text(json.dumps(meta), encoding="utf-8")


def test_sev1_1_apply_publisher_called_for_live_result(tmp_path: Path) -> None:
    """SEV-1 #1: cmd_run calls ApplyPublisher.publish() for non-dry-run ApplyResults."""
    from maintenance_worker.types.results import ApplyResult
    config = _make_config(tmp_path)
    _write_install_metadata(config.state_dir)

    live_result = _make_apply_result_live()

    # Mock run_tick to return a TickResult with one live ApplyResult
    mock_tick_result = MagicMock()
    mock_tick_result.apply_results = [live_result]
    mock_tick_result.summary_path = None

    with patch("maintenance_worker.cli.entry.run_tick", return_value=mock_tick_result), \
         patch("maintenance_worker.cli.entry.ApplyPublisher") as MockPublisher:
        instance = MockPublisher.return_value
        instance.publish.return_value = MagicMock(error="", rolled_back=False)
        result = cmd_run(config)

    assert result == EXIT_OK
    instance.publish.assert_called_once()
    call_args = instance.publish.call_args
    assert call_args[0][0].task_id == "test_task", (
        "publish() must be called with the live ApplyResult"
    )


def test_sev1_1_publish_skipped_for_dry_run_result(tmp_path: Path) -> None:
    """SEV-1 #1: cmd_run does NOT call ApplyPublisher.publish() for dry_run_only results."""
    from maintenance_worker.types.results import ApplyResult
    config = _make_config(tmp_path)
    _write_install_metadata(config.state_dir)

    dry_result = ApplyResult(task_id="dry_task", dry_run_only=True)
    mock_tick_result = MagicMock()
    mock_tick_result.apply_results = [dry_result]
    mock_tick_result.summary_path = None

    with patch("maintenance_worker.cli.entry.run_tick", return_value=mock_tick_result), \
         patch("maintenance_worker.cli.entry.ApplyPublisher") as MockPublisher:
        instance = MockPublisher.return_value
        result = cmd_run(config)

    assert result == EXIT_OK
    instance.publish.assert_not_called()


def test_sev1_2_allowlist_check_called_before_push(tmp_path: Path) -> None:
    """SEV-1 #2: check_remote_url_allowlist is called in the publish path before any git push."""
    from maintenance_worker.core.apply_publisher import ApplyPublisher
    from maintenance_worker.types.results import ApplyResult, ValidatorResult

    install_meta = _make_install_meta()
    publisher = ApplyPublisher(repo_root=tmp_path, install_meta=install_meta)

    apply_result = _make_apply_result_live("task_allowlist")

    allowlist_calls: list = []

    def fake_check_allowlist(remote_url, meta):
        allowlist_calls.append(remote_url)
        return ValidatorResult.FORBIDDEN_OPERATION  # block — we just want to confirm it's called

    with patch.object(publisher._validator, "check_remote_url_allowlist", side_effect=fake_check_allowlist), \
         patch.object(publisher, "_resolve_remote_url", return_value="https://github.com/some/repo.git"):
        result = publisher.publish(apply_result, run_id="testrun-12345678")

    assert len(allowlist_calls) == 1, (
        f"check_remote_url_allowlist must be called exactly once; got {allowlist_calls}"
    )
    # FORBIDDEN_OPERATION → publish returns error (not proceed to push)
    assert result.error, "Allowlist rejection must surface as PublishResult.error"
    assert not result.commit_sha, "No commit must be made when allowlist blocks"


def test_sev1_3_guarded_git_blocks_force_push(tmp_path: Path) -> None:
    """SEV-1 #3: _guarded_git blocks git push --force (FORBIDDEN_OPERATION from guard)."""
    from maintenance_worker.core.apply_publisher import ApplyPublisher, PublishGuardError
    from maintenance_worker.types.results import ValidatorResult

    install_meta = _make_install_meta()
    publisher = ApplyPublisher(repo_root=tmp_path, install_meta=install_meta)

    force_push_argv = ["git", "-C", str(tmp_path), "push", "--force", "origin", "main"]

    with patch(
        "maintenance_worker.core.apply_publisher.check_git_operation",
        return_value=ValidatorResult.FORBIDDEN_OPERATION,
    ):
        import pytest as _pytest
        with _pytest.raises(PublishGuardError):
            publisher._guarded_git(force_push_argv)


def test_sev1_3_guarded_git_blocks_reset_hard(tmp_path: Path) -> None:
    """SEV-1 #3: _guarded_git blocks git reset --hard (guard prevents forbidden ops)."""
    from maintenance_worker.core.apply_publisher import ApplyPublisher, PublishGuardError
    from maintenance_worker.types.results import ValidatorResult

    install_meta = _make_install_meta()
    publisher = ApplyPublisher(repo_root=tmp_path, install_meta=install_meta)

    hard_reset_argv = ["git", "-C", str(tmp_path), "reset", "--hard", "HEAD"]

    with patch(
        "maintenance_worker.core.apply_publisher.check_git_operation",
        return_value=ValidatorResult.FORBIDDEN_OPERATION,
    ):
        import pytest as _pytest
        with _pytest.raises(PublishGuardError):
            publisher._guarded_git(hard_reset_argv)
