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
    from maintenance_worker.types.candidates import Candidate
    from maintenance_worker.types.specs import TaskSpec
    from maintenance_worker.types.results import ApplyResult
    stub_spec = TaskSpec(task_id="detector_task", description="test", schedule="daily")
    stub_entry = TaskCatalogEntry(spec=stub_spec, raw={"id": "detector_task", "schedule": "daily"})
    stub_candidate = Candidate(
        task_id="detector_task",
        path=tmp_path / "dummy",
        verdict="TEST",
        reason="test candidate",
    )
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
                    MaintenanceEngine, "_dispatch_enumerate", return_value=[stub_candidate]
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

    Injects a stub entry + one Candidate + dry-run ApplyResult so the
    for-loop actually executes and the skip branch is genuinely tested
    (non-vacuous). Compare to test_run_tick_post_mutation_detector_called
    which injects a non-dry-run result and asserts called_once.
    """
    config = _make_config(tmp_path)
    mock_run, mock_disk = _clean_guards_context(tmp_path)

    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.candidates import Candidate
    from maintenance_worker.types.specs import TaskSpec
    from maintenance_worker.types.results import ApplyResult
    stub_spec = TaskSpec(task_id="dry_run_only_task", description="dry run task", schedule="daily")
    stub_entry = TaskCatalogEntry(spec=stub_spec, raw={"id": "dry_run_only_task", "schedule": "daily"})
    stub_candidate = Candidate(
        task_id="dry_run_only_task",
        path=tmp_path / "dummy",
        verdict="TEST",
        reason="test candidate",
    )
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
                    MaintenanceEngine, "_dispatch_enumerate", return_value=[stub_candidate]
                ):
                    with patch.object(
                        MaintenanceEngine, "_apply_decisions", return_value=dry_result
                    ):
                        with patch(
                            "maintenance_worker.core.engine.post_mutation_detector"
                        ) as mock_detector:
                            result = run_tick(config)

    # Loop ran once (one candidate injected) but skipped detector because dry_run_only=True
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


# ---------------------------------------------------------------------------
# M3: _enumerate_candidates schedule parameterization
# ---------------------------------------------------------------------------


def test_enumerate_candidates_weekly_schedule(tmp_path: Path) -> None:
    """
    _enumerate_candidates(config, schedule="weekly") surfaces only weekly tasks.

    Injects a catalog with one daily task and one weekly task. Calls
    _enumerate_candidates with schedule="weekly" directly to assert only
    the weekly entry is returned.
    """
    import yaml
    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.specs import TaskSpec

    # Write a minimal catalog with one daily + one weekly task
    catalog_content = {
        "schema_version": 1,
        "tasks": [
            {
                "id": "daily_task",
                "description": "a daily task",
                "rule_source": "PURGE_CATEGORIES.md#cat-1",
                "schedule": "daily",
                "dry_run": True,
                "live_default": False,
                "config": {},
                "safety": {},
                "evidence_emit": "per_file_action",
            },
            {
                "id": "weekly_task",
                "description": "a weekly task",
                "rule_source": "PURGE_CATEGORIES.md#cat-99",
                "schedule": "weekly",
                "dry_run": True,
                "live_default": False,
                "config": {},
                "safety": {},
                "evidence_emit": "per_file_action",
            },
        ]
    }
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(yaml.dump(catalog_content))

    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=catalog_path,
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )

    engine = MaintenanceEngine()
    weekly_entries = engine._enumerate_candidates(config, schedule="weekly")
    daily_entries = engine._enumerate_candidates(config, schedule="daily")

    assert len(weekly_entries) == 1, f"Expected 1 weekly entry; got {len(weekly_entries)}"
    assert weekly_entries[0].spec.task_id == "weekly_task"
    assert weekly_entries[0].spec.schedule == "weekly"

    assert len(daily_entries) == 1, f"Expected 1 daily entry; got {len(daily_entries)}"
    assert daily_entries[0].spec.task_id == "daily_task"


# ---------------------------------------------------------------------------
# MC2: Cascade isolation — one handler crash does not poison peers
# ---------------------------------------------------------------------------


def test_one_handler_crash_does_not_poison_peers(tmp_path: Path) -> None:
    """
    When handler_A.enumerate() raises OSError, the engine must:
    - Catch it (not propagate to run_tick outer scope)
    - Return [] for handler_A
    - Still dispatch handler_B and collect its candidates

    Regression test for MC2: _dispatch_enumerate previously only caught
    TaskHandlerNotFoundError; bare OSError propagated and killed the tick.
    """
    import yaml
    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.candidates import Candidate
    from maintenance_worker.types.specs import TaskSpec

    catalog_content = {
        "schema_version": 1,
        "tasks": [
            {
                "id": "crashy_handler",
                "description": "crashes on enumerate",
                "rule_source": "test",
                "schedule": "daily",
                "dry_run": True,
                "live_default": False,
                "config": {},
                "safety": {},
                "evidence_emit": "per_file_action",
            },
            {
                "id": "clean_handler",
                "description": "returns clean candidate",
                "rule_source": "test",
                "schedule": "daily",
                "dry_run": True,
                "live_default": False,
                "config": {},
                "safety": {},
                "evidence_emit": "per_file_action",
            },
        ],
    }
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(yaml.dump(catalog_content))

    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=catalog_path,
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )
    (tmp_path / "state").mkdir()
    (tmp_path / "evidence").mkdir()
    (tmp_path / ".git").mkdir()

    clean_candidate = Candidate(
        task_id="clean_handler",
        path=tmp_path / "dummy.txt",
        verdict="TEST_CANDIDATE",
        reason="clean handler ran",
    )

    # Mock at _dispatch_by_task_id so _dispatch_enumerate's broad-except actually fires.
    # Mocking _dispatch_enumerate directly would bypass the except clause under test.
    def fake_dispatch_by_task_id(task_id: str, method: str, *args: object) -> object:
        """Simulate crashy_handler raising OSError at handler dispatch level."""
        if task_id == "crashy_handler" and method == "enumerate":
            raise OSError("disk read failed: permission denied")
        if task_id == "clean_handler" and method == "enumerate":
            return [clean_candidate]
        # apply() path — return dry_run_only ApplyResult
        from maintenance_worker.types.results import ApplyResult
        return ApplyResult(task_id=task_id, dry_run_only=True)

    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with patch.object(
                MaintenanceEngine,
                "_dispatch_by_task_id",
                side_effect=fake_dispatch_by_task_id,
            ):
                result = run_tick(config)

    # Tick must complete normally (not raise)
    assert isinstance(result, TickResult)
    assert result.skipped is False

    # clean_handler must have contributed its candidate to apply_results
    all_task_ids = [r.task_id for r in result.apply_results]
    assert "clean_handler" in all_task_ids, (
        f"clean_handler must run despite crashy_handler's OSError; got task_ids={all_task_ids}"
    )


# ---------------------------------------------------------------------------
# MC1 (Option A): Weekly cadence gate — _run_weekly_if_due
# ---------------------------------------------------------------------------


def test_weekly_cadence_fires_on_first_run(tmp_path: Path) -> None:
    """
    When last_weekly_tick.json is absent, run_tick must dispatch weekly tasks
    (cadence due on first run) and write last_weekly_tick.json.
    """
    import yaml
    from maintenance_worker.types.candidates import Candidate
    from maintenance_worker.rules.parser import TaskCatalogEntry

    catalog_content = {
        "schema_version": 1,
        "tasks": [
            {
                "id": "authority_drift_surface",
                "description": "weekly drift check",
                "rule_source": "test",
                "schedule": "weekly",
                "dry_run": True,
                "live_default": False,
                "config": {},
                "safety": {},
                "evidence_emit": "drift_report_per_doc",
            },
        ],
    }
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(yaml.dump(catalog_content))

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (tmp_path / "evidence").mkdir()
    (tmp_path / ".git").mkdir()

    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=state_dir,
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=catalog_path,
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )

    weekly_ran: list[str] = []

    def fake_dispatch_enumerate(entry: TaskCatalogEntry, ctx: object) -> list[Candidate]:
        weekly_ran.append(entry.spec.task_id)
        return []

    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with patch.object(
                MaintenanceEngine,
                "_dispatch_enumerate",
                side_effect=fake_dispatch_enumerate,
            ):
                result = run_tick(config)

    assert "authority_drift_surface" in weekly_ran, (
        "Weekly task must dispatch when last_weekly_tick.json is absent (first run)"
    )
    weekly_ts_file = state_dir / "maintenance_state" / "last_weekly_tick.json"
    assert weekly_ts_file.exists(), (
        "last_weekly_tick.json must be written after weekly dispatch"
    )


def test_weekly_cadence_skips_when_recent(tmp_path: Path) -> None:
    """
    When last_weekly_tick.json was written 6 days ago, weekly tasks must NOT fire.
    """
    import json
    import time
    import yaml
    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.candidates import Candidate

    catalog_content = {
        "schema_version": 1,
        "tasks": [
            {
                "id": "authority_drift_surface",
                "description": "weekly drift check",
                "rule_source": "test",
                "schedule": "weekly",
                "dry_run": True,
                "live_default": False,
                "config": {},
                "safety": {},
                "evidence_emit": "drift_report_per_doc",
            },
        ],
    }
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(yaml.dump(catalog_content))

    state_dir = tmp_path / "state"
    maintenance_state_dir = state_dir / "maintenance_state"
    maintenance_state_dir.mkdir(parents=True)
    (tmp_path / "evidence").mkdir()
    (tmp_path / ".git").mkdir()

    # Write last_weekly_tick.json with 6-day-ago timestamp
    six_days_ago = time.time() - (6 * 86400)
    weekly_ts_file = maintenance_state_dir / "last_weekly_tick.json"
    weekly_ts_file.write_text(json.dumps({"last_run_ts": six_days_ago}))

    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=state_dir,
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=catalog_path,
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )

    weekly_ran: list[str] = []

    def fake_dispatch_enumerate(entry: TaskCatalogEntry, ctx: object) -> list[Candidate]:
        weekly_ran.append(entry.spec.task_id)
        return []

    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with patch.object(
                MaintenanceEngine,
                "_dispatch_enumerate",
                side_effect=fake_dispatch_enumerate,
            ):
                result = run_tick(config)

    assert "authority_drift_surface" not in weekly_ran, (
        "Weekly task must NOT dispatch when last run was 6 days ago (< 7d threshold)"
    )


def test_weekly_cadence_fires_when_overdue(tmp_path: Path) -> None:
    """
    When last_weekly_tick.json was written 8 days ago, weekly tasks must fire again.
    """
    import json
    import time
    import yaml
    from maintenance_worker.rules.parser import TaskCatalogEntry
    from maintenance_worker.types.candidates import Candidate

    catalog_content = {
        "schema_version": 1,
        "tasks": [
            {
                "id": "authority_drift_surface",
                "description": "weekly drift check",
                "rule_source": "test",
                "schedule": "weekly",
                "dry_run": True,
                "live_default": False,
                "config": {},
                "safety": {},
                "evidence_emit": "drift_report_per_doc",
            },
        ],
    }
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(yaml.dump(catalog_content))

    state_dir = tmp_path / "state"
    maintenance_state_dir = state_dir / "maintenance_state"
    maintenance_state_dir.mkdir(parents=True)
    (tmp_path / "evidence").mkdir()
    (tmp_path / ".git").mkdir()

    # Write last_weekly_tick.json with 8-day-ago timestamp
    eight_days_ago = time.time() - (8 * 86400)
    weekly_ts_file = maintenance_state_dir / "last_weekly_tick.json"
    weekly_ts_file.write_text(json.dumps({"last_run_ts": eight_days_ago}))

    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=state_dir,
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=catalog_path,
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )

    weekly_ran: list[str] = []

    def fake_dispatch_enumerate(entry: TaskCatalogEntry, ctx: object) -> list[Candidate]:
        weekly_ran.append(entry.spec.task_id)
        return []

    mock_run, mock_disk = _clean_guards_context(tmp_path)
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="SCHEDULED",
        ):
            with patch.object(
                MaintenanceEngine,
                "_dispatch_enumerate",
                side_effect=fake_dispatch_enumerate,
            ):
                result = run_tick(config)

    assert "authority_drift_surface" in weekly_ran, (
        "Weekly task must fire when last run was 8 days ago (> 7d threshold)"
    )
    # Timestamp must have been updated
    updated = json.loads(weekly_ts_file.read_text())
    assert updated["last_run_ts"] > eight_days_ago, (
        "last_weekly_tick.json must be updated after weekly dispatch"
    )
