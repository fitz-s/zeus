# Created: 2026-05-17
# Last reused or audited: 2026-05-23
# Authority basis: SCAFFOLD.md §3 topology verdict D7 + EXECUTION_PLAN.md W5 + Batch-C brief step 1.4
#                  Phase-1 Harness plan (composed-marinating-rabin.md) checks H1-H4
#                  H5-H6 sprawl antibody: scaffold_antibody.md (2026-05-23)
"""Tests for topology_doctor_docs_checks module.

D7: check_expected_empty_zones enforces that zones declared expected_empty: true
in topology.yaml contain only .gitkeep (or nothing at all — absent path is OK).

H1-H4 (Harness): fail-closed steering checks for docs/operations pointer integrity.

H5 (operations_task_dir_sprawl_ceiling): any unregistered or closed/historical
task_* dir fires RED. Steady-state target is ZERO top-level task_* dirs.

H6 (operations_new_packet_outside_current): any task_* dir dated >= current
package.yaml created_at fires RED — it's a route bypass.
"""
from __future__ import annotations

import types
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from scripts.topology_doctor_docs_checks import (
    check_current_state_freshness,
    check_expected_empty_zones,
    check_multiple_active_pointers,
    check_new_packet_outside_current,
    check_stale_current_fact_referenced,
    check_task_dir_sprawl_ceiling,
    check_task_dirs_fully_unregistered,
)


def _make_api(root: Path, tracked_files: list[str]) -> Any:
    """Build a minimal topology_doctor api mock for docs-checks testing."""
    api = MagicMock()
    api.ROOT = root
    api._git_visible_files.return_value = tracked_files

    def _issue(code: str, path: str, msg: str) -> Any:
        issue = types.SimpleNamespace(code=code, path=path, message=msg)
        return issue

    api._issue.side_effect = _issue
    return api


def _make_harness_api(
    root: Path,
    *,
    tracked_files: list[str] | None = None,
    registry_entries: list[dict] | None = None,
    agents_registered: set[str] | None = None,
) -> Any:
    """Harness-specific api mock that also stubs load_docs_registry and _registry_entries."""
    api = _make_api(root, tracked_files or [])
    api.load_docs_registry.return_value = {"entries": registry_entries or []}
    api._registry_entries.return_value = agents_registered or set()
    api._markdown_path_tokens.return_value = set()
    return api


def _topology_with_cold_zone(cold_path: str = "docs/operations/archive/cold") -> dict[str, Any]:
    """Return a minimal topology dict with one expected_empty zone."""
    return {
        "docs_subroots": [
            {
                "path": cold_path,
                "role": "historical_archive",
                "expected_empty": True,
            }
        ]
    }


class TestCheckExpectedEmptyZones:
    def test_happy_case_gitkeep_only(self, tmp_path: Path) -> None:
        """Cold zone containing only .gitkeep must produce zero issues."""
        cold_dir = tmp_path / "docs" / "operations" / "archive" / "cold"
        cold_dir.mkdir(parents=True)
        (cold_dir / ".gitkeep").touch()

        tracked = ["docs/operations/archive/cold/.gitkeep"]
        api = _make_api(tmp_path, tracked)
        topology = _topology_with_cold_zone()

        issues = check_expected_empty_zones(api, topology)

        assert issues == [], f"Expected no issues for .gitkeep-only cold zone, got: {issues}"

    def test_happy_case_absent_path(self, tmp_path: Path) -> None:
        """Cold zone path that does not exist at all must produce zero issues (absent == empty)."""
        # Do NOT create the cold dir — it simply doesn't exist.
        tracked: list[str] = []
        api = _make_api(tmp_path, tracked)
        topology = _topology_with_cold_zone()

        issues = check_expected_empty_zones(api, topology)

        assert issues == [], f"Expected no issues for absent cold zone, got: {issues}"

    def test_failure_stray_file_in_cold_zone(self, tmp_path: Path) -> None:
        """Cold zone with a tracked non-.gitkeep file must raise expected_empty_violation."""
        cold_dir = tmp_path / "docs" / "operations" / "archive" / "cold"
        cold_dir.mkdir(parents=True)
        stray = cold_dir / "unexpected.md"
        stray.touch()

        tracked = ["docs/operations/archive/cold/unexpected.md"]
        api = _make_api(tmp_path, tracked)
        topology = _topology_with_cold_zone()

        issues = check_expected_empty_zones(api, topology)

        assert len(issues) == 1, f"Expected exactly 1 issue, got: {issues}"
        assert issues[0].code == "expected_empty_violation"
        assert "docs/operations/archive/cold/unexpected.md" in issues[0].path

    def test_non_expected_empty_zone_not_checked(self, tmp_path: Path) -> None:
        """Zones without expected_empty: true must not be flagged even with stray files."""
        docs_dir = tmp_path / "docs" / "reference"
        docs_dir.mkdir(parents=True)

        tracked = ["docs/reference/some_doc.md"]
        api = _make_api(tmp_path, tracked)
        topology = {
            "docs_subroots": [
                {
                    "path": "docs/reference",
                    "role": "reference",
                    # No expected_empty key at all
                }
            ]
        }

        issues = check_expected_empty_zones(api, topology)

        assert issues == [], f"Non-expected_empty zone should not produce issues, got: {issues}"


