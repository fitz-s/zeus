# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/TASK_CATALOG.yaml
#   §authority_drift_surface
"""
Tests for maintenance_worker.rules.authority_drift_surface.

4 tests:
  1. Doc significantly older than code sibling → DRIFT_SURFACE_CANDIDATE
  2. Drift score >= escalate_threshold → DRIFT_ESCALATE
  3. apply() always dry_run_only=True (no writes)
  4. Both doc and code recently updated → SKIP_TOO_FRESH
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.authority_drift_surface import (
    VERDICT_CANDIDATE,
    VERDICT_ESCALATE,
    VERDICT_SKIP_BELOW,
    VERDICT_SKIP_FRESH,
    VERDICT_SKIP_NO_SIBLING,
    apply,
    enumerate,
    _compute_drift_score,
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
        run_id="test-run-c2",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
        dry_run_only=False,
    )


def _make_entry(drift_threshold: float = 0.3, escalate_threshold: float = 0.7) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="authority_drift_surface",
        description="surface-authority-code-drift",
        schedule="weekly",
        dry_run_floor_exempt=False,
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "authority_drift_surface",
            "schema_version": 1,
            "schedule": "weekly",
            "live_default": False,
            "dry_run_floor_exempt": False,
            "config": {
                "drift_score_threshold": drift_threshold,
                "escalate_threshold": escalate_threshold,
            },
            "safety": {
                "no_authority_doc_edits": True,
                "output_only_dir": "evidence/drift_surface",
            },
        },
    )


# ---------------------------------------------------------------------------
# Test 1: Doc with stale code sibling → DRIFT_SURFACE_CANDIDATE
# ---------------------------------------------------------------------------


def test_stale_code_sibling_surfaces_as_candidate(tmp_path: Path) -> None:
    """
    When an authority doc has a code sibling that is much older, and the
    drift score exceeds the threshold, it should surface as DRIFT_SURFACE_CANDIDATE.

    We mock _find_code_sibling and _get_mtime to control timestamps precisely.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(drift_threshold=0.3, escalate_threshold=0.7)

    # Create authority doc in architecture/
    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir(parents=True)
    doc_file = arch_dir / "executor.md"
    doc_file.write_text("# Executor Architecture\n\nDesign doc.\n")

    # Create a code sibling
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    code_file = src_dir / "executor.py"
    code_file.write_text("# executor module\n")

    now = time.time()
    # doc updated recently (25 days ago), code is stale (40 days ago)
    # gap=15d, stale_s=30d → base=0.5, code_age=40d>30d → stale_bonus=0.1 → score=0.6
    # 0.3 <= 0.6 < 0.7 → DRIFT_SURFACE_CANDIDATE (not ESCALATE)
    doc_mtime = now - (25 * 86400)
    code_mtime = now - (40 * 86400)

    with patch(
        "maintenance_worker.rules.authority_drift_surface._get_mtime",
        side_effect=lambda p: doc_mtime if p == doc_file else code_mtime,
    ), patch(
        "maintenance_worker.rules.authority_drift_surface._find_code_sibling",
        return_value=(code_file, code_mtime),
    ):
        results = enumerate(entry, ctx)

    # Should have at least one candidate for executor.md
    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE and c.path == doc_file]
    assert len(candidates) >= 1, (
        f"Expected DRIFT_SURFACE_CANDIDATE for executor.md; got: {[(c.path.name, c.verdict) for c in results]}"
    )
    assert candidates[0].evidence["drift_score"] >= 0.3


# ---------------------------------------------------------------------------
# Test 2: Very high drift score → DRIFT_ESCALATE
# ---------------------------------------------------------------------------


