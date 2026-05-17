# Created: 2026-05-16
# Last reused or audited: 2026-05-16
# Authority basis: docs/operations/task_2026-05-16_doc_alignment_plan/PLAN.md §WAVE 1.5 STEP 2 B2
"""
Tests for maintenance_worker.rules.zero_byte_state_cleanup.

5 tests:
  1. Zero-byte stale file → ZERO_BYTE_DELETE_CANDIDATE
  2. Non-zero file → SKIP_NON_ZERO
  3. Locked file (mock lsof) → SKIP_LOCKED_LSOF
  4. SQLite-attached pattern → SKIP_SQLITE_ATTACHED
  5. apply() dry-run mock + live-mode actual delete
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from maintenance_worker.rules.zero_byte_state_cleanup import (
    VERDICT_CANDIDATE,
    VERDICT_SKIP_FRESH,
    VERDICT_SKIP_LOCKED,
    VERDICT_SKIP_NON_ZERO,
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
    config = EngineConfig(
        repo_root=tmp_path,
        state_dir=tmp_path / "state",
        evidence_dir=tmp_path / "evidence",
        task_catalog_path=tmp_path / "catalog.yaml",
        safety_contract_path=tmp_path / "safety.yaml",
        live_default=True,
        scheduler="launchd",
        notification_channel="discord",
    )
    return TickContext(
        run_id="test-run-b2",
        started_at=datetime.now(tz=timezone.utc),
        config=config,
        invocation_mode="SCHEDULED",
        dry_run_only=dry_run_only,
    )


def _make_entry(age_days: int = 7) -> TaskCatalogEntry:
    spec = TaskSpec(
        task_id="zero_byte_state_cleanup",
        description="category-6-empty-zero-byte-result-files",
        schedule="daily",
        dry_run_floor_exempt=True,
    )
    return TaskCatalogEntry(
        spec=spec,
        raw={
            "id": "zero_byte_state_cleanup",
            "schedule": "daily",
            "live_default": True,
            "dry_run_floor_exempt": True,
            "config": {
                "zero_byte_age_days": age_days,
                "target_dirs": ["state/"],
            },
            "safety": {
                "forbidden": [
                    "non_zero_files",
                    "paths_held_by_open_lsof_handle",
                    "paths_referenced_by_active_sqlite_attach",
                ],
            },
        },
    )


def _old_mtime() -> float:
    """Return mtime 20 days in the past."""
    return time.time() - (20 * 86400)


# ---------------------------------------------------------------------------
# Test 1: Zero-byte stale file → ZERO_BYTE_DELETE_CANDIDATE
# ---------------------------------------------------------------------------


def test_zero_byte_stale_file_is_candidate(tmp_path: Path) -> None:
    """
    A zero-byte file in state/ older than age_days must be classified
    ZERO_BYTE_DELETE_CANDIDATE.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(age_days=7)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    zero_file = state_dir / "empty_result.json"
    zero_file.write_bytes(b"")  # zero bytes

    # Set mtime to 20 days ago via mock
    with patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
        return_value=False,
    ), patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_sqlite_companion",
        return_value=False,
    ), patch("pathlib.Path.stat") as mock_stat:
        import os
        stat_result = os.stat_result((
            0o100644, 0, 0, 1, 0, 0, 0,  # mode, ino, dev, nlink, uid, gid, size=0
            _old_mtime(), _old_mtime(), _old_mtime(),
        ))
        mock_stat.return_value = stat_result
        results = enumerate(entry, ctx)

    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE]
    assert len(candidates) >= 1, f"Expected ≥1 candidate; got: {[c.verdict for c in results]}"
    assert any(c.path == zero_file for c in candidates)


# ---------------------------------------------------------------------------
# Test 2: Non-zero file → SKIP_NON_ZERO
# ---------------------------------------------------------------------------


