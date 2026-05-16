# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 (P5.3)
"""
Tests for maintenance_worker.rules.task_registry — TaskRegistry.

Coverage:
  - Construction from entries list
  - TaskRegistry.from_catalog() class-method constructor
  - get_task() → entry or TaskNotFoundError
  - list_tasks() → ordered list
  - get_tasks_for_schedule() → filtered list
  - is_task_paused() → True/False based on flag file
  - task_ids() convenience method
  - get_spec() convenience method
  - __len__, __contains__, __repr__
  - dry_run_floor_exempt cross-check:
      - authorized task_id (in FLOOR_EXEMPT_TASK_IDS) → OK
      - unauthorized task_id (not in frozenset) → UnauthorizedExemptionError
      - task without exempt flag → always OK regardless of frozenset membership
  - Real TASK_CATALOG.yaml integration via from_catalog()
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from maintenance_worker.core.install_metadata import FLOOR_EXEMPT_TASK_IDS
from maintenance_worker.rules.parser import TaskCatalogEntry, load_task_catalog
from maintenance_worker.rules.task_registry import (
    TaskNotFoundError,
    TaskRegistry,
    UnauthorizedExemptionError,
)
from maintenance_worker.types.specs import TaskSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REAL_CATALOG_PATH = Path(
    "docs/operations/task_2026-05-15_runtime_improvement_engineering_package"
    "/02_daily_maintenance_agent/TASK_CATALOG.yaml"
)


def make_entry(
    task_id: str,
    schedule: str = "daily",
    dry_run_floor_exempt: bool = False,
    description: str = "desc",
) -> TaskCatalogEntry:
    """Create a minimal TaskCatalogEntry for testing."""
    spec = TaskSpec(
        task_id=task_id,
        description=description,
        schedule=schedule,
        dry_run_floor_exempt=dry_run_floor_exempt,
    )
    return TaskCatalogEntry(spec=spec, raw={"id": task_id, "schedule": schedule})


def write_catalog(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "catalog.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_registry(self) -> None:
        reg = TaskRegistry([])
        assert len(reg) == 0
        assert reg.list_tasks() == []

    def test_single_entry(self) -> None:
        entry = make_entry("task_a")
        reg = TaskRegistry([entry])
        assert len(reg) == 1

    def test_multiple_entries(self) -> None:
        entries = [make_entry(f"task_{i}") for i in range(5)]
        reg = TaskRegistry(entries)
        assert len(reg) == 5

    def test_from_catalog_classmethod(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
              - id: t2
                schedule: weekly
            """,
        )
        reg = TaskRegistry.from_catalog(p)
        assert len(reg) == 2

    def test_from_catalog_with_env(self, tmp_path: Path) -> None:
        p = write_catalog(
            tmp_path,
            """\
            schema_version: 1
            tasks:
              - id: t1
                schedule: daily
                config:
                  path: ${REPO}/data
            """,
        )
        reg = TaskRegistry.from_catalog(p, env={"REPO": "/myrepo"})
        assert reg.get_task("t1").raw["config"]["path"] == "/myrepo/data"


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------


class TestGetTask:
    def test_get_registered_task(self) -> None:
        entry = make_entry("task_a")
        reg = TaskRegistry([entry])
        assert reg.get_task("task_a") is entry

    def test_get_unknown_task_raises(self) -> None:
        reg = TaskRegistry([make_entry("task_a")])
        with pytest.raises(TaskNotFoundError):
            reg.get_task("nonexistent")

    def test_task_not_found_message_includes_known_tasks(self) -> None:
        reg = TaskRegistry([make_entry("task_a"), make_entry("task_b")])
        with pytest.raises(TaskNotFoundError, match="task_a"):
            reg.get_task("unknown")


# ---------------------------------------------------------------------------
# list_tasks / ordering
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_list_tasks_empty(self) -> None:
        assert TaskRegistry([]).list_tasks() == []

    def test_list_tasks_order_preserved(self) -> None:
        ids = ["task_c", "task_a", "task_b"]
        entries = [make_entry(tid) for tid in ids]
        reg = TaskRegistry(entries)
        assert [e.spec.task_id for e in reg.list_tasks()] == ids

    def test_list_tasks_returns_copy(self) -> None:
        """Mutating the returned list must not affect registry internals."""
        entry = make_entry("t1")
        reg = TaskRegistry([entry])
        lst = reg.list_tasks()
        lst.clear()
        assert len(reg) == 1


# ---------------------------------------------------------------------------
# get_tasks_for_schedule
# ---------------------------------------------------------------------------


