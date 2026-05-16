# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5 STEP 1
"""
Integration tests for MaintenanceEngine._dispatch_by_task_id.

Verifies:
  - Dispatcher imports the correct handler module for a known task_id.
  - Dispatcher raises TaskHandlerNotFoundError for unknown task_id.
  - Dispatcher raises AttributeError if module lacks the requested method.
  - _enumerate_candidates wires to TaskRegistry (catalog round-trip).
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.core.engine import MaintenanceEngine, TaskHandlerNotFoundError
from maintenance_worker.rules.parser import TaskCatalogEntry
from maintenance_worker.types.specs import EngineConfig, TaskSpec, TickContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path) -> TickContext:
    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )
    return TickContext(
        run_id="test-run-id",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
    )


def _make_entry(task_id: str) -> TaskCatalogEntry:
    spec = TaskSpec(task_id=task_id, description="test", schedule="daily")
    return TaskCatalogEntry(spec=spec, raw={"id": task_id, "schedule": "daily"})


# ---------------------------------------------------------------------------
# Dispatcher: known task_id imports correct module
# ---------------------------------------------------------------------------


def test_dispatch_calls_handler_function(tmp_path: Path) -> None:
    """
    _dispatch_by_task_id("fake_task", "enumerate", ...) should call
    the enumerate function from maintenance_worker.rules.fake_task.
    """
    sentinel = object()
    fake_mod = types.ModuleType("maintenance_worker.rules.fake_task")
    fake_mod.enumerate = lambda *args: sentinel  # type: ignore[attr-defined]

    engine = MaintenanceEngine()
    with patch.dict(sys.modules, {"maintenance_worker.rules.fake_task": fake_mod}):
        result = engine._dispatch_by_task_id("fake_task", "enumerate", "arg1")

    assert result is sentinel


def test_dispatch_raises_for_unknown_task_id(tmp_path: Path) -> None:
    """
    _dispatch_by_task_id raises TaskHandlerNotFoundError when no module exists.
    """
    engine = MaintenanceEngine()
    # Ensure the module is NOT in sys.modules
    sys.modules.pop("maintenance_worker.rules.nonexistent_task_xyz", None)

    with pytest.raises(TaskHandlerNotFoundError, match="nonexistent_task_xyz"):
        engine._dispatch_by_task_id("nonexistent_task_xyz", "enumerate")


def test_dispatch_raises_attribute_error_for_missing_method(tmp_path: Path) -> None:
    """
    If the handler module exists but lacks the requested method,
    _dispatch_by_task_id raises AttributeError.
    """
    fake_mod = types.ModuleType("maintenance_worker.rules.task_no_apply")
    # Intentionally no 'apply' attribute on this module

    engine = MaintenanceEngine()
    with patch.dict(sys.modules, {"maintenance_worker.rules.task_no_apply": fake_mod}):
        with pytest.raises(AttributeError, match="apply"):
            engine._dispatch_by_task_id("task_no_apply", "apply", "arg")


# ---------------------------------------------------------------------------
# _enumerate_candidates: catalog round-trip
# ---------------------------------------------------------------------------


MINIMAL_CATALOG = """\
schema_version: 1
tasks:
  - id: closed_packet_archive_proposal
    schedule: daily
    rule_source: ARCHIVAL_RULES.md
    dry_run: true
    live_default: false
  - id: authority_drift_surface
    schedule: weekly
    rule_source: REMEDIATION_PLAN.md
    dry_run: true
    live_default: false
"""


def test_enumerate_candidates_returns_daily_tasks(tmp_path: Path) -> None:
    """
    _enumerate_candidates wires to TaskRegistry.get_tasks_for_schedule("daily").
    Weekly tasks must be excluded.
    """
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.write_text(MINIMAL_CATALOG, encoding="utf-8")

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
    entries = engine._enumerate_candidates(config)

    task_ids = [e.spec.task_id for e in entries]
    assert "closed_packet_archive_proposal" in task_ids, "daily task must be included"
    assert "authority_drift_surface" not in task_ids, "weekly task must be excluded"


def test_enumerate_candidates_missing_catalog_returns_empty(tmp_path: Path) -> None:
    """
    _enumerate_candidates returns [] (and logs a warning) when catalog is absent.
    Does NOT raise.
    """
    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=tmp_path / "nonexistent_catalog.yaml",
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=False,
        scheduler="launchd",
        notification_channel="discord",
    )
    engine = MaintenanceEngine()
    entries = engine._enumerate_candidates(config)
    assert entries == []