# ---------------------------------------------------------------------------
# H1: current_state.md freshness
# ---------------------------------------------------------------------------

def _write_current_state(root: Path, last_updated: str) -> None:
    """Write a minimal current_state.md with given Last updated date."""
    cs_dir = root / "docs" / "operations"
    cs_dir.mkdir(parents=True, exist_ok=True)
    (cs_dir / "current_state.md").write_text(
        f"# Current State\n\nLast updated: {last_updated}\n\n"
        "- Active execution packet: `docs/operations/task_foo/plan.md`.\n",
        encoding="utf-8",
    )


class TestCheckCurrentStateFreshness:
    def test_fresh_passes(self, tmp_path: Path) -> None:
        """current_state updated today must produce zero issues."""
        today = date.today().isoformat()
        _write_current_state(tmp_path, today)
        api = _make_harness_api(tmp_path)
        issues = check_current_state_freshness(api, {}, max_days=14)
        assert issues == [], f"Fresh current_state should not flag, got: {issues}"

    def test_stale_fails(self, tmp_path: Path) -> None:
        """current_state older than max_days must emit operations_current_state_stale."""
        old_date = (date.today() - timedelta(days=20)).isoformat()
        _write_current_state(tmp_path, old_date)
        api = _make_harness_api(tmp_path)
        issues = check_current_state_freshness(api, {}, max_days=14)
        assert len(issues) == 1, f"Expected 1 issue, got: {issues}"
        assert issues[0].code == "operations_current_state_stale"
        assert old_date in issues[0].message

    def test_missing_header_fails(self, tmp_path: Path) -> None:
        """current_state without 'Last updated:' header must be flagged."""
        cs_dir = tmp_path / "docs" / "operations"
        cs_dir.mkdir(parents=True)
        (cs_dir / "current_state.md").write_text("# Current State\n\nNo date here.\n", encoding="utf-8")
        api = _make_harness_api(tmp_path)
        issues = check_current_state_freshness(api, {}, max_days=14)
        assert len(issues) == 1
        assert issues[0].code == "operations_current_state_stale"

    def test_missing_file_is_silent(self, tmp_path: Path) -> None:
        """If current_state.md does not exist, this check is silent (other checks handle it)."""
        api = _make_harness_api(tmp_path)
        issues = check_current_state_freshness(api, {}, max_days=14)
        assert issues == []


# ---------------------------------------------------------------------------
# H2: multiple active pointers
# ---------------------------------------------------------------------------

def _write_cs_with_packets(root: Path, packets: list[str]) -> None:
    """Write a current_state.md with the given active-packet lines."""
    cs_dir = root / "docs" / "operations"
    cs_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Current State\n", "Last updated: 2026-05-22\n\n"]
    for p in packets:
        lines.append(f"- Active execution packet: `{p}`.\n")
    (cs_dir / "current_state.md").write_text("".join(lines), encoding="utf-8")


