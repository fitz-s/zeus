# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/ARCHIVAL_RULES.md
#   §"Exemption Checks (ALL must pass to archive)" check #0
#   maintenance_worker/core/archival_check_0.py
"""
Tests for maintenance_worker.core.archival_check_0.

Covers:
  - Registry hit with CURRENT_LOAD_BEARING → LOAD_BEARING verdict
  - Registry hit with CURRENT_HISTORICAL + archival_ok=true → ARCHIVABLE
  - Registry hit with CURRENT_HISTORICAL + archival_ok=false → LOAD_BEARING
  - Registry hit with ARCHIVED → ARCHIVABLE
  - Path not in registry → ARCHIVABLE
  - Registry file absent → WARN_REGISTRY_ABSENT + WARNING logged
  - Cache stability: registry loaded once per unique path
"""
from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest
import yaml

from maintenance_worker.core.archival_check_0 import (
    ArchivalCheckResult,
    _load_registry,
    check_authority_status,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_registry(tmp_path: Path, entries: list[dict]) -> Path:
    """Write a minimal artifact_authority_status.yaml and return its path."""
    registry_path = tmp_path / "artifact_authority_status.yaml"
    data = {
        "schema_version": 1,
        "metadata": {"created": "2026-05-16"},
        "entries": entries,
    }
    registry_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return registry_path


def _fresh_check(path: Path, registry_path: Path) -> ArchivalCheckResult:
    """Call check_authority_status with cache cleared to avoid cross-test contamination."""
    _load_registry.cache_clear()
    return check_authority_status(path, registry_path)


# ---------------------------------------------------------------------------
# Test: registry hit forces LOAD_BEARING for non-archivable statuses
# ---------------------------------------------------------------------------

class TestRegistryHitLoadBearing:
    def test_current_load_bearing_returns_load_bearing(self, tmp_path: Path) -> None:
        candidate = tmp_path / "docs/operations/task_2026-05-15_foo/DESIGN.md"
        registry = _write_registry(tmp_path, [
            {
                "path": "docs/operations/task_2026-05-15_foo/DESIGN.md",
                "status": "CURRENT_LOAD_BEARING",
                "last_confirmed": "2026-05-16",
                "confirmation_ttl_days": 30,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        result = _fresh_check(candidate, registry)
        assert result.verdict == "LOAD_BEARING"
        assert result.status_row is not None
        assert result.status_row["status"] == "CURRENT_LOAD_BEARING"

    def test_stale_rewrite_needed_returns_load_bearing(self, tmp_path: Path) -> None:
        candidate = tmp_path / "AGENTS.md"
        registry = _write_registry(tmp_path, [
            {
                "path": "AGENTS.md",
                "status": "STALE_REWRITE_NEEDED",
                "last_confirmed": "2026-05-10",
                "confirmation_ttl_days": 14,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        result = _fresh_check(candidate, registry)
        assert result.verdict == "LOAD_BEARING"

    def test_current_historical_without_archival_ok_returns_load_bearing(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "docs/operations/task_2026-05-06_topology_redesign"
        registry = _write_registry(tmp_path, [
            {
                "path": "docs/operations/task_2026-05-06_topology_redesign",
                "status": "CURRENT_HISTORICAL",
                "last_confirmed": "2026-05-15",
                "confirmation_ttl_days": 90,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        result = _fresh_check(candidate, registry)
        assert result.verdict == "LOAD_BEARING"
        assert "CURRENT_HISTORICAL" in result.reason

    def test_quarantine_returns_load_bearing(self, tmp_path: Path) -> None:
        candidate = tmp_path / "docs/operations/task_2026-04-01_bad_packet"
        registry = _write_registry(tmp_path, [
            {
                "path": "docs/operations/task_2026-04-01_bad_packet",
                "status": "QUARANTINE",
                "last_confirmed": "2026-05-01",
                "confirmation_ttl_days": 7,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        result = _fresh_check(candidate, registry)
        assert result.verdict == "LOAD_BEARING"


# ---------------------------------------------------------------------------
# Test: archival_ok=true overrides CURRENT_HISTORICAL → ARCHIVABLE
# ---------------------------------------------------------------------------

class TestArchivalOkOverride:
    def test_current_historical_with_archival_ok_true_returns_archivable(
        self, tmp_path: Path
    ) -> None:
        candidate = tmp_path / "docs/operations/task_2026-05-06_hook_redesign"
        registry = _write_registry(tmp_path, [
            {
                "path": "docs/operations/task_2026-05-06_hook_redesign",
                "status": "CURRENT_HISTORICAL",
                "last_confirmed": "2026-05-15",
                "confirmation_ttl_days": 90,
                "owner": "fitz",
                "archival_ok": True,
            }
        ])
        result = _fresh_check(candidate, registry)
        assert result.verdict == "ARCHIVABLE"
        assert result.status_row is not None
        assert result.status_row["archival_ok"] is True

    def test_archived_status_always_archivable(self, tmp_path: Path) -> None:
        candidate = tmp_path / "docs/operations/task_2026-03-01_old_packet"
        registry = _write_registry(tmp_path, [
            {
                "path": "docs/operations/task_2026-03-01_old_packet",
                "status": "ARCHIVED",
                "last_confirmed": "2026-04-01",
                "confirmation_ttl_days": 365,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        result = _fresh_check(candidate, registry)
        assert result.verdict == "ARCHIVABLE"

    def test_path_not_in_registry_returns_archivable(self, tmp_path: Path) -> None:
        candidate = tmp_path / "docs/operations/task_2026-01-01_unknown"
        registry = _write_registry(tmp_path, [
            {
                "path": "docs/operations/task_2026-05-15_something_else/DESIGN.md",
                "status": "CURRENT_LOAD_BEARING",
                "last_confirmed": "2026-05-16",
                "confirmation_ttl_days": 30,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        result = _fresh_check(candidate, registry)
        assert result.verdict == "ARCHIVABLE"
        assert result.status_row is None


# ---------------------------------------------------------------------------
# Test: registry absent → WARN_REGISTRY_ABSENT + WARNING log
# ---------------------------------------------------------------------------

class TestRegistryAbsent:
    def test_absent_registry_returns_warn(self, tmp_path: Path) -> None:
        candidate = tmp_path / "docs/operations/task_2026-05-15_foo"
        registry_path = tmp_path / "nonexistent_registry.yaml"
        result = _fresh_check(candidate, registry_path)
        assert result.verdict == "WARN_REGISTRY_ABSENT"
        assert result.status_row is None

    def test_absent_registry_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        candidate = tmp_path / "some_doc.md"
        registry_path = tmp_path / "missing.yaml"
        with caplog.at_level(logging.WARNING, logger="maintenance_worker.core.archival_check_0"):
            _fresh_check(candidate, registry_path)
        assert any(
            "absent or unreadable" in record.message
            for record in caplog.records
        ), f"Expected warning not found in: {[r.message for r in caplog.records]}"

    def test_absent_registry_does_not_raise(self, tmp_path: Path) -> None:
        candidate = tmp_path / "AGENTS.md"
        registry_path = tmp_path / "no_such_file.yaml"
        # Must not raise — caller should handle WARN_REGISTRY_ABSENT gracefully
        result = _fresh_check(candidate, registry_path)
        assert result.verdict == "WARN_REGISTRY_ABSENT"


# ---------------------------------------------------------------------------
# Test: cache stability
# ---------------------------------------------------------------------------

class TestCacheStability:
    def test_cache_returns_same_entries_on_second_call(self, tmp_path: Path) -> None:
        registry = _write_registry(tmp_path, [
            {
                "path": "docs/operations/task_2026-05-15_foo/DESIGN.md",
                "status": "CURRENT_LOAD_BEARING",
                "last_confirmed": "2026-05-16",
                "confirmation_ttl_days": 30,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        _load_registry.cache_clear()
        entries_first = _load_registry(str(registry))
        entries_second = _load_registry(str(registry))
        # Same object (cached)
        assert entries_first is entries_second

    def test_cache_clear_allows_reload(self, tmp_path: Path) -> None:
        registry = _write_registry(tmp_path, [])
        _load_registry.cache_clear()
        entries_empty = _load_registry(str(registry))
        assert entries_empty == []

        # Overwrite file and clear cache
        _write_registry(tmp_path, [
            {
                "path": "AGENTS.md",
                "status": "CURRENT_LOAD_BEARING",
                "last_confirmed": "2026-05-16",
                "confirmation_ttl_days": 14,
                "owner": "fitz",
                "archival_ok": False,
            }
        ])
        _load_registry.cache_clear()
        entries_reloaded = _load_registry(str(registry))
        assert len(entries_reloaded) == 1