def test_non_zero_file_skipped(tmp_path: Path) -> None:
    """
    A file with size > 0 must be classified SKIP_NON_ZERO regardless of age.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry()

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    nonempty_file = state_dir / "has_content.json"
    nonempty_file.write_text('{"key": "value"}')  # non-zero

    results = enumerate(entry, ctx)

    non_zero_skips = [c for c in results if c.verdict == VERDICT_SKIP_NON_ZERO]
    assert len(non_zero_skips) >= 1, (
        f"Expected ≥1 SKIP_NON_ZERO; got: {[c.verdict for c in results]}"
    )
    assert any(c.path == nonempty_file for c in non_zero_skips)


# ---------------------------------------------------------------------------
# Test 3: Locked file (mock lsof) → SKIP_LOCKED_LSOF
# ---------------------------------------------------------------------------


def test_lsof_locked_file_skipped(tmp_path: Path) -> None:
    """
    A zero-byte file reported as locked by lsof must be classified
    SKIP_LOCKED_LSOF and never touched.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(age_days=7)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    locked_file = state_dir / "locked.lock"
    locked_file.write_bytes(b"")

    with patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
        return_value=True,  # simulate lsof reports it held
    ), patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_sqlite_companion",
        return_value=False,
    ), patch("pathlib.Path.stat") as mock_stat:
        import os
        stat_result = os.stat_result((
            0o100644, 0, 0, 1, 0, 0, 0,
            _old_mtime(), _old_mtime(), _old_mtime(),
        ))
        mock_stat.return_value = stat_result
        results = enumerate(entry, ctx)

    locked_skips = [c for c in results if c.verdict == VERDICT_SKIP_LOCKED]
    assert len(locked_skips) >= 1, (
        f"Expected ≥1 SKIP_LOCKED; got: {[c.verdict for c in results]}"
    )
    assert any(c.path == locked_file for c in locked_skips)


# ---------------------------------------------------------------------------
# Test 4: SQLite-attached pattern → SKIP_SQLITE_ATTACHED
# ---------------------------------------------------------------------------