class TestCheckMultipleActivePointers:
    def test_single_pointer_passes(self, tmp_path: Path) -> None:
        """One active execution packet must produce zero issues."""
        _write_cs_with_packets(tmp_path, ["docs/operations/task_a/plan.md"])
        api = _make_harness_api(tmp_path)
        issues = check_multiple_active_pointers(api, {})
        assert issues == [], f"Single pointer should not flag, got: {issues}"

    def test_two_distinct_pointers_fails(self, tmp_path: Path) -> None:
        """Two distinct active execution packet lines must emit operations_multiple_active_pointers."""
        _write_cs_with_packets(tmp_path, [
            "docs/operations/task_a/plan.md",
            "docs/operations/task_b/plan.md",
        ])
        api = _make_harness_api(tmp_path)
        issues = check_multiple_active_pointers(api, {})
        assert len(issues) == 1, f"Expected 1 issue, got: {issues}"
        assert issues[0].code == "operations_multiple_active_pointers"
        assert "task_a" in issues[0].message
        assert "task_b" in issues[0].message

    def test_duplicate_same_pointer_passes(self, tmp_path: Path) -> None:
        """Same packet path repeated twice counts as one distinct pointer — no issue."""
        _write_cs_with_packets(tmp_path, [
            "docs/operations/task_a/plan.md",
            "docs/operations/task_a/plan.md",
        ])
        api = _make_harness_api(tmp_path)
        issues = check_multiple_active_pointers(api, {})
        assert issues == []

    def test_missing_file_is_silent(self, tmp_path: Path) -> None:
        api = _make_harness_api(tmp_path)
        issues = check_multiple_active_pointers(api, {})
        assert issues == []


# ---------------------------------------------------------------------------
# H3: task dirs fully unregistered
# ---------------------------------------------------------------------------

class TestCheckTaskDirsFullyUnregistered:
    def _make_task_dir(self, root: Path, name: str = "task_2026-05-22_foo") -> Path:
        d = root / "docs" / "operations" / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_registered_in_agents_passes(self, tmp_path: Path) -> None:
        """Dir registered in AGENTS.md but not docs_registry is NOT double-unregistered."""
        self._make_task_dir(tmp_path)
        # Create AGENTS.md so the agents_path.exists() guard passes
        agents_path = tmp_path / "docs" / "operations" / "AGENTS.md"
        agents_path.write_text("# AGENTS\n", encoding="utf-8")
        api = _make_harness_api(
            tmp_path,
            agents_registered={"task_2026-05-22_foo", "task_2026-05-22_foo/"},
        )
        issues = check_task_dirs_fully_unregistered(api, {})
        assert issues == []

    def test_registered_in_docs_registry_passes(self, tmp_path: Path) -> None:
        """Dir covered by docs_registry but not AGENTS.md is NOT double-unregistered."""
        self._make_task_dir(tmp_path)
        entry = {
            "path": "docs/operations/task_2026-05-22_foo/",
            "coverage_scope": "descendants",
            "parent_coverage_allowed": True,
        }
        api = _make_harness_api(tmp_path, registry_entries=[entry])
        issues = check_task_dirs_fully_unregistered(api, {})
        assert issues == []

    def test_unregistered_in_both_fails(self, tmp_path: Path) -> None:
        """Dir absent from both AGENTS.md and docs_registry must emit operations_task_dir_fully_unregistered."""
        self._make_task_dir(tmp_path)
        api = _make_harness_api(tmp_path)  # empty registry, empty agents
        issues = check_task_dirs_fully_unregistered(api, {})
        assert len(issues) == 1, f"Expected 1 issue, got: {issues}"
        assert issues[0].code == "operations_task_dir_fully_unregistered"
        assert "task_2026-05-22_foo" in issues[0].path

    def test_no_task_dirs_is_silent(self, tmp_path: Path) -> None:
        """No task_* dirs means no issues."""
        (tmp_path / "docs" / "operations").mkdir(parents=True, exist_ok=True)
        api = _make_harness_api(tmp_path)
        issues = check_task_dirs_fully_unregistered(api, {})
        assert issues == []


