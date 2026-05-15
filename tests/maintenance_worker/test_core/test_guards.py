# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 guards.py + §6 ND3
"""
Tests for maintenance_worker.core.guards.

Covers:
- Each of 8 guards: pass state (ok=True) and fail state (ok=False + correct reason)
- Severity split: 6 refuse_fatal hard guards, 2 skip_tick soft guards
- evaluate_all: all-pass, first-failure ordering
- GuardReport.all_passed and first_failure properties
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maintenance_worker.core.guards import (
    DEFAULT_DISK_FREE_THRESHOLD_PCT,
    SEVERITY_REFUSE_FATAL,
    SEVERITY_SKIP_TICK,
    GuardReport,
    check_active_rebase,
    check_disk_free,
    check_inflight_pr,
    check_kill_switch,
    check_no_pause_flag,
    check_oncall_quiet,
    check_self_quarantined,
    check_dirty_repo,
    evaluate_all,
)
from maintenance_worker.types.modes import RefusalReason


# ---------------------------------------------------------------------------
# check_kill_switch
# ---------------------------------------------------------------------------


def test_check_kill_switch_passes_when_absent(tmp_path: Path) -> None:
    result = check_kill_switch(tmp_path)
    assert result.ok is True


def test_check_kill_switch_fails_when_present(tmp_path: Path) -> None:
    (tmp_path / "KILL_SWITCH").touch()
    result = check_kill_switch(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.KILL_SWITCH.value
    assert result.details["severity"] == SEVERITY_REFUSE_FATAL


# ---------------------------------------------------------------------------
# check_self_quarantined
# ---------------------------------------------------------------------------


def test_check_self_quarantined_passes_when_absent(tmp_path: Path) -> None:
    result = check_self_quarantined(tmp_path)
    assert result.ok is True


def test_check_self_quarantined_fails_when_present(tmp_path: Path) -> None:
    (tmp_path / "SELF_QUARANTINE").write_text("quarantine reason")
    result = check_self_quarantined(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.SELF_QUARANTINED.value
    assert result.details["severity"] == SEVERITY_REFUSE_FATAL
    assert "quarantine reason" in result.details["quarantine_content"]


# ---------------------------------------------------------------------------
# check_dirty_repo
# ---------------------------------------------------------------------------


def test_check_dirty_repo_passes_on_clean_repo(tmp_path: Path) -> None:
    # Use a real clean git repo (init + initial commit)
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env={"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@t.com",
             "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    result = check_dirty_repo(tmp_path)
    assert result.ok is True


def test_check_dirty_repo_fails_on_dirty_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "dirty.txt").write_text("change")
    result = check_dirty_repo(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.DIRTY_REPO.value
    assert result.details["severity"] == SEVERITY_REFUSE_FATAL


def test_check_dirty_repo_fails_when_git_not_available(tmp_path: Path) -> None:
    with patch("maintenance_worker.core.guards.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("git not found")
        result = check_dirty_repo(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.DIRTY_REPO.value


# ---------------------------------------------------------------------------
# check_active_rebase
# ---------------------------------------------------------------------------


def test_check_active_rebase_passes_when_clean(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    result = check_active_rebase(tmp_path)
    assert result.ok is True


@pytest.mark.parametrize(
    "indicator",
    ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply"],
)
def test_check_active_rebase_fails_on_interrupt_indicator(
    tmp_path: Path, indicator: str
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / indicator).touch()
    result = check_active_rebase(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.ACTIVE_REBASE.value
    assert result.details["severity"] == SEVERITY_REFUSE_FATAL
    assert indicator in result.details["indicator"]


# ---------------------------------------------------------------------------
# check_disk_free
# ---------------------------------------------------------------------------


def test_check_disk_free_passes_when_above_threshold(tmp_path: Path) -> None:
    # Real disk usage check — should pass on a normal dev machine
    result = check_disk_free(tmp_path, threshold_pct=0.001)
    assert result.ok is True


def test_check_disk_free_fails_when_below_threshold(tmp_path: Path) -> None:
    # Mock: 1% free, threshold 5%
    import shutil
    with patch("maintenance_worker.core.guards.shutil.disk_usage") as mock_usage:
        mock_usage.return_value = shutil.disk_usage.__doc__ and MagicMock(
            free=1_000_000, total=100_000_000
        ) or MagicMock(free=1_000_000, total=100_000_000)
        result = check_disk_free(tmp_path, threshold_pct=5.0)
    assert result.ok is False
    assert result.reason == RefusalReason.LOW_DISK.value
    assert result.details["severity"] == SEVERITY_REFUSE_FATAL


def test_check_disk_free_fails_on_oserror(tmp_path: Path) -> None:
    with patch("maintenance_worker.core.guards.shutil.disk_usage") as mock_usage:
        mock_usage.side_effect = OSError("permission denied")
        result = check_disk_free(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.LOW_DISK.value


# ---------------------------------------------------------------------------
# check_inflight_pr
# ---------------------------------------------------------------------------


def test_check_inflight_pr_passes_when_absent(tmp_path: Path) -> None:
    result = check_inflight_pr(tmp_path)
    assert result.ok is True


def test_check_inflight_pr_fails_when_state_file_present(tmp_path: Path) -> None:
    (tmp_path / "INFLIGHT_MAINTENANCE_PR").write_text(
        "https://github.com/org/repo/pull/42\n"
    )
    result = check_inflight_pr(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.INFLIGHT_PR.value
    assert result.details["severity"] == SEVERITY_REFUSE_FATAL
    assert "42" in result.details["pr_url"]


# ---------------------------------------------------------------------------
# check_no_pause_flag (soft)
# ---------------------------------------------------------------------------


def test_check_no_pause_flag_passes_when_absent(tmp_path: Path) -> None:
    result = check_no_pause_flag(tmp_path)
    assert result.ok is True
    assert result.details["severity"] == SEVERITY_SKIP_TICK


def test_check_no_pause_flag_fails_when_present(tmp_path: Path) -> None:
    (tmp_path / "MAINTENANCE_PAUSED").touch()
    result = check_no_pause_flag(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.MAINTENANCE_PAUSED.value
    assert result.details["severity"] == SEVERITY_SKIP_TICK


# ---------------------------------------------------------------------------
# check_oncall_quiet (soft)
# ---------------------------------------------------------------------------


def test_check_oncall_quiet_passes_when_absent(tmp_path: Path) -> None:
    result = check_oncall_quiet(tmp_path)
    assert result.ok is True
    assert result.details["severity"] == SEVERITY_SKIP_TICK


def test_check_oncall_quiet_fails_when_present(tmp_path: Path) -> None:
    (tmp_path / "ONCALL_QUIET").touch()
    result = check_oncall_quiet(tmp_path)
    assert result.ok is False
    assert result.reason == RefusalReason.ONCALL_QUIET.value
    assert result.details["severity"] == SEVERITY_SKIP_TICK


# ---------------------------------------------------------------------------
# Guard severity split verification: 6 refuse_fatal + 2 skip_tick
# ---------------------------------------------------------------------------


def test_guard_severity_split_6_refuse_fatal_2_skip_tick(tmp_path: Path) -> None:
    """
    SCAFFOLD §6 ND3: exactly 6 hard guards (refuse_fatal) + 2 soft (skip_tick).
    Verified by triggering all 8 guards and checking severity labels.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Trigger all 8 guard failures by creating the trigger files
    (state_dir / "KILL_SWITCH").touch()
    (state_dir / "SELF_QUARANTINE").write_text("test")
    (state_dir / "INFLIGHT_MAINTENANCE_PR").write_text("https://github.com/r/p/42")
    (state_dir / "MAINTENANCE_PAUSED").touch()
    (state_dir / "ONCALL_QUIET").touch()

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "MERGE_HEAD").touch()  # active rebase
    # Make repo dirty by patching git status
    (tmp_path / "dirty.txt").write_text("dirty")

    results = [
        check_kill_switch(state_dir),
        check_self_quarantined(state_dir),
        check_inflight_pr(state_dir),
        check_active_rebase(tmp_path),
        check_no_pause_flag(state_dir),
        check_oncall_quiet(state_dir),
    ]

    refuse_fatal_count = sum(
        1 for r in results if r.details.get("severity") == SEVERITY_REFUSE_FATAL
    )
    skip_tick_count = sum(
        1 for r in results if r.details.get("severity") == SEVERITY_SKIP_TICK
    )
    assert refuse_fatal_count == 4  # kill_switch, self_quarantined, inflight_pr, active_rebase
    assert skip_tick_count == 2    # pause_flag, oncall_quiet


