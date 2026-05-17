# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §agent_self_evidence_archival
"""
Tests for maintenance_worker.rules.agent_self_evidence_archival.

6 tests:
  1. Old evidence dir entry → ARCHIVAL_CANDIDATE
  2. Current tick dir (today's date) → SKIP_CURRENT_TICK_DIR
  3. Symlink escape outside evidence_dir → SKIP_PATH_ESCAPE
  4. apply() dry_run_only=True → mock diff, no move
  5. apply() live mode → actual shutil.move to cold_archive_dir
  6. Fresh evidence entry → SKIP_TOO_FRESH
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.agent_self_evidence_archival import (
    VERDICT_CANDIDATE,
    VERDICT_SKIP_CURRENT_TICK,
    VERDICT_SKIP_FRESH,
    VERDICT_SKIP_PATH_ESCAPE,
    VERDICT_SKIP_SQLITE,
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


def _make_ctx(tmp_path: Path, dry_run_only: bool = False) -> TickContext:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)

    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=state_dir,
        evidence_dir=evidence_dir,
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=True,
        scheduler="launchd",
        notification_channel="discord",
    )
    return TickContext(
        run_id="test-run-c3",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
        dry_run_only=dry_run_only,
    )


def _make_entry(retention_days: int = 90) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="agent_self_evidence_archival",
        description="archive-old-evidence-trail",
        schedule="daily",
        dry_run_floor_exempt=True,
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "agent_self_evidence_archival",
            "schema_version": 1,
            "schedule": "daily",
            "live_default": True,
            "dry_run_floor_exempt": True,
            "config": {
                "evidence_retention_days": retention_days,
                "cold_archive_dir": "state/evidence_cold",
            },
            "safety": {
                "target_only": "evidence/",
                "forbidden": ["any_path_outside_evidence_dir", "current_tick_evidence_dir"],
            },
        },
    )


def _old_mtime(days: int = 100) -> float:
    """Return mtime N days in the past."""
    return time.time() - (days * 86400)


# ---------------------------------------------------------------------------
# Test 1: Old evidence dir entry → ARCHIVAL_CANDIDATE
# ---------------------------------------------------------------------------


def test_old_evidence_dir_is_candidate(tmp_path: Path) -> None:
    """
    An evidence subdirectory older than retention_days must surface as
    ARCHIVAL_CANDIDATE.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(retention_days=90)

    evidence_dir = ctx.config.evidence_dir
    old_dir = evidence_dir / "2025-11-01"
    old_dir.mkdir()

    old_ts = _old_mtime(100)

    with patch("maintenance_worker.rules.agent_self_evidence_archival.time.time", return_value=time.time()), \
         patch.object(Path, "stat") as mock_stat:
        import os as _os
        mock_stat.return_value = _os.stat_result((
            0o040755, 0, 0, 1, 0, 0, 0,
            old_ts, old_ts, old_ts,
        ))
        results = enumerate(entry, ctx)

    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE]
    assert len(candidates) >= 1, f"Expected ≥1 ARCHIVAL_CANDIDATE; got: {[c.verdict for c in results]}"
    assert any("2025-11-01" in str(c.path) for c in candidates)


# ---------------------------------------------------------------------------
# Test 2: Current tick dir → SKIP_CURRENT_TICK_DIR
# ---------------------------------------------------------------------------


def test_current_tick_dir_is_skipped(tmp_path: Path) -> None:
    """
    Today's evidence directory must be classified SKIP_CURRENT_TICK_DIR.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(retention_days=90)

    evidence_dir = ctx.config.evidence_dir
    today_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    today_dir = evidence_dir / today_str
    today_dir.mkdir()

    results = enumerate(entry, ctx)

    current_tick_skips = [c for c in results if c.verdict == VERDICT_SKIP_CURRENT_TICK]
    assert len(current_tick_skips) >= 1, (
        f"Expected ≥1 SKIP_CURRENT_TICK_DIR for {today_str}; got: {[c.verdict for c in results]}"
    )
    assert any(today_str in str(c.path) for c in current_tick_skips)


# ---------------------------------------------------------------------------
# Test 3: Symlink pointing outside evidence_dir → SKIP_PATH_ESCAPE
# ---------------------------------------------------------------------------


def test_symlink_escape_is_skipped(tmp_path: Path) -> None:
    """
    A symlink inside evidence_dir pointing outside it must be classified
    SKIP_PATH_ESCAPE (path containment defense).
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(retention_days=90)

    evidence_dir = ctx.config.evidence_dir
    # Create a target outside evidence_dir
    outside = tmp_path / "outside_target"
    outside.mkdir()

    # Create a symlink inside evidence_dir pointing outside
    escape_link = evidence_dir / "escape_link"
    escape_link.symlink_to(outside)

    results = enumerate(entry, ctx)

    escape_skips = [c for c in results if c.verdict == VERDICT_SKIP_PATH_ESCAPE]
    assert len(escape_skips) >= 1, (
        f"Expected ≥1 SKIP_PATH_ESCAPE for symlink escape; got: {[c.verdict for c in results]}"
    )
    assert any("escape_link" in str(c.path) for c in escape_skips)