# ---------------------------------------------------------------------------
# H4: stale current-fact surface referenced by active task
# ---------------------------------------------------------------------------

def _write_surface(root: Path, rel: str, last_audited: str, max_days: int = 14) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"Last audited: {last_audited}\nMax staleness: {max_days} days\n",
        encoding="utf-8",
    )


def _write_cs_referencing(root: Path, surface_rel: str, active_packet: str) -> None:
    cs_dir = root / "docs" / "operations"
    cs_dir.mkdir(parents=True, exist_ok=True)
    (cs_dir / "current_state.md").write_text(
        f"# Current State\n\nLast updated: 2026-05-22\n\n"
        f"- Active execution packet: `{active_packet}`.\n"
        f"See also `{surface_rel}`.\n",
        encoding="utf-8",
    )


class TestCheckStaleCurrentFactReferenced:
    def test_fresh_surface_passes(self, tmp_path: Path) -> None:
        """A surface within its max staleness window must produce zero issues."""
        today = date.today().isoformat()
        surface = "docs/operations/current_data_state.md"
        active_packet = "docs/operations/task_foo/plan.md"
        _write_surface(tmp_path, surface, today, max_days=14)
        _write_cs_referencing(tmp_path, surface, active_packet)
        (tmp_path / "docs" / "operations" / "task_foo").mkdir(parents=True, exist_ok=True)
        (tmp_path / active_packet).write_text("task content", encoding="utf-8")
        api = _make_harness_api(tmp_path)
        issues = check_stale_current_fact_referenced(api, {})
        assert issues == [], f"Fresh surface should not flag, got: {issues}"

    def test_stale_referenced_surface_fails(self, tmp_path: Path) -> None:
        """A stale surface referenced by the active task must emit operations_stale_current_fact_referenced."""
        old_date = (date.today() - timedelta(days=30)).isoformat()
        surface = "docs/operations/current_data_state.md"
        active_packet = "docs/operations/task_foo/plan.md"
        _write_surface(tmp_path, surface, old_date, max_days=14)
        _write_cs_referencing(tmp_path, surface, active_packet)
        (tmp_path / "docs" / "operations" / "task_foo").mkdir(parents=True, exist_ok=True)
        (tmp_path / active_packet).write_text("task content", encoding="utf-8")
        api = _make_harness_api(tmp_path)
        issues = check_stale_current_fact_referenced(api, {})
        assert len(issues) == 1, f"Expected 1 issue, got: {issues}"
        assert issues[0].code == "operations_stale_current_fact_referenced"
        assert old_date in issues[0].message

    def test_stale_unreferenced_surface_passes(self, tmp_path: Path) -> None:
        """A stale surface NOT referenced by the active task must not be flagged."""
        old_date = (date.today() - timedelta(days=30)).isoformat()
        surface = "docs/operations/current_data_state.md"
        _write_surface(tmp_path, surface, old_date, max_days=14)
        # current_state does NOT mention the surface
        cs_dir = tmp_path / "docs" / "operations"
        cs_dir.mkdir(parents=True, exist_ok=True)
        (cs_dir / "current_state.md").write_text(
            "# Current State\n\nLast updated: 2026-05-22\n\n"
            "- Active execution packet: `docs/operations/task_foo/plan.md`.\n",
            encoding="utf-8",
        )
        api = _make_harness_api(tmp_path)
        issues = check_stale_current_fact_referenced(api, {})
        assert issues == [], f"Unreferenced stale surface should not flag, got: {issues}"

    def test_no_active_packet_is_silent(self, tmp_path: Path) -> None:
        """No active packet in current_state means no cross-reference possible — silent."""
        cs_dir = tmp_path / "docs" / "operations"
        cs_dir.mkdir(parents=True)
        (cs_dir / "current_state.md").write_text(
            "# Current State\n\nLast updated: 2026-05-22\n\n",
            encoding="utf-8",
        )
        api = _make_harness_api(tmp_path)
        issues = check_stale_current_fact_referenced(api, {})
        assert issues == []


