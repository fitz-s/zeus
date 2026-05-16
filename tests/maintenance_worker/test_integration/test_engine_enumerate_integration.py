# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/WAVE_1.5_BATCH_A_CRITIC.md §C1+C2
"""
Integration test: engine.run_tick() with real closed_packet_archive_proposal handler.

C1 fix verification: engine calls handler.enumerate(entry, ctx) and the resulting
Candidates reach the proposal manifest phase (not empty stub).

C2 fix verification: engine calls handler.apply(candidate, ctx) where candidate is
a Candidate instance (not ProposalManifest).

Tests:
  1. Real enumerate: full run_tick with closed_packet_archive_proposal handler wired →
     Candidate list non-empty when stale packets exist; apply() receives Candidate.
  2. Empty catalog: no tasks → no candidates, no apply calls.
  3. Handler not found (unknown task_id): graceful fallback → dry_run_only ApplyResult.
"""
from __future__ import annotations

import time
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from maintenance_worker.core.engine import MaintenanceEngine, run_tick
from maintenance_worker.types.candidates import Candidate
from maintenance_worker.types.specs import EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, live_default: bool = False) -> EngineConfig:
    repo_root = tmp_path / "repo"
    state_dir = tmp_path / "state"
    evidence_dir = tmp_path / "evidence"
    task_catalog = tmp_path / "TASK_CATALOG.yaml"
    safety_contract = tmp_path / "safety.md"

    for d in (repo_root, state_dir, evidence_dir):
        d.mkdir(parents=True)
    safety_contract.write_text("# Safety Contract\n", encoding="utf-8")

    return EngineConfig(
        repo_root=repo_root,
        state_dir=state_dir,
        evidence_dir=evidence_dir,
        task_catalog_path=task_catalog,
        safety_contract_path=safety_contract,
        live_default=live_default,
        scheduler="cron",
        notification_channel="none",
    )


def _clean_guards_context():
    """Mock guards so all 8 guards pass cleanly."""
    mock_run = patch(
        "maintenance_worker.core.guards.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="", stderr=""),
    )
    mock_disk = patch(
        "maintenance_worker.core.guards.shutil.disk_usage",
        return_value=MagicMock(free=50_000_000_000, total=100_000_000_000),
    )
    return mock_run, mock_disk


def _write_catalog_with_archive_proposal(catalog_path: Path) -> None:
    """Write a TASK_CATALOG.yaml containing closed_packet_archive_proposal (daily)."""
    catalog_path.write_text(
        "schema_version: 1\n"
        "tasks:\n"
        "  - id: closed_packet_archive_proposal\n"
        "    description: Archive stale ops packets\n"
        "    schedule: daily\n"
        "    config:\n"
        "      packet_archive_ttl_days: 60\n",
        encoding="utf-8",
    )


def _make_stale_packet(ops_dir: Path, name: str, age_days: float = 90) -> Path:
    """Create a stale packet directory with a closed PLAN.md."""
    packet = ops_dir / name
    packet.mkdir(parents=True)
    plan = packet / "PLAN.md"
    plan.write_text("# Test packet\n\nStatus: CLOSED\n", encoding="utf-8")
    old_ts = time.time() - age_days * 86400
    os.utime(plan, (old_ts, old_ts))
    os.utime(packet, (old_ts, old_ts))
    return packet


# ---------------------------------------------------------------------------
# Test 1: Real enumerate wiring — Candidate list reaches apply()
# ---------------------------------------------------------------------------