class TestGetTasksForSchedule:
    def test_filter_daily(self) -> None:
        entries = [
            make_entry("t1", schedule="daily"),
            make_entry("t2", schedule="weekly"),
            make_entry("t3", schedule="daily"),
        ]
        reg = TaskRegistry(entries)
        daily = reg.get_tasks_for_schedule("daily")
        assert [e.spec.task_id for e in daily] == ["t1", "t3"]

    def test_filter_weekly(self) -> None:
        entries = [
            make_entry("t1", schedule="daily"),
            make_entry("t2", schedule="weekly"),
        ]
        reg = TaskRegistry(entries)
        assert [e.spec.task_id for e in reg.get_tasks_for_schedule("weekly")] == ["t2"]

    def test_filter_unknown_schedule_returns_empty(self) -> None:
        reg = TaskRegistry([make_entry("t1", schedule="daily")])
        assert reg.get_tasks_for_schedule("monthly") == []

    def test_schedule_match_is_case_sensitive(self) -> None:
        reg = TaskRegistry([make_entry("t1", schedule="Daily")])
        assert reg.get_tasks_for_schedule("daily") == []
        assert len(reg.get_tasks_for_schedule("Daily")) == 1


# ---------------------------------------------------------------------------
# is_task_paused
# ---------------------------------------------------------------------------


class TestIsTaskPaused:
    def test_not_paused_when_flag_absent(self, tmp_path: Path) -> None:
        reg = TaskRegistry([make_entry("t1")])
        pause_dir = tmp_path / "pauses"
        pause_dir.mkdir()
        assert reg.is_task_paused("t1", pause_dir) is False

    def test_paused_when_flag_present(self, tmp_path: Path) -> None:
        reg = TaskRegistry([make_entry("t1")])
        pause_dir = tmp_path / "pauses"
        pause_dir.mkdir()
        (pause_dir / "t1.pause").touch()
        assert reg.is_task_paused("t1", pause_dir) is True

    def test_pause_flag_for_other_task_does_not_affect(self, tmp_path: Path) -> None:
        reg = TaskRegistry([make_entry("t1"), make_entry("t2")])
        pause_dir = tmp_path / "pauses"
        pause_dir.mkdir()
        (pause_dir / "t2.pause").touch()
        assert reg.is_task_paused("t1", pause_dir) is False
        assert reg.is_task_paused("t2", pause_dir) is True

    def test_pause_check_works_for_unregistered_task_id(self, tmp_path: Path) -> None:
        """is_task_paused does not require the task to be registered."""
        reg = TaskRegistry([])
        pause_dir = tmp_path / "pauses"
        pause_dir.mkdir()
        # no flag file
        assert reg.is_task_paused("ghost_task", pause_dir) is False


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


class TestConvenienceMethods:
    def test_task_ids(self) -> None:
        entries = [make_entry("b"), make_entry("a"), make_entry("c")]
        reg = TaskRegistry(entries)
        assert reg.task_ids() == ["b", "a", "c"]

    def test_get_spec(self) -> None:
        entry = make_entry("t1")
        reg = TaskRegistry([entry])
        spec = reg.get_spec("t1")
        assert isinstance(spec, TaskSpec)
        assert spec.task_id == "t1"

    def test_get_spec_unknown_raises(self) -> None:
        reg = TaskRegistry([])
        with pytest.raises(TaskNotFoundError):
            reg.get_spec("nope")

    def test_contains_true(self) -> None:
        reg = TaskRegistry([make_entry("t1")])
        assert "t1" in reg

    def test_contains_false(self) -> None:
        reg = TaskRegistry([make_entry("t1")])
        assert "t2" not in reg

    def test_repr(self) -> None:
        reg = TaskRegistry([make_entry("t1"), make_entry("t2")])
        r = repr(reg)
        assert "TaskRegistry" in r
        assert "2 tasks" in r

    def test_len(self) -> None:
        assert len(TaskRegistry([])) == 0
        assert len(TaskRegistry([make_entry("t1")])) == 1


# ---------------------------------------------------------------------------
# dry_run_floor_exempt cross-check
# ---------------------------------------------------------------------------


