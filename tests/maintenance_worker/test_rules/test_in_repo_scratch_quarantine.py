# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5 STEP 2
"""
Tests for maintenance_worker.rules.in_repo_scratch_quarantine.

4 tests:
  1. Stale scratch file matching pattern → SCRATCH_QUARANTINE_CANDIDATE
  2. File inside src/ → SKIP_FORBIDDEN_PATH (forbidden dir)
  3. Fresh scratch file (< ttl_days) → SKIP_TOO_FRESH
  4. apply() always returns dry_run_only=True + mock diff
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from maintenance_worker.rules.in_repo_scratch_quarantine import (
    VERDICT_CANDIDATE,
    VERDICT_SKIP_FORBIDDEN,
    VERDICT_SKIP_FRESH,
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
        run_id="test-run-id",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
    )


def _make_entry(ttl_days: int = 7) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="in_repo_scratch_quarantine",
        description="PURGE_CATEGORIES.md#category-3-in-repo-scratch-directories",
        schedule="daily",
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "in_repo_scratch_quarantine",
            "schedule": "daily",
            "config": {
                "scratch_ttl_days": ttl_days,
                "scratch_patterns": ["tmp", "tmp/*", "scratch", "debug_*", "*.tmp"],
                "quarantine_dir": ".archive/scratch",
            },
            "safety": {
                "forbidden_paths": ["src/**", "tests/**", "architecture/**",
                                    "docs/**", "state/**", "config/**", "scripts/**"],
            },
        },
    )


def _set_old_mtime(path: Path, age_days: float = 10) -> None:
    """Set file mtime to `age_days` days in the past."""
    old_ts = time.time() - age_days * 86400
    os.utime(path, (old_ts, old_ts))


# ---------------------------------------------------------------------------
# Test 1: Stale scratch file → SCRATCH_QUARANTINE_CANDIDATE
# ---------------------------------------------------------------------------


def test_stale_scratch_file_is_candidate(tmp_path: Path) -> None:
    """
    A file matching scratch_patterns older than ttl_days → SCRATCH_QUARANTINE_CANDIDATE.
    """
    scratch_file = tmp_path / "debug_output.tmp"
    scratch_file.write_text("debug stuff", encoding="utf-8")
    _set_old_mtime(scratch_file, age_days=10)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=7)

    results = enumerate(entry, ctx)

    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE]
    assert any(c.path == scratch_file for c in candidates), (
        f"Stale debug_output.tmp should be CANDIDATE; got: {[c.verdict for c in results]}"
    )


def test_stale_tmp_dir_is_candidate(tmp_path: Path) -> None:
    """
    A 'tmp' directory older than ttl_days → SCRATCH_QUARANTINE_CANDIDATE.
    """
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    tmp_file = tmp_dir / "old_output.txt"
    tmp_file.write_text("old data", encoding="utf-8")
    _set_old_mtime(tmp_file, age_days=15)
    _set_old_mtime(tmp_dir, age_days=15)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=7)

    results = enumerate(entry, ctx)

    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE]
    assert any(c.path.name == "tmp" or "tmp" in str(c.path) for c in candidates), (
        f"Stale tmp dir should produce CANDIDATE(s); got: {[(c.path.name, c.verdict) for c in results]}"
    )


# ---------------------------------------------------------------------------
# Test 2: File in src/ → forbidden (SKIP_FORBIDDEN_PATH or silently skipped)
# ---------------------------------------------------------------------------


def test_src_dir_is_forbidden(tmp_path: Path) -> None:
    """
    The src/ directory itself matches forbidden_paths and must never be a candidate.
    Files inside src/ that match scratch patterns are skipped entirely.
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    # Create a file inside src/ that would match *.tmp pattern
    scratch_in_src = src_dir / "something.tmp"
    scratch_in_src.write_text("source code", encoding="utf-8")
    _set_old_mtime(scratch_in_src, age_days=20)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=7)

    results = enumerate(entry, ctx)

    # src/ itself and its contents must NOT appear as CANDIDATE
    candidate_paths = [c.path for c in results if c.verdict == VERDICT_CANDIDATE]
    assert src_dir not in candidate_paths, "src/ dir must not be a quarantine candidate"
    for cp in candidate_paths:
        assert not str(cp).startswith(str(src_dir)), (
            f"No file under src/ should be a candidate; found: {cp}"
        )


# ---------------------------------------------------------------------------
# Test 3: Fresh scratch file → SKIP_TOO_FRESH
# ---------------------------------------------------------------------------


def test_fresh_scratch_file_is_skipped(tmp_path: Path) -> None:
    """
    A scratch file modified within ttl_days must be SKIP_TOO_FRESH.
    """
    fresh_scratch = tmp_path / "debug_fresh.tmp"
    fresh_scratch.write_text("fresh debug data", encoding="utf-8")
    # Set mtime to 2 days ago (< 7d threshold)
    _set_old_mtime(fresh_scratch, age_days=2)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=7)

    results = enumerate(entry, ctx)

    fresh_results = [c for c in results if c.path == fresh_scratch]
    assert len(fresh_results) == 1
    assert fresh_results[0].verdict == VERDICT_SKIP_FRESH, (
        f"Fresh file should be SKIP_TOO_FRESH; got {fresh_results[0].verdict!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: apply() always dry_run_only + mock diff
# ---------------------------------------------------------------------------


def test_apply_always_dry_run_with_mock_diff(tmp_path: Path) -> None:
    """
    apply() must return dry_run_only=True with a non-empty diff tuple.
    """
    ctx = _make_ctx(tmp_path)

    fake_candidate = Candidate(
        task_id="in_repo_scratch_quarantine",
        path=tmp_path / "debug_output.tmp",
        verdict=VERDICT_CANDIDATE,
        reason="Test stale scratch",
    )

    result = apply(fake_candidate, ctx)

    assert isinstance(result, ApplyResult)
    assert result.dry_run_only is True
    assert result.task_id == "in_repo_scratch_quarantine"
    assert len(result.diff) > 0
    assert any("mv" in line or "dry-run" in line for line in result.diff)