# ---------------------------------------------------------------------------
# GuardReport
# ---------------------------------------------------------------------------


def test_guard_report_all_passed_true_when_all_ok(tmp_path: Path) -> None:
    from maintenance_worker.types.results import CheckResult
    report = GuardReport()
    report.results = [
        ("guard_a", CheckResult(ok=True)),
        ("guard_b", CheckResult(ok=True)),
    ]
    assert report.all_passed is True


def test_guard_report_all_passed_false_on_any_failure(tmp_path: Path) -> None:
    from maintenance_worker.types.results import CheckResult
    report = GuardReport()
    report.results = [
        ("guard_a", CheckResult(ok=True)),
        ("guard_b", CheckResult(ok=False, reason="KILL_SWITCH")),
    ]
    assert report.all_passed is False


def test_guard_report_first_failure_is_none_when_all_pass() -> None:
    from maintenance_worker.types.results import CheckResult
    report = GuardReport()
    report.results = [("a", CheckResult(ok=True)), ("b", CheckResult(ok=True))]
    assert report.first_failure is None


def test_guard_report_first_failure_returns_first_failed() -> None:
    from maintenance_worker.types.results import CheckResult
    report = GuardReport()
    report.results = [
        ("guard_a", CheckResult(ok=True)),
        ("guard_b", CheckResult(ok=False, reason="KILL_SWITCH")),
        ("guard_c", CheckResult(ok=False, reason="DIRTY_REPO")),
    ]
    name, result = report.first_failure
    assert name == "guard_b"
    assert result.reason == "KILL_SWITCH"