# H5: task_dir_sprawl_ceiling — break/restore meta-verify
# ---------------------------------------------------------------------------


class TestCheckTaskDirSprawlCeiling:
    """H5: any unregistered or closed/historical task_* dir fires RED.

    Antibody proof: each test creates the condition → asserts RED, then removes
    the condition → asserts GREEN. This confirms the check catches what its
    docstring claims.
    """

    def test_unregistered_task_dir_fires_then_green(self, tmp_path: Path) -> None:
        """Break: unregistered task_* dir → RED. Restore: remove dir → GREEN."""
        ops = tmp_path / "docs" / "operations"
        task_dir = ops / "task_2026-05-01_test_unregistered"
        task_dir.mkdir(parents=True)

        api = _make_harness_api(tmp_path, registry_entries=[])
        issues = check_task_dir_sprawl_ceiling(api, {})
        assert len(issues) == 1
        assert issues[0].code == "operations_task_dir_sprawl_ceiling"
        assert "task_2026-05-01_test_unregistered" in issues[0].path
        assert "not registered" in issues[0].message or "unregistered" in issues[0].message.lower()

        # Restore: remove the dir
        task_dir.rmdir()
        issues_after = check_task_dir_sprawl_ceiling(api, {})
        assert issues_after == []

    def test_closed_lifecycle_fires_then_green(self, tmp_path: Path) -> None:
        """Break: closed task_* dir → RED. Restore: update lifecycle to active → GREEN."""
        ops = tmp_path / "docs" / "operations"
        task_dir = ops / "task_2026-05-01_test_closed"
        task_dir.mkdir(parents=True)

        closed_entries = [
            {
                "path": "docs/operations/task_2026-05-01_test_closed/",
                "lifecycle_state": "closed",
                "doc_class": "operation_packet",
            }
        ]
        api = _make_harness_api(tmp_path, registry_entries=closed_entries)
        issues = check_task_dir_sprawl_ceiling(api, {})
        assert len(issues) == 1
        assert issues[0].code == "operations_task_dir_sprawl_ceiling"
        assert "closed" in issues[0].message

        # Restore: change lifecycle_state to active
        active_entries = [
            {
                "path": "docs/operations/task_2026-05-01_test_closed/",
                "lifecycle_state": "active",
                "doc_class": "operation_packet",
            }
        ]
        api_active = _make_harness_api(tmp_path, registry_entries=active_entries)
        issues_after = check_task_dir_sprawl_ceiling(api_active, {})
        assert issues_after == []

    def test_historical_lifecycle_fires(self, tmp_path: Path) -> None:
        """historical lifecycle_state also fires H5."""
        ops = tmp_path / "docs" / "operations"
        (ops / "task_2026-04-01_old_historical").mkdir(parents=True)

        entries = [
            {
                "path": "docs/operations/task_2026-04-01_old_historical/",
                "lifecycle_state": "historical",
            }
        ]
        api = _make_harness_api(tmp_path, registry_entries=entries)
        issues = check_task_dir_sprawl_ceiling(api, {})
        assert len(issues) == 1
        assert issues[0].code == "operations_task_dir_sprawl_ceiling"
        assert "historical" in issues[0].message

    def test_transitional_lifecycle_passes(self, tmp_path: Path) -> None:
        """transitional is allowed — H5 should be silent."""
        ops = tmp_path / "docs" / "operations"
        (ops / "task_2026-05-01_transitional").mkdir(parents=True)

        entries = [
            {
                "path": "docs/operations/task_2026-05-01_transitional/",
                "lifecycle_state": "transitional",
            }
        ]
        api = _make_harness_api(tmp_path, registry_entries=entries)
        issues = check_task_dir_sprawl_ceiling(api, {})
        assert issues == []

    def test_active_lifecycle_passes(self, tmp_path: Path) -> None:
        """active lifecycle_state is allowed — H5 should be silent."""
        ops = tmp_path / "docs" / "operations"
        (ops / "task_2026-05-01_active_work").mkdir(parents=True)

        entries = [
            {
                "path": "docs/operations/task_2026-05-01_active_work/",
                "lifecycle_state": "active",
            }
        ]
        api = _make_harness_api(tmp_path, registry_entries=entries)
        issues = check_task_dir_sprawl_ceiling(api, {})
        assert issues == []

    def test_no_task_dirs_is_silent(self, tmp_path: Path) -> None:
        """No task_* dirs → GREEN. This is the steady-state target."""
        (tmp_path / "docs" / "operations").mkdir(parents=True)
        api = _make_harness_api(tmp_path, registry_entries=[])
        issues = check_task_dir_sprawl_ceiling(api, {})
        assert issues == []


