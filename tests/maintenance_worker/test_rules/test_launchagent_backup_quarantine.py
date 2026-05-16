# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5 STEP 2 B3
"""
Tests for maintenance_worker.rules.launchagent_backup_quarantine.

4 tests:
  1. Stale backup file with active plist → LAUNCHAGENT_BACKUP_CANDIDATE
  2. Fresh backup file (< ttl_days) → SKIP_TOO_FRESH
  3. Backup file with no corresponding active plist → SKIP_NO_ACTIVE_PLIST
  4. apply() always dry_run_only + mock diff present
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.launchagent_backup_quarantine import (
    VERDICT_CANDIDATE,
    VERDICT_SKIP_FRESH,
    VERDICT_SKIP_FORBIDDEN,
    VERDICT_SKIP_NO_ACTIVE_PLIST,
    apply,
    enumerate,
)
from maintenance_worker.rules.parser import TaskCatalogEntry
from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
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
        run_id="test-run-b3",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
    )


def _make_entry(ttl_days: int = 14) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="launchagent_backup_quarantine",
        description="category-1-launchagent-backup-shrapnel",
        schedule="daily",
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "launchagent_backup_quarantine",
            "schedule": "daily",
            "live_default": False,
            "config": {
                "backup_ttl_days": ttl_days,
                "quarantine_dir": "~/Library/LaunchAgents/.archive",
                "quarantine_retention_days": 90,
                "regex": r"\.(bak|backup|replaced|locked|before_[a-z_]+)[-._]?[0-9TZ]*(?:\.bak)?$",
            },
            "safety": {
                "forbidden_paths": [
                    "~/Library/LaunchAgents/com.zeus.*[!.bak]*[!.replaced]*[!.locked]*[!.before*]"
                ],
                "pre_check": "corresponding_active_plist_must_exist",
            },
        },
    )


def _old_mtime() -> float:
    """Return mtime 20 days in the past."""
    return time.time() - (20 * 86400)


def _fresh_mtime() -> float:
    """Return mtime 2 days in the past (within ttl=14)."""
    return time.time() - (2 * 86400)


# ---------------------------------------------------------------------------
# Test 1: Stale backup + active plist → LAUNCHAGENT_BACKUP_CANDIDATE
# ---------------------------------------------------------------------------


def test_stale_backup_with_active_plist_is_candidate(tmp_path: Path) -> None:
    """
    A stale backup file with a corresponding active .plist must be classified
    LAUNCHAGENT_BACKUP_CANDIDATE.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=14)

    # Fake LaunchAgents dir with a backup + active plist pair
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    active_plist = la_dir / "com.openclaw.venus.plist"
    active_plist.write_text("<?xml ...");
    backup_file = la_dir / "com.openclaw.venus.plist.bak"
    backup_file.write_text("old backup")

    with patch(
        "maintenance_worker.rules.launchagent_backup_quarantine._get_launch_agents_dir",
        return_value=la_dir,
    ), patch(
        "maintenance_worker.rules.launchagent_backup_quarantine._get_mtime",
        return_value=_old_mtime(),
    ):
        results = enumerate(entry, ctx)

    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE]
    assert len(candidates) == 1, f"Expected 1 candidate; got: {[c.verdict for c in results]}"
    assert candidates[0].path == backup_file
    assert candidates[0].task_id == "launchagent_backup_quarantine"


# ---------------------------------------------------------------------------
# Test 2: Fresh backup file (< ttl_days) → SKIP_TOO_FRESH
# ---------------------------------------------------------------------------


def test_fresh_backup_file_skipped(tmp_path: Path) -> None:
    """
    A backup file modified within ttl_days must be classified SKIP_TOO_FRESH.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=14)

    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    active_plist = la_dir / "com.openclaw.venus.plist"
    active_plist.write_text("<?xml ...")
    fresh_backup = la_dir / "com.openclaw.venus.plist.bak"
    fresh_backup.write_text("recent backup")

    with patch(
        "maintenance_worker.rules.launchagent_backup_quarantine._get_launch_agents_dir",
        return_value=la_dir,
    ), patch(
        "maintenance_worker.rules.launchagent_backup_quarantine._get_mtime",
        return_value=_fresh_mtime(),
    ):
        results = enumerate(entry, ctx)

    fresh_skips = [c for c in results if c.verdict == VERDICT_SKIP_FRESH]
    assert len(fresh_skips) == 1, f"Expected 1 SKIP_FRESH; got: {[c.verdict for c in results]}"
    assert fresh_skips[0].path == fresh_backup


# ---------------------------------------------------------------------------
# Test 3: Backup with no active plist → SKIP_NO_ACTIVE_PLIST
# ---------------------------------------------------------------------------


def test_backup_without_active_plist_skipped(tmp_path: Path) -> None:
    """
    A backup file with no corresponding active .plist must be classified
    SKIP_NO_ACTIVE_PLIST — the pre_check requires active plist presence.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=14)

    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    # Only the backup exists, no active plist
    orphan_backup = la_dir / "com.orphan.agent.plist.bak"
    orphan_backup.write_text("orphan backup")

    with patch(
        "maintenance_worker.rules.launchagent_backup_quarantine._get_launch_agents_dir",
        return_value=la_dir,
    ), patch(
        "maintenance_worker.rules.launchagent_backup_quarantine._get_mtime",
        return_value=_old_mtime(),
    ):
        results = enumerate(entry, ctx)

    no_plist_skips = [c for c in results if c.verdict == VERDICT_SKIP_NO_ACTIVE_PLIST]
    assert len(no_plist_skips) == 1, (
        f"Expected 1 SKIP_NO_ACTIVE_PLIST; got: {[c.verdict for c in results]}"
    )
    assert no_plist_skips[0].path == orphan_backup


# ---------------------------------------------------------------------------
# Test 4: apply() always dry_run_only + mock diff present
# ---------------------------------------------------------------------------


def test_apply_always_dry_run_only(tmp_path: Path) -> None:
    """
    apply() must return dry_run_only=True with a non-empty mock diff
    regardless of ctx.dry_run_only value (live_default: false).
    """
    ctx = _make_ctx(tmp_path)

    decision = Candidate(
        task_id="launchagent_backup_quarantine",
        path=Path.home() / "Library" / "LaunchAgents" / "com.openclaw.venus.plist.bak",
        verdict=VERDICT_CANDIDATE,
        reason="stale backup file: age=20.0d > ttl=14d; active plist exists",
        evidence={"name": "com.openclaw.venus.plist.bak", "age_days": 20.0},
    )

    result = apply(decision, ctx)

    assert result.dry_run_only is True
    assert result.task_id == "launchagent_backup_quarantine"
    assert len(result.diff) > 0
    diff_text = " ".join(result.diff)
    assert "com.openclaw.venus.plist.bak" in diff_text
