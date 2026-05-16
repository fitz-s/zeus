# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5 STEP 2 B1
"""
Tests for maintenance_worker.rules.untracked_top_level_quarantine.

4 tests:
  1. Stale untracked file → UNTRACKED_QUARANTINE_CANDIDATE
  2. File under docs/operations/task_*/ → SKIP_ACTIVE_PACKET_DIR
  3. File matching secret pattern (*credential*) → SKIP_FORBIDDEN_PATH
  4. apply() always dry_run_only + mock diff
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.untracked_top_level_quarantine import (
    VERDICT_CANDIDATE,
    VERDICT_SKIP_ACTIVE_PACKET,
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
        run_id="test-run-b1",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
    )


def _make_entry(ttl_days: int = 14) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="untracked_top_level_quarantine",
        description="category-5-stale-untracked-top-level-files",
        schedule="daily",
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "untracked_top_level_quarantine",
            "schedule": "daily",
            "config": {
                "untracked_ttl_days": ttl_days,
                "quarantine_dir": ".archive/untracked",
            },
            "safety": {
                "forbidden_paths": [
                    "docs/operations/task_*/**",
                    ".env*",
                    "*credential*",
                    "*secret*",
                    "*key*",
                ],
            },
        },
    )


# ---------------------------------------------------------------------------
# Test 1: Stale untracked file → UNTRACKED_QUARANTINE_CANDIDATE
# ---------------------------------------------------------------------------


def test_stale_untracked_file_is_candidate(tmp_path: Path) -> None:
    """
    An untracked file with mtime > ttl_days and no forbidden match should
    be classified UNTRACKED_QUARANTINE_CANDIDATE.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(ttl_days=14)

    stale_file = tmp_path / "old_scratch.log"
    stale_file.write_text("old content")

    # Mock: only this file is untracked; mtime is 20 days ago
    old_mtime = time.time() - (20 * 86400)

    with patch(
        "maintenance_worker.rules.untracked_top_level_quarantine._list_untracked_files",
        return_value=["old_scratch.log"],
    ), patch(
        "maintenance_worker.rules.untracked_top_level_quarantine._get_mtime",
        return_value=old_mtime,
    ):
        results = enumerate(entry, ctx)

    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE]
    assert len(candidates) == 1, f"Expected 1 candidate; got: {[c.verdict for c in results]}"
    assert candidates[0].path == stale_file
    assert candidates[0].task_id == "untracked_top_level_quarantine"


# ---------------------------------------------------------------------------
# Test 2: File under docs/operations/task_*/ → SKIP_ACTIVE_PACKET_DIR
# ---------------------------------------------------------------------------


def test_file_under_task_packet_skipped(tmp_path: Path) -> None:
    """
    A file under docs/operations/task_<anything>/ must be classified
    SKIP_ACTIVE_PACKET_DIR — never quarantine active packet dirs.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry()

    # Simulate an untracked file inside a task packet dir
    packet_rel = "docs/operations/task_2026-05-16_doc_alignment_plan/some_artifact.md"
    packet_abs = tmp_path / packet_rel
    packet_abs.parent.mkdir(parents=True, exist_ok=True)
    packet_abs.write_text("artifact")

    old_mtime = time.time() - (30 * 86400)

    with patch(
        "maintenance_worker.rules.untracked_top_level_quarantine._list_untracked_files",
        return_value=[packet_rel],
    ), patch(
        "maintenance_worker.rules.untracked_top_level_quarantine._get_mtime",
        return_value=old_mtime,
    ):
        results = enumerate(entry, ctx)

    # The forbidden pattern check also catches task_* — either SKIP_FORBIDDEN or
    # SKIP_ACTIVE_PACKET is acceptable since the packet is protected.
    skip_verdicts = {VERDICT_SKIP_FORBIDDEN, VERDICT_SKIP_ACTIVE_PACKET}
    assert all(c.verdict in skip_verdicts for c in results), (
        f"Expected all SKIP verdicts; got: {[c.verdict for c in results]}"
    )
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Test 3: File matching secret pattern → SKIP_FORBIDDEN_PATH
# ---------------------------------------------------------------------------


def test_secret_pattern_file_skipped(tmp_path: Path) -> None:
    """
    Files matching forbidden secret patterns (*credential*, *key*, etc.)
    must be classified SKIP_FORBIDDEN_PATH regardless of age.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry()

    secret_file = tmp_path / "api_credential_store.txt"
    secret_file.write_text("secret")

    old_mtime = time.time() - (90 * 86400)

    with patch(
        "maintenance_worker.rules.untracked_top_level_quarantine._list_untracked_files",
        return_value=["api_credential_store.txt"],
    ), patch(
        "maintenance_worker.rules.untracked_top_level_quarantine._get_mtime",
        return_value=old_mtime,
    ):
        results = enumerate(entry, ctx)

    assert len(results) == 1
    assert results[0].verdict == VERDICT_SKIP_FORBIDDEN
    assert results[0].path == secret_file


# ---------------------------------------------------------------------------
# Test 4: apply() always dry_run_only + mock diff present
# ---------------------------------------------------------------------------


def test_apply_always_dry_run_only(tmp_path: Path) -> None:
    """
    apply() must return dry_run_only=True and include a non-empty diff
    (mock) for any candidate — even if live_default were True (it isn't).
    """
    ctx = _make_ctx(tmp_path)
    decision = Candidate(
        task_id="untracked_top_level_quarantine",
        path=tmp_path / "old_file.txt",
        verdict=VERDICT_CANDIDATE,
        reason="stale untracked file: age=20.0d > ttl=14d",
        evidence={"rel_path": "old_file.txt", "age_days": 20.0},
    )

    result = apply(decision, ctx)

    assert result.dry_run_only is True
    assert result.task_id == "untracked_top_level_quarantine"
    assert len(result.diff) > 0
    # Mock diff must reference the file path
    diff_text = " ".join(result.diff)
    assert "old_file.txt" in diff_text