class TestDryRunFloorExemptCrossCheck:
    """
    SCAFFOLD §4: catalog claims of dry_run_floor_exempt must cross-check
    against FLOOR_EXEMPT_TASK_IDS frozenset.
    """

    def test_authorized_exempt_task_accepted(self) -> None:
        """A task in FLOOR_EXEMPT_TASK_IDS may claim dry_run_floor_exempt=True."""
        # Pick one of the two hardcoded exempt IDs
        exempt_id = next(iter(FLOOR_EXEMPT_TASK_IDS))
        entry = make_entry(exempt_id, dry_run_floor_exempt=True)
        reg = TaskRegistry([entry])  # must not raise
        assert reg.get_spec(exempt_id).dry_run_floor_exempt is True

    def test_unauthorized_exempt_claim_raises(self) -> None:
        """A task NOT in FLOOR_EXEMPT_TASK_IDS may NOT claim dry_run_floor_exempt=True."""
        entry = make_entry("totally_new_task", dry_run_floor_exempt=True)
        with pytest.raises(UnauthorizedExemptionError, match="totally_new_task"):
            TaskRegistry([entry])

    def test_non_exempt_task_always_accepted(self) -> None:
        """dry_run_floor_exempt=False never triggers the cross-check."""
        # Even a task_id that happens to be in FLOOR_EXEMPT_TASK_IDS but
        # claims False is fine
        exempt_id = next(iter(FLOOR_EXEMPT_TASK_IDS))
        entry = make_entry(exempt_id, dry_run_floor_exempt=False)
        reg = TaskRegistry([entry])
        assert reg.get_spec(exempt_id).dry_run_floor_exempt is False

    def test_both_authorized_exempt_tasks_accepted(self) -> None:
        """Both hardcoded exempt task IDs may claim dry_run_floor_exempt=True."""
        entries = [
            make_entry("zero_byte_state_cleanup", dry_run_floor_exempt=True),
            make_entry("agent_self_evidence_archival", dry_run_floor_exempt=True),
        ]
        reg = TaskRegistry(entries)
        assert len(reg) == 2

    def test_error_message_names_frozenset(self) -> None:
        """Error message must mention FLOOR_EXEMPT_TASK_IDS for diagnosis."""
        entry = make_entry("unauthorized_task", dry_run_floor_exempt=True)
        with pytest.raises(UnauthorizedExemptionError) as exc_info:
            TaskRegistry([entry])
        # Error should name the frozenset contents
        msg = str(exc_info.value)
        assert "FLOOR_EXEMPT_TASK_IDS" in msg

    def test_cross_check_fails_on_second_of_multiple_entries(self) -> None:
        """Cross-check runs on every entry; failure on entry N still raises."""
        good = make_entry("zero_byte_state_cleanup", dry_run_floor_exempt=True)
        bad = make_entry("unauthorized_task", dry_run_floor_exempt=True)
        with pytest.raises(UnauthorizedExemptionError, match="unauthorized_task"):
            TaskRegistry([good, bad])


# ---------------------------------------------------------------------------
# Real TASK_CATALOG.yaml integration
# ---------------------------------------------------------------------------


class TestRealCatalogIntegration:
    def test_real_catalog_registry_loads(self) -> None:
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        reg = TaskRegistry.from_catalog(REAL_CATALOG_PATH, env={})
        assert len(reg) == 9

    def test_real_catalog_exempt_tasks_in_registry(self) -> None:
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        reg = TaskRegistry.from_catalog(REAL_CATALOG_PATH, env={})
        assert reg.get_spec("zero_byte_state_cleanup").dry_run_floor_exempt is True
        assert reg.get_spec("agent_self_evidence_archival").dry_run_floor_exempt is True

    def test_real_catalog_daily_tasks(self) -> None:
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        reg = TaskRegistry.from_catalog(REAL_CATALOG_PATH, env={})
        daily = reg.get_tasks_for_schedule("daily")
        # authority_drift_surface is weekly; all others are daily
        assert len(daily) == 8
        daily_ids = {e.spec.task_id for e in daily}
        assert "authority_drift_surface" not in daily_ids

    def test_real_catalog_weekly_tasks(self) -> None:
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        reg = TaskRegistry.from_catalog(REAL_CATALOG_PATH, env={})
        weekly = reg.get_tasks_for_schedule("weekly")
        assert len(weekly) == 1
        assert weekly[0].spec.task_id == "authority_drift_surface"

    def test_real_catalog_get_task_by_id(self) -> None:
        if not REAL_CATALOG_PATH.exists():
            pytest.skip(f"Real catalog not found: {REAL_CATALOG_PATH}")
        reg = TaskRegistry.from_catalog(REAL_CATALOG_PATH, env={})
        entry = reg.get_task("zero_byte_state_cleanup")
        assert entry.spec.task_id == "zero_byte_state_cleanup"