def test_engine_enumerate_wires_to_handler_and_apply_receives_candidate(
    tmp_path: Path,
) -> None:
    """
    C1: engine calls closed_packet_archive_proposal.enumerate(entry, ctx).
    C2: engine calls closed_packet_archive_proposal.apply(candidate, ctx)
        where candidate is a Candidate instance (not ProposalManifest).

    Setup: one stale packet (90d), catalog has closed_packet_archive_proposal.
    Assert:
      - At least one Candidate reaches apply().
      - apply() is called with a Candidate (not ProposalManifest).
      - All apply results are dry_run_only=True (task is proposal-only).
    """
    config = _make_config(tmp_path)
    _write_catalog_with_archive_proposal(config.task_catalog_path)

    ops_dir = config.repo_root / "docs" / "operations"
    ops_dir.mkdir(parents=True)
    _make_stale_packet(ops_dir, "task_2025-01-01_old_closed_packet", age_days=90)

    apply_call_args: list = []

    # Import real_apply BEFORE patching so _capture_apply holds the real function.
    from maintenance_worker.rules.closed_packet_archive_proposal import apply as real_apply

    def _capture_apply(candidate, ctx):
        apply_call_args.append(candidate)
        return real_apply(candidate, ctx)

    mock_run, mock_disk = _clean_guards_context()
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="CRON",
        ):
            with patch(
                "maintenance_worker.rules.closed_packet_archive_proposal._code_reference_grep",
                return_value=[],
            ), patch(
                "maintenance_worker.rules.closed_packet_archive_proposal._worktree_branch_check",
                return_value=[],
            ):
                with patch(
                    "maintenance_worker.rules.closed_packet_archive_proposal.apply",
                    side_effect=_capture_apply,
                ):
                    engine = MaintenanceEngine()
                    result = engine.run_tick(config)

    # C1: enumerate was called and produced at least one candidate
    assert len(apply_call_args) >= 1, (
        "C1 fail: enumerate() produced no candidates; handler.apply() never called. "
        f"apply_results={result.apply_results!r}"
    )

    # C2: apply() received Candidate instances, not ProposalManifest
    from maintenance_worker.types.specs import ProposalManifest
    for arg in apply_call_args:
        assert isinstance(arg, Candidate), (
            f"C2 fail: apply() received {type(arg).__name__} instead of Candidate"
        )
        assert not isinstance(arg, ProposalManifest), (
            "C2 fail: apply() received ProposalManifest instead of Candidate"
        )

    # All apply results are dry_run_only (this task is always proposal-only)
    for ar in result.apply_results:
        assert ar.dry_run_only is True, (
            f"closed_packet_archive_proposal.apply() must always be dry_run_only; "
            f"got dry_run_only={ar.dry_run_only!r} for task {ar.task_id!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: Empty catalog → no candidates, no apply calls
# ---------------------------------------------------------------------------


def test_engine_empty_catalog_produces_no_candidates(tmp_path: Path) -> None:
    """
    With an empty task catalog, enumerate produces no TaskCatalogEntries,
    no handler.enumerate() is called, and apply_results is empty.
    """
    config = _make_config(tmp_path)
    config.task_catalog_path.write_text(
        "schema_version: 1\ntasks: []\n", encoding="utf-8"
    )

    mock_run, mock_disk = _clean_guards_context()
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="CRON",
        ):
            engine = MaintenanceEngine()
            result = engine.run_tick(config)

    assert result.apply_results == [], (
        f"Empty catalog must produce no apply_results; got {result.apply_results!r}"
    )
    phase_names = [name for name, _ in result.state_machine_breadcrumbs]
    assert "ENUMERATE_CANDIDATES" in phase_names
    assert "APPLY_DECISIONS" in phase_names


# ---------------------------------------------------------------------------
# Test 3: Unknown task_id → TaskHandlerNotFoundError → dry_run_only fallback
# ---------------------------------------------------------------------------


def test_engine_unknown_handler_falls_back_to_dry_run(tmp_path: Path) -> None:
    """
    A task_id with no handler module causes _dispatch_enumerate to return []
    and _apply_decisions to return dry_run_only=True (safe fallback).
    The tick must complete without raising.
    """
    config = _make_config(tmp_path)
    config.task_catalog_path.write_text(
        "schema_version: 1\n"
        "tasks:\n"
        "  - id: nonexistent_handler_xyz\n"
        "    description: No handler\n"
        "    schedule: daily\n",
        encoding="utf-8",
    )

    mock_run, mock_disk = _clean_guards_context()
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="CRON",
        ):
            engine = MaintenanceEngine()
            result = engine.run_tick(config)

    # Must complete without exception
    assert result.skipped is False
    # 1 task with no candidates → 1 dry_run_only result (from the no-candidate branch)
    assert len(result.apply_results) == 1
    assert result.apply_results[0].dry_run_only is True
    assert result.apply_results[0].task_id == "nonexistent_handler_xyz"
