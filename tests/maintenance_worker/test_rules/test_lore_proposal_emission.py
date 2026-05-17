# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §lore_proposal_emission
"""
Tests for maintenance_worker.rules.lore_proposal_emission.

5 tests:
  1. Packet with unreviewed ## Lessons section older than TTL → LORE_PROPOSAL_CANDIDATE
  2. Packet with REVIEWED: marker → SKIP_ALREADY_REVIEWED
  3. Packet too fresh → SKIP_TOO_FRESH
  4. apply() always dry_run_only=True, returns mock diff (no writes)
  5. Stale lore topic entry without REVIEWED marker → LORE_STALE_REVIEW
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.lore_proposal_emission import (
    VERDICT_PROPOSAL,
    VERDICT_SKIP_FRESH,
    VERDICT_SKIP_NO_LESSONS,
    VERDICT_SKIP_REVIEWED,
    VERDICT_STALE_REVIEW,
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
        run_id="test-run-c1",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
        dry_run_only=False,
    )


def _make_entry(ttl_days: int = 7) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="lore_proposal_emission",
        description="surface-lore-extraction-candidates",
        schedule="daily",
        dry_run_floor_exempt=False,
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "lore_proposal_emission",
            "schema_version": 1,
            "schedule": "daily",
            "live_default": False,
            "dry_run_floor_exempt": False,
            "config": {
                "lore_review_ttl_days": ttl_days,
                "lore_topic_dirs": [],
                "proposals_dir": "lore_proposals",
            },
            "safety": {
                "no_file_mutations": True,
                "output_only_dir": "lore_proposals",
            },
        },
    )


def _old_mtime(days: int = 20) -> float:
    """Return mtime N days in the past."""
    return time.time() - (days * 86400)


def _make_packet_with_lessons(ops_dir: Path, name: str, reviewed: bool = False) -> Path:
    """Create a task_* packet dir containing PLAN.md with a ## Lessons section."""
    packet = ops_dir / name
    packet.mkdir(parents=True, exist_ok=True)
    lessons_text = "# Plan\n\nSome content.\n\n## Lessons\n\n- thing learned\n"
    if reviewed:
        lessons_text += "\nREVIEWED: 2026-05-10\n"
    (packet / "PLAN.md").write_text(lessons_text)
    return packet


# ---------------------------------------------------------------------------
# Test 1: Packet with unreviewed ## Lessons older than TTL → LORE_PROPOSAL_CANDIDATE
# ---------------------------------------------------------------------------


def test_old_unreviewed_packet_is_proposal(tmp_path: Path) -> None:
    """
    A task_* packet with a ## Lessons heading and mtime > ttl_days must surface
    as LORE_PROPOSAL_CANDIDATE.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=7)

    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    packet = _make_packet_with_lessons(ops_dir, "task_2026-01-01_old_feature")

    old_ts = _old_mtime(20)

    with patch(
        "maintenance_worker.rules.lore_proposal_emission._get_mtime",
        return_value=old_ts,
    ):
        results = enumerate(entry, ctx)

    proposals = [c for c in results if c.verdict == VERDICT_PROPOSAL]
    assert len(proposals) >= 1, f"Expected ≥1 LORE_PROPOSAL_CANDIDATE; got: {[c.verdict for c in results]}"
    assert any("old_feature" in str(c.path) for c in proposals)


# ---------------------------------------------------------------------------
# Test 2: Packet with REVIEWED marker → SKIP_ALREADY_REVIEWED
# ---------------------------------------------------------------------------


def test_reviewed_packet_is_skipped(tmp_path: Path) -> None:
    """
    A packet whose lessons file already has a REVIEWED: marker must be
    classified SKIP_ALREADY_REVIEWED even if it is old.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=7)

    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    _make_packet_with_lessons(ops_dir, "task_2026-01-02_reviewed_feature", reviewed=True)

    old_ts = _old_mtime(20)

    with patch(
        "maintenance_worker.rules.lore_proposal_emission._get_mtime",
        return_value=old_ts,
    ):
        results = enumerate(entry, ctx)

    reviewed = [c for c in results if c.verdict == VERDICT_SKIP_REVIEWED]
    assert len(reviewed) >= 1, f"Expected ≥1 SKIP_ALREADY_REVIEWED; got: {[c.verdict for c in results]}"
    assert any("reviewed_feature" in str(c.path) for c in reviewed)