def test_sqlite_pattern_file_skipped(tmp_path: Path) -> None:
    """
    A zero-byte file with .db or .db-wal suffix must be classified
    SKIP_SQLITE_ATTACHED to avoid corrupting live WAL.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(age_days=7)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_file = state_dir / "zeus_world.db"
    db_file.write_bytes(b"")

    with patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
        return_value=False,
    ), patch("pathlib.Path.stat") as mock_stat:
        import os
        stat_result = os.stat_result((
            0o100644, 0, 0, 1, 0, 0, 0,
            _old_mtime(), _old_mtime(), _old_mtime(),
        ))
        mock_stat.return_value = stat_result
        results = enumerate(entry, ctx)

    sqlite_skips = [c for c in results if c.verdict == VERDICT_SKIP_SQLITE]
    assert len(sqlite_skips) >= 1, (
        f"Expected ≥1 SKIP_SQLITE; got: {[c.verdict for c in results]}"
    )
    assert any(c.path == db_file for c in sqlite_skips)


# ---------------------------------------------------------------------------
# Test 5a: apply() dry-run mock (ctx.dry_run_only=True)
# ---------------------------------------------------------------------------


def test_apply_dry_run_mode_returns_mock(tmp_path: Path) -> None:
    """
    apply() with ctx.dry_run_only=True must return dry_run_only=True
    and include a non-empty mock diff. No file deletion occurs.
    """
    ctx = _make_ctx(tmp_path, dry_run_only=True)

    # File doesn't even need to exist — dry run doesn't touch disk
    ghost_file = tmp_path / "state" / "zero.json"

    decision = Candidate(
        task_id="zero_byte_state_cleanup",
        path=ghost_file,
        verdict=VERDICT_CANDIDATE,
        reason="zero-byte stale file: age=20.0d > 7d",
        evidence={"age_days": 20.0, "ttl_days": 7},
    )

    result = apply(decision, ctx)

    assert result.dry_run_only is True
    assert result.task_id == "zero_byte_state_cleanup"
    assert len(result.diff) > 0


# ---------------------------------------------------------------------------
# Test 5b: apply() live-mode actual delete (ctx.dry_run_only=False)
# ---------------------------------------------------------------------------


def test_apply_live_mode_deletes_file(tmp_path: Path) -> None:
    """
    apply() with ctx.dry_run_only=False must actually unlink the file
    and return dry_run_only=False with deleted populated.

    This is the only handler with live_default=true. The TOP guard must
    NOT fire when dry_run_only=False.
    """
    ctx = _make_ctx(tmp_path, dry_run_only=False)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    zero_file = state_dir / "empty_result.json"
    zero_file.write_bytes(b"")
    assert zero_file.exists()

    decision = Candidate(
        task_id="zero_byte_state_cleanup",
        path=zero_file,
        verdict=VERDICT_CANDIDATE,
        reason="zero-byte stale file: age=20.0d > 7d",
        evidence={"age_days": 20.0, "ttl_days": 7},
    )

    result = apply(decision, ctx)

    assert result.dry_run_only is False
    assert result.task_id == "zero_byte_state_cleanup"
    assert not zero_file.exists(), "File should have been deleted in live mode"
    assert zero_file in result.deleted


# ---------------------------------------------------------------------------
# CB1 Tests: expanded sqlite-companion filter
# ---------------------------------------------------------------------------


def test_db_journal_file_skipped(tmp_path: Path) -> None:
    """
    A zero-byte .db-journal file must be classified SKIP_SQLITE_ATTACHED.
    Deleting a rollback-journal while parent .db is mid-transaction = corruption.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(age_days=7)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    journal_file = state_dir / "zeus_world.db-journal"
    journal_file.write_bytes(b"")

    with patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
        return_value=False,
    ), patch("pathlib.Path.stat") as mock_stat:
        import os
        stat_result = os.stat_result((
            0o100644, 0, 0, 1, 0, 0, 0,
            _old_mtime(), _old_mtime(), _old_mtime(),
        ))
        mock_stat.return_value = stat_result
        results = enumerate(entry, ctx)

    sqlite_skips = [c for c in results if c.verdict == VERDICT_SKIP_SQLITE]
    assert any(c.path == journal_file for c in sqlite_skips), (
        f".db-journal must be SKIP_SQLITE_ATTACHED; got: {[c.verdict for c in results]}"
    )


def test_sqlite3_shm_file_skipped(tmp_path: Path) -> None:
    """
    A zero-byte .sqlite3-shm file must be classified SKIP_SQLITE_ATTACHED.
    .sqlite3 extension is common; its -shm companion is an active WAL index.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(age_days=7)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    shm_file = state_dir / "forecasts.sqlite3-shm"
    shm_file.write_bytes(b"")

    with patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
        return_value=False,
    ), patch("pathlib.Path.stat") as mock_stat:
        import os
        stat_result = os.stat_result((
            0o100644, 0, 0, 1, 0, 0, 0,
            _old_mtime(), _old_mtime(), _old_mtime(),
        ))
        mock_stat.return_value = stat_result
        results = enumerate(entry, ctx)

    sqlite_skips = [c for c in results if c.verdict == VERDICT_SKIP_SQLITE]
    assert any(c.path == shm_file for c in sqlite_skips), (
        f".sqlite3-shm must be SKIP_SQLITE_ATTACHED; got: {[c.verdict for c in results]}"
    )


def test_writer_lock_bulk_file_skipped_via_companion_check(tmp_path: Path) -> None:
    """
    A zero-byte zeus-world.db.writer-lock.bulk file must be classified
    SKIP_SQLITE_ATTACHED via the companion-sibling check.

    Path.suffix == '.bulk' — not in suffix set — so the companion-sibling check
    must detect that zeus-world.db exists in the same dir and skip this file.
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(age_days=7)

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Companion .db file must exist for the sibling check to fire
    companion_db = state_dir / "zeus-world.db"
    companion_db.write_bytes(b"fake-db-content")

    bulk_file = state_dir / "zeus-world.db.writer-lock.bulk"
    bulk_file.write_bytes(b"")

    with patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
        return_value=False,
    ), patch("pathlib.Path.stat") as mock_stat:
        import os

        def stat_side_effect(self=None):
            # companion_db is non-zero so use real stat; bulk_file is old zero-byte
            path_obj = self if self is not None else tmp_path
            # We need size=0 for bulk_file, real for companion_db
            # Since Path.stat is patched globally, check the path name
            return os.stat_result((
                0o100644, 0, 0, 1, 0, 0, 0,
                _old_mtime(), _old_mtime(), _old_mtime(),
            ))

        mock_stat.return_value = os.stat_result((
            0o100644, 0, 0, 1, 0, 0, 0,
            _old_mtime(), _old_mtime(), _old_mtime(),
        ))
        results = enumerate(entry, ctx)

    sqlite_skips = [c for c in results if c.verdict == VERDICT_SKIP_SQLITE]
    assert any(c.path == bulk_file for c in sqlite_skips), (
        f"zeus-world.db.writer-lock.bulk must be SKIP_SQLITE_ATTACHED via companion check; "
        f"got: {[(c.path.name, c.verdict) for c in results]}"
    )


