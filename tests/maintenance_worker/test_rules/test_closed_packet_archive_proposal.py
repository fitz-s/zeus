# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5 STEP 2
"""
Tests for maintenance_worker.rules.closed_packet_archive_proposal.

4 tests per handler plan:
  1. happy path: stale packet (>60d, no load-bearing signals) → ARCHIVE_CANDIDATE
  2. load-bearing path: registry hit → LOAD_BEARING_DESPITE_AGE
  3. wave family atomic: mixed verdicts → all stay LOAD_BEARING
  4. dry-run path: apply() always returns dry_run_only=True + mock diff
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.closed_packet_archive_proposal import (
    VERDICT_ARCHIVABLE,
    VERDICT_LOAD_BEARING,
    enumerate,
    apply,
    _open_pr_check,
)
from maintenance_worker.rules.parser import TaskCatalogEntry
from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import EngineConfig, TaskSpec, TickContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, live_default: bool = False) -> TickContext:
    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=live_default,
        scheduler="launchd",
        notification_channel="discord",
    )
    return TickContext(
        run_id="test-run-id",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
    )


def _make_entry(ttl_days: int = 60) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="closed_packet_archive_proposal",
        description="ARCHIVAL_RULES.md",
        schedule="daily",
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "closed_packet_archive_proposal",
            "schedule": "daily",
            "config": {"packet_archive_ttl_days": ttl_days},
        },
    )


def _make_stale_packet(ops_dir: Path, name: str, age_days: float = 70) -> Path:
    """Create a stale packet directory with a PLAN.md file."""
    packet = ops_dir / name
    packet.mkdir(parents=True)
    plan = packet / "PLAN.md"
    plan.write_text("# Test packet\n\nStatus: CLOSED\n", encoding="utf-8")
    # Set mtime to `age_days` days in the past
    old_ts = time.time() - age_days * 86400
    import os
    os.utime(plan, (old_ts, old_ts))
    os.utime(packet, (old_ts, old_ts))
    return packet


# ---------------------------------------------------------------------------
# Test 1: Happy path — stale packet → ARCHIVE_CANDIDATE
# ---------------------------------------------------------------------------


def test_stale_packet_classified_archivable(tmp_path: Path) -> None:
    """
    A packet older than 60 days with no load-bearing signals → ARCHIVE_CANDIDATE.
    All 9 exemption checks must pass (registry absent → WARN+continue,
    checks 1-8 heuristic all pass on a clean packet).
    """
    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    _make_stale_packet(ops_dir, "task_2025-01-01_old_closed_packet", age_days=90)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=60)

    # Patch subprocess calls to return no references; gh returns no PRs (check #6 passes)
    with patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._open_pr_check",
        return_value={"status": "PASSED_NO_PRS", "prs": []},
    ):
        results = enumerate(entry, ctx)

    archivable = [c for c in results if c.verdict == VERDICT_ARCHIVABLE]
    assert len(archivable) == 1, f"Expected 1 ARCHIVE_CANDIDATE, got: {[c.verdict for c in results]}"
    assert archivable[0].path.name == "task_2025-01-01_old_closed_packet"
    assert archivable[0].task_id == "closed_packet_archive_proposal"
    assert "checks_passed" in archivable[0].evidence


# ---------------------------------------------------------------------------
# Test 2: Load-bearing path — registry hit → LOAD_BEARING
# ---------------------------------------------------------------------------


def test_registry_hit_classified_load_bearing(tmp_path: Path) -> None:
    """
    A packet found in artifact_authority_status.yaml with a non-archivable
    status → LOAD_BEARING_DESPITE_AGE immediately (Check #0).
    """
    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    packet = _make_stale_packet(ops_dir, "task_2025-02-01_load_bearing_packet", age_days=90)

    # Create registry with LOAD_BEARING entry
    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir()
    registry = arch_dir / "artifact_authority_status.yaml"
    registry.write_text(
        f"entries:\n"
        f"  - path: docs/operations/{packet.name}\n"
        f"    status: CURRENT_LOAD_BEARING\n"
        f"    archival_ok: false\n",
        encoding="utf-8",
    )

    ctx = _make_ctx(tmp_path)
    entry = _make_entry()

    with patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
        return_value=[],
    ):
        results = enumerate(entry, ctx)

    load_bearing = [c for c in results if c.verdict == VERDICT_LOAD_BEARING]
    assert any(c.path.name == packet.name for c in load_bearing), (
        f"Registry hit should produce LOAD_BEARING; got: {[c.verdict for c in results]}"
    )
    lb = next(c for c in load_bearing if c.path.name == packet.name)
    assert "check_0_registry" in lb.evidence
    assert lb.evidence["check_0_registry"] == "LOAD_BEARING"


# ---------------------------------------------------------------------------
# Test 3: Wave family atomic — mixed verdicts → all LOAD_BEARING
# ---------------------------------------------------------------------------


def test_wave_family_atomic_all_stay_if_one_load_bearing(tmp_path: Path) -> None:
    """
    Wave packets in same family: if any member is LOAD_BEARING, all members
    must be classified LOAD_BEARING (atomic group rule).
    """
    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)

    # Create 3 wave packets in same family (same date+slug)
    wave1 = _make_stale_packet(ops_dir, "task_2025-03-01_multi_wave_wave1", age_days=90)
    wave2 = _make_stale_packet(ops_dir, "task_2025-03-01_multi_wave_wave2", age_days=90)
    wave3 = _make_stale_packet(ops_dir, "task_2025-03-01_multi_wave_wave3", age_days=90)

    # Make wave2 load-bearing via registry
    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir()
    registry = arch_dir / "artifact_authority_status.yaml"
    registry.write_text(
        f"entries:\n"
        f"  - path: docs/operations/{wave2.name}\n"
        f"    status: CURRENT_LOAD_BEARING\n"
        f"    archival_ok: false\n",
        encoding="utf-8",
    )

    ctx = _make_ctx(tmp_path)
    entry = _make_entry()

    with patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
        return_value=[],
    ):
        results = enumerate(entry, ctx)

    wave_results = {
        c.path.name: c.verdict
        for c in results
        if c.path.name in (wave1.name, wave2.name, wave3.name)
    }

    assert len(wave_results) == 3, f"Expected 3 wave results, got: {wave_results}"
    for name, verdict in wave_results.items():
        assert verdict == VERDICT_LOAD_BEARING, (
            f"Wave member '{name}' should be LOAD_BEARING due to atomic group; got {verdict!r}"
        )


# ---------------------------------------------------------------------------
# Test 4: Dry-run path — apply() always dry_run_only + mock diff
# ---------------------------------------------------------------------------


def test_apply_always_dry_run_with_mock_diff(tmp_path: Path) -> None:
    """
    apply() must return dry_run_only=True regardless of context.
    The diff tuple must contain at least one non-empty string.
    """
    ctx = _make_ctx(tmp_path, live_default=False)

    # Create a fake decision candidate
    fake_candidate = Candidate(
        task_id="closed_packet_archive_proposal",
        path=tmp_path / "docs/operations/task_2025-01-01_test",
        verdict=VERDICT_ARCHIVABLE,
        reason="Test candidate",
    )

    result = apply(fake_candidate, ctx)

    assert isinstance(result, ApplyResult)
    assert result.dry_run_only is True
    assert result.task_id == "closed_packet_archive_proposal"
    assert len(result.diff) > 0, "mock diff must contain at least one line"
    assert any("dry-run" in line or "git mv" in line for line in result.diff)


# ---------------------------------------------------------------------------
# Test 5 (M1): Check #6 fails closed when gh unavailable
# ---------------------------------------------------------------------------


def test_check_6_fails_closed_when_gh_unavailable(tmp_path: Path) -> None:
    """
    M1: When `gh pr list` is unavailable (FileNotFoundError) or returns non-zero,
    check #6 must classify the packet LOAD_BEARING_DESPITE_AGE — not ARCHIVE_CANDIDATE.

    The fail-closed direction ensures stale-but-PR'd packets never slip through.
    """
    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    _make_stale_packet(ops_dir, "task_2025-04-01_stale_but_unverifiable", age_days=90)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=60)

    # gh unavailable → FileNotFoundError
    with patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal.subprocess.run",
        side_effect=FileNotFoundError("gh not found"),
    ):
        results = enumerate(entry, ctx)

    # Must be LOAD_BEARING, not ARCHIVE_CANDIDATE
    target = next(
        (c for c in results if c.path.name == "task_2025-04-01_stale_but_unverifiable"),
        None,
    )
    assert target is not None, "Packet must appear in results"
    assert target.verdict == VERDICT_LOAD_BEARING, (
        f"Check #6 unavailable must produce LOAD_BEARING (fail closed); got {target.verdict!r}"
    )
    assert target.evidence.get("check_6_status") == "UNVERIFIED_FAIL_CLOSED", (
        f"Evidence must record UNVERIFIED_FAIL_CLOSED; got {target.evidence.get('check_6_status')!r}"
    )


def test_check_6_fails_closed_when_gh_returns_nonzero(tmp_path: Path) -> None:
    """
    M1: When `gh pr list` exits non-zero (e.g. auth failure), check #6
    must classify the packet LOAD_BEARING_DESPITE_AGE (fail closed).
    """
    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    _make_stale_packet(ops_dir, "task_2025-04-02_stale_gh_error", age_days=90)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=60)

    from unittest.mock import MagicMock
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "error: authentication required"

    with patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal.subprocess.run",
        return_value=mock_proc,
    ):
        results = enumerate(entry, ctx)

    target = next(
        (c for c in results if c.path.name == "task_2025-04-02_stale_gh_error"),
        None,
    )
    assert target is not None
    assert target.verdict == VERDICT_LOAD_BEARING, (
        f"gh non-zero exit must produce LOAD_BEARING; got {target.verdict!r}"
    )
    assert target.evidence.get("check_6_status") == "UNVERIFIED_FAIL_CLOSED"


def test_check_6_load_bearing_when_pr_touches_packet(tmp_path: Path) -> None:
    """
    M1: When gh returns open PRs that touch the packet path, check #6 must
    classify the packet LOAD_BEARING_DESPITE_AGE (actively referenced).
    """
    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    packet_name = "task_2025-04-03_stale_with_pr"
    _make_stale_packet(ops_dir, packet_name, age_days=90)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=60)

    import json
    from unittest.mock import MagicMock
    pr_json = json.dumps([
        {
            "number": 42,
            "title": "Update old packet",
            "files": [{"path": f"docs/operations/{packet_name}/PLAN.md"}],
        }
    ])
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = pr_json

    with patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal.subprocess.run",
        return_value=mock_proc,
    ):
        results = enumerate(entry, ctx)

    target = next(
        (c for c in results if c.path.name == packet_name),
        None,
    )
    assert target is not None
    assert target.verdict == VERDICT_LOAD_BEARING, (
        f"PR touching packet must produce LOAD_BEARING; got {target.verdict!r}"
    )
    assert target.evidence.get("check_6_status") == "LOAD_BEARING_PR_FOUND"


def test_check_6_passes_when_gh_ok_no_prs(tmp_path: Path) -> None:
    """
    M1: When gh runs successfully and finds no PRs touching the packet,
    check #6 passes and the packet may reach ARCHIVE_CANDIDATE.
    """
    ops_dir = tmp_path / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    _make_stale_packet(ops_dir, "task_2025-04-04_stale_clean", age_days=90)

    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=60)

    import json
    from unittest.mock import MagicMock
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps([])  # No open PRs

    with patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
        return_value=[],
    ), patch(
        "maintenance_worker.rules.closed_packet_archive_proposal.subprocess.run",
        return_value=mock_proc,
    ):
        results = enumerate(entry, ctx)

    target = next(
        (c for c in results if c.path.name == "task_2025-04-04_stale_clean"),
        None,
    )
    assert target is not None
    assert target.verdict == VERDICT_ARCHIVABLE, (
        f"gh ok + no PRs must allow ARCHIVE_CANDIDATE; got {target.verdict!r}"
    )
    assert target.evidence.get("check_6_status") == "PASSED_NO_PRS"