# ---------------------------------------------------------------------------
# Test 4: apply() dry_run_only=True → mock diff, no move
# ---------------------------------------------------------------------------


def test_apply_dry_run_returns_mock_diff(tmp_path: Path) -> None:
    """
    When ctx.dry_run_only=True, apply() must return a mock diff and must
    not move any files.
    """
    ctx = _make_ctx(tmp_path, dry_run_only=True)

    evidence_dir = ctx.config.evidence_dir
    old_dir = evidence_dir / "2025-10-01"
    old_dir.mkdir()

    candidate = Candidate(
        task_id="agent_self_evidence_archival",
        path=old_dir,
        verdict=VERDICT_CANDIDATE,
        reason="test candidate",
        evidence={"age_days": 100.0, "ttl_days": 90},
    )

    result = apply(candidate, ctx)

    assert result.dry_run_only is True
    assert result.diff is not None and len(result.diff) > 0
    # The source path must still exist (not moved)
    assert old_dir.exists(), "apply() dry_run must not move the directory"
    cold_dir = ctx.config.state_dir / "evidence_cold"
    assert not cold_dir.exists() or not (cold_dir / "2025-10-01").exists(), (
        "apply() dry_run must not create cold archive entry"
    )


# ---------------------------------------------------------------------------
# Test 5: apply() live mode → actual move to cold_archive_dir
# ---------------------------------------------------------------------------


def test_apply_live_mode_moves_directory(tmp_path: Path) -> None:
    """
    In live mode (dry_run_only=False, verdict=ARCHIVAL_CANDIDATE), apply()
    must move the evidence entry to state/evidence_cold/.
    """
    ctx = _make_ctx(tmp_path, dry_run_only=False)

    evidence_dir = ctx.config.evidence_dir
    old_dir = evidence_dir / "2025-09-01"
    old_dir.mkdir()
    (old_dir / "tick_summary.json").write_text('{"run_id": "abc"}')

    candidate = Candidate(
        task_id="agent_self_evidence_archival",
        path=old_dir,
        verdict=VERDICT_CANDIDATE,
        reason="100 days old",
        evidence={"age_days": 100.0, "ttl_days": 90},
    )

    result = apply(candidate, ctx)

    assert result.dry_run_only is False, f"Expected live result; got dry_run_only={result.dry_run_only}"
    assert len(result.moved) == 1, f"Expected 1 moved entry; got {result.moved}"
    src, dest = result.moved[0]
    assert src == old_dir
    assert dest.exists(), f"Destination {dest} must exist after move"
    assert not old_dir.exists(), f"Source {old_dir} must be gone after move"
    assert (dest / "tick_summary.json").exists(), "Contents must have moved with directory"


# ---------------------------------------------------------------------------
# Test 6: Fresh evidence entry → SKIP_TOO_FRESH
# ---------------------------------------------------------------------------


def test_fresh_evidence_entry_skipped(tmp_path: Path) -> None:
    """
    An evidence entry whose mtime is within retention_days must be
    classified SKIP_TOO_FRESH.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(retention_days=90)

    evidence_dir = ctx.config.evidence_dir
    # Create a date dir that is only 10 days old
    recent_dir = evidence_dir / "2026-05-06"
    recent_dir.mkdir()

    fresh_ts = time.time() - (10 * 86400)  # 10 days ago < 90 day retention

    with patch.object(Path, "stat") as mock_stat:
        import os as _os
        mock_stat.return_value = _os.stat_result((
            0o040755, 0, 0, 1, 0, 0, 0,
            fresh_ts, fresh_ts, fresh_ts,
        ))
        results = enumerate(entry, ctx)

    fresh = [c for c in results if c.verdict == VERDICT_SKIP_FRESH]
    assert len(fresh) >= 1, f"Expected ≥1 SKIP_TOO_FRESH; got: {[c.verdict for c in results]}"
    assert any("2026-05-06" in str(c.path) for c in fresh)