def test_generic_tmp_file_is_candidate(tmp_path: Path) -> None:
    """
    Positive control: a stale zero-byte .tmp file with no sqlite companion must be
    classified ZERO_BYTE_DELETE_CANDIDATE (not skipped by the expanded filter).
    """
    ctx = _make_ctx(tmp_path)
    entry = _make_entry(age_days=7)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    tmp_file = state_dir / "stale_result.tmp"
    tmp_file.write_bytes(b"")

    with patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
        return_value=False,
    ), patch(
        "maintenance_worker.rules.zero_byte_state_cleanup._is_sqlite_companion",
        return_value=False,
    ), patch("pathlib.Path.stat") as mock_stat:
        import os
        mock_stat.return_value = os.stat_result((
            0o100644, 0, 0, 1, 0, 0, 0,
            _old_mtime(), _old_mtime(), _old_mtime(),
        ))
        results = enumerate(entry, ctx)

    candidates = [c for c in results if c.verdict == VERDICT_CANDIDATE]
    assert any(c.path == tmp_file for c in candidates), (
        f".tmp file should be ZERO_BYTE_DELETE_CANDIDATE; got: {[(c.path.name, c.verdict) for c in results]}"
    )


# ---------------------------------------------------------------------------
# MB1 Test: TOCTOU re-verify in apply()
# ---------------------------------------------------------------------------


def test_apply_toctou_skip_if_file_grows(tmp_path: Path) -> None:
    """
    MB1: apply() must skip deletion if the file has grown (size > 0) between
    enumerate() and apply() time.

    Simulate: decision has VERDICT_CANDIDATE but file is non-zero at apply-time
    (mocked via Path.stat returning st_size > 0).
    apply() must return dry_run_only=True and NOT call unlink().
    """
    ctx = _make_ctx(tmp_path, dry_run_only=False)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    growing_file = state_dir / "race_file.json"
    growing_file.write_bytes(b"")  # zero at enumerate time

    decision = Candidate(
        task_id="zero_byte_state_cleanup",
        path=growing_file,
        verdict=VERDICT_CANDIDATE,
        reason="zero-byte stale file: age=20.0d > 7d",
        evidence={"age_days": 20.0, "ttl_days": 7},
    )

    import os
    # Simulate file having grown between enumerate and apply
    non_zero_stat = os.stat_result((
        0o100644, 0, 0, 1, 0, 0, 42,  # st_size=42 (non-zero)
        0.0, 0.0, 0.0,
    ))

    with patch("maintenance_worker.rules.zero_byte_state_cleanup._is_locked_by_lsof",
               return_value=False), \
         patch("maintenance_worker.rules.zero_byte_state_cleanup._is_sqlite_companion",
               return_value=False), \
         patch.object(type(growing_file), "stat", return_value=non_zero_stat):
        result = apply(decision, ctx)

    assert result.dry_run_only is True, "apply() must skip when file grew post-enumerate"
    assert growing_file.exists(), "File must NOT have been deleted"