# ---------------------------------------------------------------------------
# Test 3: Packet too fresh → SKIP_TOO_FRESH
# ---------------------------------------------------------------------------


def test_fresh_packet_is_skipped(tmp_path: Path) -> None:
    """
    A packet with a ## Lessons section but mtime within ttl_days must be
    classified SKIP_TOO_FRESH.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=7)

    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    _make_packet_with_lessons(ops_dir, "task_2026-05-15_fresh_feature")

    fresh_ts = time.time() - (1 * 86400)  # 1 day ago (< 7 day TTL)

    with patch(
        "maintenance_worker.rules.lore_proposal_emission._get_mtime",
        return_value=fresh_ts,
    ):
        results = enumerate(entry, ctx)

    fresh = [c for c in results if c.verdict == VERDICT_SKIP_FRESH]
    assert len(fresh) >= 1, f"Expected ≥1 SKIP_TOO_FRESH; got: {[c.verdict for c in results]}"
    assert any("fresh_feature" in str(c.path) for c in fresh)


# ---------------------------------------------------------------------------
# Test 4: apply() is ALWAYS dry_run_only=True (no writes ever)
# ---------------------------------------------------------------------------


def test_apply_is_always_dry_run(tmp_path: Path) -> None:
    """
    apply() must return dry_run_only=True regardless of ctx.dry_run_only,
    because lore emission always requires human review.
    """
    ctx_live = _make_ctx(tmp_path)
    ctx_dryrun = TickContext(
        run_id="test-dryrun",
        started_at=datetime.now(tz=timezone.utc),
        config=ctx_live.config,
        invocation_mode="MANUAL",
        dry_run_only=True,
    )

    candidate = Candidate(
        task_id="lore_proposal_emission",
        path=tmp_path / "docs" / "operations" / "task_2026-01-01_test" / "PLAN.md",
        verdict=VERDICT_PROPOSAL,
        reason="test candidate",
        evidence={"packet": "task_2026-01-01_test"},
    )

    for ctx in (ctx_live, ctx_dryrun):
        result = apply(candidate, ctx)
        assert result.dry_run_only is True, (
            f"apply() must always return dry_run_only=True; got {result.dry_run_only} "
            f"(ctx.dry_run_only={ctx.dry_run_only})"
        )
        assert result.diff is not None and len(result.diff) > 0, (
            "apply() must return non-empty mock diff"
        )
        # No files must have been created
        proposals_dir = tmp_path / "lore_proposals"
        assert not proposals_dir.exists() or list(proposals_dir.iterdir()) == [], (
            "apply() must not write any files"
        )


# ---------------------------------------------------------------------------
# Test 5: Stale lore topic entry → LORE_STALE_REVIEW
# ---------------------------------------------------------------------------


def test_stale_lore_topic_entry_surfaces(tmp_path: Path) -> None:
    """
    A .md file in docs/lore/<topic>/ older than ttl_days without a REVIEWED
    marker must surface as LORE_STALE_REVIEW.
    """
    ctx = _make_ctx(tmp_path)
    spec = TaskSpec(
        task_id="lore_proposal_emission",
        description="surface-lore-extraction-candidates",
        schedule="daily",
        dry_run_floor_exempt=False,
    )
    entry = TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "lore_proposal_emission",
            "schema_version": 1,
            "schedule": "daily",
            "live_default": False,
            "dry_run_floor_exempt": False,
            "config": {
                "lore_review_ttl_days": 7,
                "lore_topic_dirs": ["runtime"],
                "proposals_dir": "lore_proposals",
            },
            "safety": {},
        },
    )

    # Create a stale unreviewed lore entry
    lore_dir = tmp_path / "docs" / "lore" / "runtime"
    lore_dir.mkdir(parents=True)
    lore_file = lore_dir / "2026-01-10_timing_quirk.md"
    lore_file.write_text("# Timing Quirk\n\nSome runtime lore content.\n")

    old_ts = _old_mtime(20)

    with patch(
        "maintenance_worker.rules.lore_proposal_emission._get_mtime",
        return_value=old_ts,
    ):
        results = enumerate(entry, ctx)

    stale = [c for c in results if c.verdict == VERDICT_STALE_REVIEW]
    assert len(stale) >= 1, f"Expected ≥1 LORE_STALE_REVIEW; got: {[c.verdict for c in results]}"
    assert any("timing_quirk" in str(c.path) for c in stale)