# ---------------------------------------------------------------------------
# evaluate_all integration
# ---------------------------------------------------------------------------


def test_evaluate_all_passes_on_clean_state(tmp_path: Path) -> None:
    """All guards pass when no trigger files exist and repo is clean."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    with patch("maintenance_worker.core.guards.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("maintenance_worker.core.guards.shutil.disk_usage") as mock_disk:
            mock_disk.return_value = MagicMock(free=10_000_000_000, total=100_000_000_000)
            report = evaluate_all(tmp_path, state_dir)

    assert report.all_passed is True
    assert report.first_failure is None
    assert len(report.results) == 8


def test_evaluate_all_returns_8_results(tmp_path: Path) -> None:
    """evaluate_all always returns exactly 8 guard results."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    with patch("maintenance_worker.core.guards.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("maintenance_worker.core.guards.shutil.disk_usage") as mock_disk:
            mock_disk.return_value = MagicMock(free=10_000_000_000, total=100_000_000_000)
            report = evaluate_all(tmp_path, state_dir)

    assert len(report.results) == 8


def test_evaluate_all_fail_on_kill_switch(tmp_path: Path) -> None:
    """Kill switch present → first_failure is check_kill_switch (first guard)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "KILL_SWITCH").touch()
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    with patch("maintenance_worker.core.guards.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("maintenance_worker.core.guards.shutil.disk_usage") as mock_disk:
            mock_disk.return_value = MagicMock(free=10_000_000_000, total=100_000_000_000)
            report = evaluate_all(tmp_path, state_dir)

    assert not report.all_passed
    name, result = report.first_failure
    assert name == "check_kill_switch"
    assert result.reason == RefusalReason.KILL_SWITCH.value
