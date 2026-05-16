# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 engine.py + §3.5
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Tick Lifecycle"
"""
Tests for maintenance_worker.core.engine.

Covers:
- run_tick: state machine breadcrumbs, all phases present in order
- Guard failure paths: refuse_fatal (hard) and skip_tick (soft)
- MANUAL_CLI forces dry_run_only
- module-level run_tick import works (smoke test)
- post_mutation_detector called after apply (integration)
- TickResult structure: run_id, started_at, guard_report, apply_results
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maintenance_worker.core.engine import MaintenanceEngine, TickResult, run_tick
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


def _clean_guards_context(tmp_path: Path):
    """Return a context manager that mocks git+disk so all guards pass."""
    mock_run = patch(
        "maintenance_worker.core.guards.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="", stderr=""),
    )
    mock_disk = patch(
        "maintenance_worker.core.guards.shutil.disk_usage",
        return_value=MagicMock(free=50_000_000_000, total=100_000_000_000),
    )
    return mock_run, mock_disk


# ---------------------------------------------------------------------------
# Module-level import smoke test
# ---------------------------------------------------------------------------


def test_run_tick_importable_as_module_function() -> None:
    """Smoke: run_tick must be importable as a module-level function."""
    assert callable(run_tick)


# ---------------------------------------------------------------------------
# run_tick — happy path (all guards pass, no tasks)
# ---------------------------------------------------------------------------


def test_run_tick_returns_tick_result(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    assert isinstance(result, TickResult)


def test_run_tick_has_run_id(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    assert result.run_id
    assert len(result.run_id) == 36  # UUID4 format


def test_run_tick_state_machine_breadcrumbs_ordered(tmp_path: Path) -> None:
    """All 7 phases appear in the breadcrumbs in correct order."""
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    phase_names = [name for name, _ in result.state_machine_breadcrumbs]
    expected_order = [
        "START",
        "LOAD_CONFIG",
        "CHECK_GUARDS",
        "ENUMERATE_CANDIDATES",
        "DRY_RUN_PROPOSAL",
        "APPLY_DECISIONS",
        "SUMMARY_REPORT",
        "END",
    ]
    assert phase_names == expected_order


def test_run_tick_all_breadcrumbs_ok(tmp_path: Path) -> None:
    """All breadcrumb phases pass (ok=True) on happy path."""
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    for name, ok in result.state_machine_breadcrumbs:
        assert ok is True, f"Phase {name!r} should be ok=True on happy path"


def test_run_tick_guard_report_present(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    assert result.guard_report is not None
    assert result.guard_report.all_passed is True


def test_run_tick_not_skipped_on_clean_state(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    assert result.skipped is False


# ---------------------------------------------------------------------------
# Guard failure paths
# ---------------------------------------------------------------------------


def test_run_tick_refuse_fatal_on_kill_switch(tmp_path: Path) -> None:
    """Hard guard failure → SystemExit (non-zero)."""
    config = _make_config(tmp_path)
    (config.state_dir / "KILL_SWITCH").touch()
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with pytest.raises(SystemExit) as exc_info:
                run_tick(config)
    assert exc_info.value.code != 0


def test_run_tick_skip_on_maintenance_paused(tmp_path: Path) -> None:
    """Soft guard failure → returns TickResult with skipped=True (no SystemExit)."""
    config = _make_config(tmp_path)
    (config.state_dir / "MAINTENANCE_PAUSED").touch()
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    assert result.skipped is True
    assert "MAINTENANCE_PAUSED" in result.skip_reason


def test_run_tick_skip_on_oncall_quiet(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    (config.state_dir / "ONCALL_QUIET").touch()
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    assert result.skipped is True
    assert "ONCALL_QUIET" in result.skip_reason


def test_run_tick_hard_guard_does_not_write_self_quarantine(tmp_path: Path) -> None:
    """
    C2 invariant: hard guard failure (Path A) must NOT write SELF_QUARANTINE.
    Only post_mutation_detector (Path B) writes it.
    """
    config = _make_config(tmp_path)
    (config.state_dir / "KILL_SWITCH").touch()
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with pytest.raises(SystemExit):
                run_tick(config)
    quarantine = config.state_dir / "SELF_QUARANTINE"
    assert not quarantine.exists(), "Hard guard must NOT write SELF_QUARANTINE (C2 invariant)"


# ---------------------------------------------------------------------------
# MANUAL_CLI forces dry_run_only
# ---------------------------------------------------------------------------


def test_run_tick_manual_cli_forces_dry_run_only(tmp_path: Path) -> None:
    """MANUAL_CLI invocation mode → all ApplyResults have dry_run_only=True."""
    config = _make_config(tmp_path, live_default=True)
    mock_run, mock_disk = _clean_guards_context(tmp_path)

    # Inject a stub entry so _apply_decisions is exercised
    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.specs import TaskSpec
    stub_spec = TaskSpec(task_id="test_live_task", description="live task", schedule="daily")
    stub_entry = TaskCatalogEntry(spec=stub_spec, raw={"id": "test_live_task", "schedule": "daily"})

    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="MANUAL_CLI",
        ):
            with patch.object(
                MaintenanceEngine,
                "_enumerate_candidates",
                return_value=[stub_entry],
            ):
                result = run_tick(config)

    assert all(r.dry_run_only for r in result.apply_results), (
        "MANUAL_CLI must force dry_run_only=True on all apply results"
    )


# ---------------------------------------------------------------------------
# post_mutation_detector called after apply
# ---------------------------------------------------------------------------


def test_run_tick_post_mutation_detector_called(tmp_path: Path) -> None:
    """post_mutation_detector is invoked for non-dry-run apply results."""
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)

    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.specs import TaskSpec
    from maintenance_worker.types.results import ApplyResult
    stub_spec = TaskSpec(task_id="detector_task", description="test", schedule="daily")
    stub_entry = TaskCatalogEntry(spec=stub_spec, raw={"id": "detector_task", "schedule": "daily"})
    non_dry_result = ApplyResult(task_id="detector_task", dry_run_only=False)

    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with patch.object(
                MaintenanceEngine, "_enumerate_candidates", return_value=[stub_entry]
            ):
                with patch.object(
                    MaintenanceEngine, "_apply_decisions", return_value=non_dry_result
                ):
                    with patch(
                        "maintenance_worker.core.engine.post_mutation_detector"
                    ) as mock_detector:
                        result = run_tick(config)

    mock_detector.assert_called_once()


def test_run_tick_post_mutation_detector_not_called_for_dry_run(
    tmp_path: Path,
) -> None:
    """
    post_mutation_detector is NOT invoked when dry_run_only=True.

    Injects a stub entry + dry-run ApplyResult so the for-loop actually
    executes and the skip branch is genuinely tested (non-vacuous).
    Compare to test_run_tick_post_mutation_detector_called which injects
    a non-dry-run result and asserts called_once.
    """
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)

    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.specs import TaskSpec
    from maintenance_worker.types.results import ApplyResult
    stub_spec = TaskSpec(task_id="dry_run_only_task", description="dry run task", schedule="daily")
    stub_entry = TaskCatalogEntry(spec=stub_spec, raw={"id": "dry_run_only_task", "schedule": "daily"})
    dry_result = ApplyResult(task_id="dry_run_only_task", dry_run_only=True)

    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with patch.object(
                MaintenanceEngine, "_enumerate_candidates", return_value=[stub_entry]
            ):
                with patch.object(
                    MaintenanceEngine, "_apply_decisions", return_value=dry_result
                ):
                    with patch(
                        "maintenance_worker.core.engine.post_mutation_detector"
                    ) as mock_detector:
                        result = run_tick(config)

    # Loop ran once (one entry injected) but skipped detector because dry_run_only=True
    assert len(result.apply_results) == 1
    assert result.apply_results[0].dry_run_only is True
    mock_detector.assert_not_called()


# ---------------------------------------------------------------------------
# TickResult structure
# ---------------------------------------------------------------------------


def test_tick_result_started_at_is_utc(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            result = run_tick(config)
    assert result.started_at.tzinfo is not None


def test_run_tick_self_quarantined_guard_exits_nonzero(tmp_path: Path) -> None:
    """SELF_QUARANTINE present → refuse_fatal(SELF_QUARANTINED) → SystemExit."""
    config = _make_config(tmp_path)
    (config.state_dir / "SELF_QUARANTINE").write_text("prior quarantine")
    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with pytest.raises(SystemExit) as exc_info:
                run_tick(config)
    assert exc_info.value.code != 0
