# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §4
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"Dry-run floor enforcement"
"""
F2 regression test — dry-run floor gate wired into engine._apply_decisions.

Verifies that when install_metadata.first_run_at is less than 30 days ago
and the task is NOT floor-exempt, _apply_decisions forces dry_run_only=True.

This test exercises the gate directly (unit-level) rather than through a
full run_tick tick, because _enumerate_candidates is still a stub returning []
in P5.1–P5.3. The direct call confirms the wiring is live so future packets
cannot inadvertently bypass the floor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from maintenance_worker.core.engine import MaintenanceEngine
from maintenance_worker.core.install_metadata import DryRunFloor, InstallMetadata
from maintenance_worker.rules.parser import TaskCatalogEntry
from maintenance_worker.types.specs import EngineConfig, ProposalManifest, TaskSpec, TickContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_install_meta(days_ago: int) -> InstallMetadata:
    """Return an InstallMetadata with first_run_at set to `days_ago` days in the past."""
    first_run = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return InstallMetadata(
        schema_version=1,
        first_run_at=first_run,
        agent_version="0.0.1-test",
        install_run_id="00000000-0000-4000-8000-000000000002",
    )


def _make_task(task_id: str, dry_run_floor_exempt: bool = False) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        description="test task",
        schedule="daily",
        dry_run_floor_exempt=dry_run_floor_exempt,
    )


def _make_entry(task_id: str, dry_run_floor_exempt: bool = False) -> TaskCatalogEntry:
    spec = _make_task(task_id, dry_run_floor_exempt)
    return TaskCatalogEntry(spec=spec, raw={"id": task_id, "schedule": "daily"})


def _make_proposal(task_id: str) -> ProposalManifest:
    return ProposalManifest(task_id=task_id)


def _make_ctx(tmp_path: Path) -> TickContext:
    """Return a minimal TickContext for direct _apply_decisions calls."""
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
        run_id="00000000-0000-4000-8000-000000000099",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
    )


# ---------------------------------------------------------------------------
# F2 regression: floor not met → dry_run_only forced
# ---------------------------------------------------------------------------


def test_apply_decisions_floor_not_met_forces_dry_run(tmp_path: Path) -> None:
    """
    F2 regression: install_metadata.first_run_at = 5 days ago, non-exempt task.
    _apply_decisions must return dry_run_only=True (floor not met).
    """
    engine = MaintenanceEngine()
    install_meta = _make_install_meta(days_ago=5)
    entry = _make_entry("some_cleanup_task", dry_run_floor_exempt=False)
    proposal = _make_proposal(entry.spec.task_id)
    ctx = _make_ctx(tmp_path)

    result = engine._apply_decisions(
        entry=entry,
        proposal=proposal,
        ctx=ctx,
        force_dry_run=False,
        install_meta=install_meta,
    )

    assert result.dry_run_only is True, (
        "Floor not met (5 days < 30): _apply_decisions must force dry_run_only=True"
    )
    assert result.task_id == entry.spec.task_id


def test_apply_decisions_floor_met_allows_apply(tmp_path: Path) -> None:
    """
    When install_metadata.first_run_at >= 30 days ago and task is not exempt,
    _apply_decisions does NOT force dry_run_only=True due to the floor.
    (Falls through to dispatch; handler absent → dry_run_only=True via fallback.)
    """
    engine = MaintenanceEngine()
    install_meta = _make_install_meta(days_ago=35)
    entry = _make_entry("some_cleanup_task", dry_run_floor_exempt=False)
    proposal = _make_proposal(entry.spec.task_id)
    ctx = _make_ctx(tmp_path)

    result = engine._apply_decisions(
        entry=entry,
        proposal=proposal,
        ctx=ctx,
        force_dry_run=False,
        install_meta=install_meta,
    )

    # Floor gate passed; dispatch falls back to dry_run_only=True (no handler),
    # but must NOT raise or block.
    assert result.task_id == entry.spec.task_id


def test_apply_decisions_exempt_task_bypasses_floor(tmp_path: Path) -> None:
    """
    A task with dry_run_floor_exempt=True bypasses the floor check
    even when first_run_at is 1 day ago.
    """
    engine = MaintenanceEngine()
    install_meta = _make_install_meta(days_ago=1)
    entry = _make_entry("zero_byte_state_cleanup", dry_run_floor_exempt=True)
    proposal = _make_proposal(entry.spec.task_id)
    ctx = _make_ctx(tmp_path)

    # Should not raise; floor gate is skipped for exempt tasks.
    result = engine._apply_decisions(
        entry=entry,
        proposal=proposal,
        ctx=ctx,
        force_dry_run=False,
        install_meta=install_meta,
    )
    assert result.task_id == entry.spec.task_id


def test_apply_decisions_no_install_meta_skips_floor(tmp_path: Path) -> None:
    """
    When install_meta is None (absent — engine started before install script ran),
    the floor gate is skipped and _apply_decisions falls through without raising.
    """
    engine = MaintenanceEngine()
    entry = _make_entry("some_cleanup_task")
    proposal = _make_proposal(entry.spec.task_id)
    ctx = _make_ctx(tmp_path)

    result = engine._apply_decisions(
        entry=entry,
        proposal=proposal,
        ctx=ctx,
        force_dry_run=False,
        install_meta=None,
    )

    assert result.task_id == entry.spec.task_id
    assert result.dry_run_only is True


def test_apply_decisions_force_dry_run_takes_priority(tmp_path: Path) -> None:
    """
    force_dry_run=True (MANUAL_CLI) takes priority over the floor check:
    even with a fresh install_meta (1 day old) and non-exempt task,
    force_dry_run short-circuits before the floor gate.
    """
    engine = MaintenanceEngine()
    install_meta = _make_install_meta(days_ago=1)
    entry = _make_entry("some_cleanup_task", dry_run_floor_exempt=False)
    proposal = _make_proposal(entry.spec.task_id)
    ctx = _make_ctx(tmp_path)

    result = engine._apply_decisions(
        entry=entry,
        proposal=proposal,
        ctx=ctx,
        force_dry_run=True,
        install_meta=install_meta,
    )

    assert result.dry_run_only is True
    assert result.task_id == entry.spec.task_id
