# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/WAVE_1.5_BATCH_B_CRITIC.md §MB3
"""
Integration test: engine.run_tick() → zero_byte_state_cleanup live path.

MB3 fix: verify that the engine actually drives zero_byte_state_cleanup's
enumerate() and apply() end-to-end, including the floor-exempt live-deletion path.

This is the ONLY handler with live_default=True + dry_run_floor_exempt=True;
it bypasses both the force_dry_run guard and the dry-run floor gate.

Tests:
  1. Stale zero-byte file → engine deletes it (dry_run_only=False, file gone).
  2. Zero-byte .db-journal file → engine skips it (SKIP_SQLITE_ATTACHED, file preserved).
  3. Non-zero file → engine skips it (SKIP_NON_ZERO, file preserved).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from maintenance_worker.core.engine import MaintenanceEngine
from maintenance_worker.types.specs import EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> EngineConfig:
    repo_root = tmp_path / "repo"
    state_dir = tmp_path / "state_engine"
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
        live_default=True,
        scheduler="cron",
        notification_channel="none",
    )


def _write_catalog(catalog_path: Path, age_days: int = 7) -> None:
    """Write a minimal TASK_CATALOG.yaml with zero_byte_state_cleanup only."""
    catalog_path.write_text(
        f"schema_version: 1\n"
        f"tasks:\n"
        f"  - id: zero_byte_state_cleanup\n"
        f"    description: category-6-empty-zero-byte-result-files\n"
        f"    schedule: daily\n"
        f"    live_default: true\n"
        f"    dry_run_floor_exempt: true\n"
        f"    config:\n"
        f"      zero_byte_age_days: {age_days}\n"
        f"      target_dirs:\n"
        f"        - state/\n"
        f"    safety:\n"
        f"      forbidden:\n"
        f"        - non_zero_files\n"
        f"        - paths_held_by_open_lsof_handle\n"
        f"        - paths_referenced_by_active_sqlite_attach\n",
        encoding="utf-8",
    )


def _old_mtime() -> float:
    """Return mtime 20 days in the past."""
    return time.time() - (20 * 86400)


def _clean_guards_context():
    """Return context managers that make all 8 guards pass cleanly."""
    mock_run = patch(
        "maintenance_worker.core.guards.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="", stderr=""),
    )
    mock_disk = patch(
        "maintenance_worker.core.guards.shutil.disk_usage",
        return_value=MagicMock(free=50_000_000_000, total=100_000_000_000),
    )
    return mock_run, mock_disk


# ---------------------------------------------------------------------------
# Test 1: Stale zero-byte file → engine deletes it (live path)
# ---------------------------------------------------------------------------


def test_engine_live_deletes_stale_zero_byte_file(tmp_path: Path) -> None:
    """
    MB3 (updated for PR #124 Codex P2 gate): engine drives zero_byte_state_cleanup
    enumerate() → apply() end-to-end, but with the P5.5 empty-manifest gate active,
    the apply() call receives dry_run_only=True — preventing the live deletion →
    empty manifest → SELF_QUARANTINE brick pattern.

    The MB3 invariant (engine dispatches the handler) is preserved: the gate
    warning log proves the dispatch reached _apply_decisions and the gate fired.
    The previous assertion (live delete actually happens) is now WRONG by design —
    live deletion with an empty stub manifest bricks the tick permanently.

    When P5.5 _emit_dry_run_proposal() ships real manifest entries, the gate
    will stop firing and a separate P5.5 integration test should assert live
    deletion works with a populated manifest.

    REMOVE the P5.5 guard from this test comment when P5.5 lands.
    """
    config = _make_config(tmp_path)
    _write_catalog(config.task_catalog_path, age_days=7)

    # Create state/ subdir under repo_root (handler walks repo_root / "state/")
    state_dir = config.repo_root / "state"
    state_dir.mkdir(parents=True)

    stale_file = state_dir / "stale_result.json"
    stale_file.write_bytes(b"")  # zero bytes

    # Set mtime to 20 days ago
    old_ts = _old_mtime()
    os.utime(stale_file, (old_ts, old_ts))

    mock_run, mock_disk = _clean_guards_context()
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="CRON",
        ):
            with patch(
                "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
                return_value=False,
            ):
                engine = MaintenanceEngine()
                result = engine.run_tick(config)

    # P5.5 gate: all apply results must be dry_run_only=True while manifest is stub.
    # Gate prevents: live delete → empty manifest mismatch → SELF_QUARANTINE → exit(50).
    task_results = [ar for ar in result.apply_results if ar.task_id == "zero_byte_state_cleanup"]
    assert len(task_results) >= 1, (
        "Engine must have dispatched zero_byte_state_cleanup and returned ≥1 ApplyResult"
    )
    for ar in task_results:
        assert ar.dry_run_only is True, (
            "P5.5 gate must force dry_run_only=True for live-exempt handler "
            "when manifest is empty stub (Codex PR #124 P2). "
            f"Got dry_run_only={ar.dry_run_only!r}"
        )

    # File must be preserved — no live deletion while gate is active
    assert stale_file.exists(), (
        "P5.5 gate: stale zero-byte file must be preserved (no live delete) "
        "while _emit_dry_run_proposal() returns empty stub manifest"
    )


# ---------------------------------------------------------------------------
# Test 2: Zero-byte .db-journal file → engine skips it (sqlite companion)
# ---------------------------------------------------------------------------


def test_engine_skips_sqlite_journal_companion(tmp_path: Path) -> None:
    """
    MB3: engine must NOT delete a zero-byte .db-journal file even in live mode.
    The sqlite-companion filter (_is_sqlite_companion) must fire during enumerate()
    and classify it SKIP_SQLITE_ATTACHED — apply() never called for it.
    """
    config = _make_config(tmp_path)
    _write_catalog(config.task_catalog_path, age_days=7)

    state_dir = config.repo_root / "state"
    state_dir.mkdir(parents=True)

    journal_file = state_dir / "zeus_world.db-journal"
    journal_file.write_bytes(b"")  # zero bytes
    old_ts = _old_mtime()
    os.utime(journal_file, (old_ts, old_ts))

    mock_run, mock_disk = _clean_guards_context()
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="CRON",
        ):
            with patch(
                "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
                return_value=False,
            ):
                engine = MaintenanceEngine()
                result = engine.run_tick(config)

    # The journal file must still exist
    assert journal_file.exists(), (
        "Engine must NOT delete a .db-journal companion file"
    )

    # All apply results for this task must be dry_run_only (skip verdict)
    task_results = [ar for ar in result.apply_results if ar.task_id == "zero_byte_state_cleanup"]
    for ar in task_results:
        assert ar.dry_run_only is True, (
            f"zero_byte_state_cleanup must skip .db-journal; got dry_run_only={ar.dry_run_only!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: Non-zero file → engine skips it (SKIP_NON_ZERO)
# ---------------------------------------------------------------------------


def test_engine_skips_non_zero_file(tmp_path: Path) -> None:
    """
    MB3: engine must NOT delete a non-zero file even if it's stale.
    enumerate() classifies it SKIP_NON_ZERO; apply() returns dry_run_only=True.
    """
    config = _make_config(tmp_path)
    _write_catalog(config.task_catalog_path, age_days=7)

    state_dir = config.repo_root / "state"
    state_dir.mkdir(parents=True)

    nonempty_file = state_dir / "has_content.json"
    nonempty_file.write_text('{"key": "value"}')  # non-zero
    old_ts = _old_mtime()
    os.utime(nonempty_file, (old_ts, old_ts))

    mock_run, mock_disk = _clean_guards_context()
    with mock_run, mock_disk:
        with patch(
            "maintenance_worker.core.engine.check_scheduler_invocation",
            return_value="CRON",
        ):
            with patch(
                "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
                return_value=False,
            ):
                engine = MaintenanceEngine()
                result = engine.run_tick(config)

    # Non-zero file must survive
    assert nonempty_file.exists(), (
        "Engine must NOT delete a non-zero file"
    )

    # All apply results must be dry_run_only (SKIP_NON_ZERO path)
    task_results = [ar for ar in result.apply_results if ar.task_id == "zero_byte_state_cleanup"]
    for ar in task_results:
        assert ar.dry_run_only is True, (
            f"Non-zero file must produce dry_run_only; got dry_run_only={ar.dry_run_only!r}"
        )
