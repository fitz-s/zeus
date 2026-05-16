# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5 STEP 2
"""
Tests for maintenance_worker.rules.stale_worktree_quarantine.

4 tests:
  1. Idle clean worktree → STALE_QUARANTINE_CANDIDATE
  2. Main/current-branch worktree → SKIP_CURRENT_BRANCH
  3. Worktree with uncommitted changes → SKIP_UNCOMMITTED
  4. apply() always dry_run_only + mock diff
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.stale_worktree_quarantine import (
    VERDICT_SKIP_CURRENT,
    VERDICT_SKIP_OPEN_PR,
    VERDICT_SKIP_PR_CHECK_UNVERIFIED,
    VERDICT_SKIP_UNCOMMITTED,
    VERDICT_STALE,
    _parse_porcelain_output,
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

_PORCELAIN_MAIN = """\
worktree /repo/zeus
HEAD abc123
branch refs/heads/main

worktree /repo/worktree-stale
HEAD def456
branch refs/heads/feat/old-feature

"""

_PORCELAIN_WITH_UNCOMMITTED = """\
worktree /repo/zeus
HEAD abc123
branch refs/heads/main

worktree /repo/worktree-dirty
HEAD fff999
branch refs/heads/feat/dirty-branch

"""


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


def _make_entry(ttl_days: int = 21) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="stale_worktree_quarantine",
        description="PURGE_CATEGORIES.md#category-2-stale-worktrees",
        schedule="daily",
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "stale_worktree_quarantine",
            "schedule": "daily",
            "config": {"worktree_idle_ttl_days": ttl_days},
        },
    )


# ---------------------------------------------------------------------------
# Test 1: Idle clean worktree → STALE_QUARANTINE_CANDIDATE
# ---------------------------------------------------------------------------


def test_idle_clean_worktree_is_stale_candidate(tmp_path: Path) -> None:
    """
    A worktree with no uncommitted changes and last activity > ttl_days ago
    should be classified as STALE_QUARANTINE_CANDIDATE.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=21)

    stale_wt_path = tmp_path / "worktree-stale"
    stale_wt_path.mkdir()

    porcelain = (
        f"worktree {tmp_path}\n"
        f"HEAD abc123\n"
        f"branch refs/heads/main\n"
        f"\n"
        f"worktree {stale_wt_path}\n"
        f"HEAD def456\n"
        f"branch refs/heads/feat/old-feature\n"
        f"\n"
    )

    with patch(
        "maintenance_worker.rules.stale_worktree_quarantine._parse_worktree_list",
        return_value=_parse_porcelain_output(porcelain),
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._get_current_branch",
        return_value="main",
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._has_uncommitted_changes",
        return_value=False,
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._is_worktree_idle",
        return_value=True,
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._open_pr_check_for_branch",
        return_value={"status": "NO_OPEN_PR", "prs": []},
    ):
        results = enumerate(entry, ctx)

    stale = [c for c in results if c.verdict == VERDICT_STALE]
    assert len(stale) == 1, f"Expected 1 STALE candidate; got: {[c.verdict for c in results]}"
    assert stale_wt_path in [c.path for c in stale]


# ---------------------------------------------------------------------------
# Test 2: Current/main worktree → SKIP_CURRENT_BRANCH
# ---------------------------------------------------------------------------


def test_main_worktree_skipped(tmp_path: Path) -> None:
    """
    The main/canonical worktree (first entry in porcelain output) must always
    be classified as SKIP_CURRENT_BRANCH.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry()

    porcelain = (
        f"worktree {tmp_path}\n"
        f"HEAD abc123\n"
        f"branch refs/heads/main\n"
        f"\n"
    )

    with patch(
        "maintenance_worker.rules.stale_worktree_quarantine._parse_worktree_list",
        return_value=_parse_porcelain_output(porcelain),
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._get_current_branch",
        return_value="main",
    ):
        results = enumerate(entry, ctx)

    assert len(results) == 1
    assert results[0].verdict == VERDICT_SKIP_CURRENT
    assert results[0].path == tmp_path


# ---------------------------------------------------------------------------
# Test 3: Worktree with uncommitted changes → SKIP_UNCOMMITTED
# ---------------------------------------------------------------------------


def test_uncommitted_worktree_skipped(tmp_path: Path) -> None:
    """
    A worktree with uncommitted changes must be classified SKIP_UNCOMMITTED
    regardless of idle status — never quarantine dirty worktrees.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry()

    dirty_wt_path = tmp_path / "worktree-dirty"
    dirty_wt_path.mkdir()

    porcelain = (
        f"worktree {tmp_path}\n"
        f"HEAD abc123\n"
        f"branch refs/heads/main\n"
        f"\n"
        f"worktree {dirty_wt_path}\n"
        f"HEAD fff999\n"
        f"branch refs/heads/feat/dirty-branch\n"
        f"\n"
    )

    with patch(
        "maintenance_worker.rules.stale_worktree_quarantine._parse_worktree_list",
        return_value=_parse_porcelain_output(porcelain),
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._get_current_branch",
        return_value="main",
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._has_uncommitted_changes",
        side_effect=lambda p: p == dirty_wt_path,
    ):
        results = enumerate(entry, ctx)

    dirty_results = [c for c in results if c.path == dirty_wt_path]
    assert len(dirty_results) == 1
    assert dirty_results[0].verdict == VERDICT_SKIP_UNCOMMITTED