# H6: new_packet_outside_current — break/restore meta-verify
# ---------------------------------------------------------------------------


class TestCheckNewPacketOutsideCurrent:
    """H6: task_* dir dated >= current package.yaml created_at fires RED.

    Antibody proof: each test creates the condition → asserts RED, then removes
    the condition → asserts GREEN.
    """

    def _write_package_yaml(self, tmp_path: Path, created_at: str) -> None:
        current_dir = tmp_path / "docs" / "operations" / "current"
        current_dir.mkdir(parents=True, exist_ok=True)
        (current_dir / "package.yaml").write_text(
            f"created_at: '{created_at}'\n", encoding="utf-8"
        )

    def test_task_dir_after_package_fires_then_green(self, tmp_path: Path) -> None:
        """Break: task_* dir dated after package → RED. Restore: remove dir → GREEN."""
        self._write_package_yaml(tmp_path, "2026-05-10")
        task_dir = tmp_path / "docs" / "operations" / "task_2026-05-11_bypass"
        task_dir.mkdir(parents=True)

        api = _make_harness_api(tmp_path)
        issues = check_new_packet_outside_current(api, {})
        assert len(issues) == 1
        assert issues[0].code == "operations_new_packet_outside_current"
        assert "2026-05-11" in issues[0].message
        assert "2026-05-10" in issues[0].message

        # Restore: remove the dir
        task_dir.rmdir()
        issues_after = check_new_packet_outside_current(api, {})
        assert issues_after == []

    def test_same_date_as_package_fires(self, tmp_path: Path) -> None:
        """Same date as package creation is a bypass (>= not just >)."""
        self._write_package_yaml(tmp_path, "2026-05-22")
        (tmp_path / "docs" / "operations" / "task_2026-05-22_same_day").mkdir(parents=True)

        api = _make_harness_api(tmp_path)
        issues = check_new_packet_outside_current(api, {})
        assert len(issues) == 1
        assert issues[0].code == "operations_new_packet_outside_current"

    def test_task_dir_before_package_passes(self, tmp_path: Path) -> None:
        """task_* dir dated before package creation is legacy — H6 is silent."""
        self._write_package_yaml(tmp_path, "2026-05-22")
        (tmp_path / "docs" / "operations" / "task_2026-05-15_legacy").mkdir(parents=True)

        api = _make_harness_api(tmp_path)
        issues = check_new_packet_outside_current(api, {})
        assert issues == []

    def test_no_package_yaml_is_silent(self, tmp_path: Path) -> None:
        """If current/package.yaml is absent, H6 does not fire (not applicable)."""
        (tmp_path / "docs" / "operations" / "task_2026-05-22_some_task").mkdir(parents=True)

        api = _make_harness_api(tmp_path)
        issues = check_new_packet_outside_current(api, {})
        assert issues == []

    def test_no_task_dirs_is_silent(self, tmp_path: Path) -> None:
        """No task_* dirs → GREEN even with package.yaml present."""
        self._write_package_yaml(tmp_path, "2026-05-10")
        (tmp_path / "docs" / "operations").mkdir(parents=True, exist_ok=True)

        api = _make_harness_api(tmp_path)
        issues = check_new_packet_outside_current(api, {})
        assert issues == []
