# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: SCAFFOLD.md §3 topology verdict D7 + EXECUTION_PLAN.md W5 + Batch-C brief step 1.4
"""Tests for topology_doctor_docs_checks module.

D7: check_expected_empty_zones enforces that zones declared expected_empty: true
in topology.yaml contain only .gitkeep (or nothing at all — absent path is OK).
"""
from __future__ import annotations

import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from scripts.topology_doctor_docs_checks import check_expected_empty_zones


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