# ---------------------------------------------------------------------------
# Test 4: apply() always dry_run_only + mock diff
# ---------------------------------------------------------------------------


def test_apply_always_dry_run_with_mock_diff(tmp_path: Path) -> None:
    """
    apply() must return dry_run_only=True with a non-empty diff tuple.
    """
    ctx = _make_ctx(tmp_path)

    fake_candidate = Candidate(
        task_id="stale_worktree_quarantine",
        path=tmp_path / "worktree-stale",
        verdict=VERDICT_STALE,
        reason="Test",
        evidence={"branch": "feat/old-feature"},
    )

    result = apply(fake_candidate, ctx)

    assert isinstance(result, ApplyResult)
    assert result.dry_run_only is True
    assert result.task_id == "stale_worktree_quarantine"
    assert len(result.diff) > 0
    assert any("worktree remove" in line or "dry-run" in line for line in result.diff)


# ---------------------------------------------------------------------------
# Test 5 (M2): gh unavailable → SKIP_PR_CHECK_UNVERIFIED (fail closed)
# ---------------------------------------------------------------------------


def test_open_pr_check_unavailable_fails_closed(tmp_path: Path) -> None:
    """
    M2 amendment per WAVE_1.5_BATCH_A_CRITIC.md:
    When `gh pr list` is unavailable/errors, classify worktree
    SKIP_PR_CHECK_UNVERIFIED — fail closed per TASK_CATALOG.yaml:88-91
    forbidden:any_worktree_whose_branch_appears_in_open_pr.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=21)

    stale_wt_path = tmp_path / "worktree-stale"
    stale_wt_path.mkdir()

    porcelain = (
        f"worktree {tmp_path}\n"
        f"HEAD abc123\n"
        f"branch refs/heads/main\n"
        f"\n"
        f"worktree {stale_wt_path}\n"
        f"HEAD def456\n"
        f"branch refs/heads/feat/old-feature\n"
        f"\n"
    )

    with patch(
        "maintenance_worker.rules.stale_worktree_quarantine._parse_worktree_list",
        return_value=_parse_porcelain_output(porcelain),
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._get_current_branch",
        return_value="main",
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._has_uncommitted_changes",
        return_value=False,
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._open_pr_check_for_branch",
        return_value={"status": "UNVERIFIED_FAIL_CLOSED", "prs": []},
    ):
        results = enumerate(entry, ctx)

    fail_closed = [c for c in results if c.verdict == VERDICT_SKIP_PR_CHECK_UNVERIFIED]
    assert len(fail_closed) == 1, f"Expected 1 SKIP_PR_CHECK_UNVERIFIED; got: {[c.verdict for c in results]}"
    assert fail_closed[0].evidence.get("check_6_pr_status") == "UNVERIFIED_FAIL_CLOSED"


# ---------------------------------------------------------------------------
# Test 6 (M2): branch has open PR → SKIP_OPEN_PR (stay)
# ---------------------------------------------------------------------------


def test_open_pr_found_skips_worktree(tmp_path: Path) -> None:
    """
    M2 amendment: when gh confirms the worktree's branch appears in an open
    PR, classify SKIP_OPEN_PR (catalog forbidden clause honored).
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=21)

    pr_wt_path = tmp_path / "worktree-pr"
    pr_wt_path.mkdir()

    porcelain = (
        f"worktree {tmp_path}\n"
        f"HEAD abc123\n"
        f"branch refs/heads/main\n"
        f"\n"
        f"worktree {pr_wt_path}\n"
        f"HEAD def456\n"
        f"branch refs/heads/feat/pr-feature\n"
        f"\n"
    )

    with patch(
        "maintenance_worker.rules.stale_worktree_quarantine._parse_worktree_list",
        return_value=_parse_porcelain_output(porcelain),
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._get_current_branch",
        return_value="main",
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._has_uncommitted_changes",
        return_value=False,
    ), patch(
        "maintenance_worker.rules.stale_worktree_quarantine._open_pr_check_for_branch",
        return_value={"status": "OPEN_PR_FOUND", "prs": [{"number": 42, "title": "feat: pr-feature"}]},
    ):
        results = enumerate(entry, ctx)

    skipped = [c for c in results if c.verdict == VERDICT_SKIP_OPEN_PR]
    assert len(skipped) == 1, f"Expected 1 SKIP_OPEN_PR; got: {[c.verdict for c in results]}"
    assert "open PR" in skipped[0].reason
    assert skipped[0].evidence.get("check_6_prs")
    assert skipped[0].evidence["check_6_prs"][0]["number"] == 42