def test_high_drift_score_escalates(tmp_path: Path) -> None:
    """
    When drift_score >= escalate_threshold, verdict must be DRIFT_ESCALATE.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(drift_threshold=0.3, escalate_threshold=0.7)

    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir(parents=True)
    doc_file = arch_dir / "evaluator.md"
    doc_file.write_text("# Evaluator Design\n")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    code_file = src_dir / "evaluator.py"
    code_file.write_text("# evaluator\n")

    now = time.time()
    # doc is very recent, code is ancient — maximum drift
    doc_mtime = now - (1 * 86400)
    code_mtime = now - (120 * 86400)  # 120 days old, stale_seconds=30d → score > 0.7

    with patch(
        "maintenance_worker.rules.authority_drift_surface._get_mtime",
        side_effect=lambda p: doc_mtime if p == doc_file else code_mtime,
    ), patch(
        "maintenance_worker.rules.authority_drift_surface._find_code_sibling",
        return_value=(code_file, code_mtime),
    ):
        results = enumerate(entry, ctx)

    escalated = [c for c in results if c.verdict == VERDICT_ESCALATE and c.path == doc_file]
    assert len(escalated) >= 1, (
        f"Expected DRIFT_ESCALATE for evaluator.md; got: {[(c.path.name, c.verdict) for c in results]}"
    )


# ---------------------------------------------------------------------------
# Test 3: apply() is always dry_run_only=True
# ---------------------------------------------------------------------------


def test_apply_is_always_dry_run(tmp_path: Path) -> None:
    """
    apply() must return dry_run_only=True regardless of ctx settings.
    No files should be written.
    """
    ctx = _make_ctx(tmp_path)

    candidate = Candidate(
        task_id="authority_drift_surface",
        path=tmp_path / "architecture" / "executor.md",
        verdict=VERDICT_CANDIDATE,
        reason="test drift candidate",
        evidence={"drift_score": 0.5},
    )

    # Test with both dry_run=False and dry_run=True contexts
    for dry_run in (False, True):
        test_ctx = TickContext(
            run_id="test-apply",
            started_at=datetime.now(tz=timezone.utc),
            config=ctx.config,
            invocation_mode="MANUAL",
            dry_run_only=dry_run,
        )
        result = apply(candidate, test_ctx)
        assert result.dry_run_only is True, (
            f"apply() must always return dry_run_only=True; got {result.dry_run_only}"
        )
        assert result.diff is not None and len(result.diff) > 0, (
            "apply() must return non-empty mock diff"
        )
        # Verify no files written
        drift_dir = tmp_path / "evidence" / "drift_surface"
        assert not drift_dir.exists() or list(drift_dir.iterdir()) == [], (
            "apply() must not write any files"
        )


# ---------------------------------------------------------------------------
# Test 4: Both doc and code recently updated → SKIP_TOO_FRESH
# ---------------------------------------------------------------------------


def test_fresh_doc_and_code_skipped(tmp_path: Path) -> None:
    """
    When both the authority doc and the code sibling are recently updated
    (< DEFAULT_STALE_DAYS), the entry should be SKIP_TOO_FRESH.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(drift_threshold=0.3)

    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir(parents=True)
    doc_file = arch_dir / "harvester.md"
    doc_file.write_text("# Harvester Design\n")

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    code_file = src_dir / "harvester.py"
    code_file.write_text("# harvester\n")

    now = time.time()
    # Both updated 3 days ago — well within stale_days=30
    fresh_mtime = now - (3 * 86400)

    with patch(
        "maintenance_worker.rules.authority_drift_surface._get_mtime",
        return_value=fresh_mtime,
    ), patch(
        "maintenance_worker.rules.authority_drift_surface._find_code_sibling",
        return_value=(code_file, fresh_mtime),
    ):
        results = enumerate(entry, ctx)

    fresh = [c for c in results if c.verdict == VERDICT_SKIP_FRESH and c.path == doc_file]
    assert len(fresh) >= 1, (
        f"Expected SKIP_TOO_FRESH for harvester.md; got: {[(c.path.name, c.verdict) for c in results]}"
    )


# ---------------------------------------------------------------------------
# Test 5: _compute_drift_score unit test
# ---------------------------------------------------------------------------


def test_compute_drift_score_boundaries() -> None:
    """
    _compute_drift_score must:
    - Return 0.0 when doc and code have same mtime
    - Return > 0.3 when gap is > 30% of stale_seconds
    - Never exceed 1.0
    """
    now = time.time()
    stale_s = 30 * 86400  # 30 days

    # Same mtime → score = 0 (no stale bonus since code_age ~ 0)
    score_same = _compute_drift_score(now, now, now, stale_s)
    assert score_same == 0.0, f"Same mtime should give score 0.0, got {score_same}"

    # 15-day gap → base = 0.5, code_age 0 → no bonus → 0.5
    doc_mtime = now - (5 * 86400)
    code_mtime = now - (20 * 86400)
    score_mid = _compute_drift_score(doc_mtime, code_mtime, now, stale_s)
    assert 0.4 < score_mid <= 1.0, f"15-day gap should give score ~0.5, got {score_mid}"

    # Cap at 1.0
    ancient = now - (365 * 86400)
    score_max = _compute_drift_score(now, ancient, now, stale_s)
    assert score_max == 1.0, f"Max score must be 1.0, got {score_max}"
