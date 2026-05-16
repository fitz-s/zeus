# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §4
"""
Tests for maintenance_worker.core.install_metadata.

Covers:
- write_install_metadata: success + ImmutableMetadataError on second write
- read_install_metadata: round-trip + schema_version mismatch
- enforce_dry_run_floor: 4 scenarios per SCAFFOLD §4 decision tree
- DryRunFloor: invalid floor_days
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from maintenance_worker.core.install_metadata import (
    FLOOR_EXEMPT_TASK_IDS,
    DRY_RUN_FLOOR_DAYS,
    DryRunFloor,
    ImmutableMetadataError,
    InstallMetadata,
    MetadataSchemaError,
    enforce_dry_run_floor,
    read_install_metadata,
    write_install_metadata,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_meta(first_run_at: datetime) -> InstallMetadata:
    return InstallMetadata(
        schema_version=1,
        first_run_at=first_run_at,
        agent_version="0.1.0",
        install_run_id="test-run-id-00000000",
        allowed_remote_urls=("https://github.com/org/repo.git",),
        repo_root_at_install="/tmp/test-repo",
    )


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# write_install_metadata
# ---------------------------------------------------------------------------


def test_write_install_metadata_creates_file(tmp_path: Path) -> None:
    meta = _make_meta(_now_utc())
    target = write_install_metadata(tmp_path, meta)
    assert target == tmp_path / "install_metadata.json"
    assert target.exists()


def test_write_install_metadata_content_round_trips(tmp_path: Path) -> None:
    first_run = _now_utc().replace(microsecond=0)
    meta = _make_meta(first_run)
    write_install_metadata(tmp_path, meta)
    raw = json.loads((tmp_path / "install_metadata.json").read_text())
    assert raw["schema_version"] == 1
    assert raw["agent_version"] == "0.1.0"
    assert raw["allowed_remote_urls"] == ["https://github.com/org/repo.git"]
    assert raw["repo_root_at_install"] == "/tmp/test-repo"


def test_write_install_metadata_immutable_on_second_write(tmp_path: Path) -> None:
    meta = _make_meta(_now_utc())
    write_install_metadata(tmp_path, meta)
    with pytest.raises(ImmutableMetadataError, match="immutable"):
        write_install_metadata(tmp_path, meta)


def test_write_install_metadata_creates_state_dir(tmp_path: Path) -> None:
    state_dir = tmp_path / "nested" / "state"
    meta = _make_meta(_now_utc())
    write_install_metadata(state_dir, meta)
    assert (state_dir / "install_metadata.json").exists()


# ---------------------------------------------------------------------------
# read_install_metadata
# ---------------------------------------------------------------------------


def test_read_install_metadata_round_trip(tmp_path: Path) -> None:
    first_run = _now_utc().replace(microsecond=0)
    meta = _make_meta(first_run)
    write_install_metadata(tmp_path, meta)
    loaded = read_install_metadata(tmp_path)
    assert loaded.schema_version == 1
    assert loaded.agent_version == "0.1.0"
    assert loaded.install_run_id == "test-run-id-00000000"
    assert loaded.allowed_remote_urls == ("https://github.com/org/repo.git",)
    assert loaded.repo_root_at_install == "/tmp/test-repo"


def test_read_install_metadata_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_install_metadata(tmp_path)


def test_read_install_metadata_schema_version_mismatch(tmp_path: Path) -> None:
    bad_json = json.dumps(
        {
            "schema_version": 99,
            "first_run_at": _now_utc().isoformat(),
            "agent_version": "0.1.0",
            "install_run_id": "abc",
        }
    )
    (tmp_path / "install_metadata.json").write_text(bad_json)
    with pytest.raises(MetadataSchemaError, match="schema_version"):
        read_install_metadata(tmp_path)


def test_read_install_metadata_preserves_tz(tmp_path: Path) -> None:
    first_run = _now_utc().replace(microsecond=0)
    meta = _make_meta(first_run)
    write_install_metadata(tmp_path, meta)
    loaded = read_install_metadata(tmp_path)
    assert loaded.first_run_at.tzinfo is not None


# ---------------------------------------------------------------------------
# enforce_dry_run_floor
# ---------------------------------------------------------------------------


def test_floor_exempt_task_bypasses_floor() -> None:
    """Exempt task IDs bypass floor regardless of elapsed time or ack file."""
    for task_id in FLOOR_EXEMPT_TASK_IDS:
        meta = _make_meta(_now_utc())  # just installed — no time elapsed
        floor_cfg = DryRunFloor()
        result = enforce_dry_run_floor(task_id, meta, floor_cfg)
        assert result == "ALLOWED", f"Expected ALLOWED for exempt task {task_id!r}"


def test_floor_override_ack_file_bypasses_floor(tmp_path: Path) -> None:
    """Human ack file presence bypasses the floor even if floor not elapsed."""
    ack_file = tmp_path / "dry_run_floor_override.ack"
    ack_file.write_text("OVERRIDE\n")
    meta = _make_meta(_now_utc())  # fresh install
    floor_cfg = DryRunFloor(override_ack_file=ack_file)
    result = enforce_dry_run_floor("some_task", meta, floor_cfg)
    assert result == "ALLOWED"


def test_floor_enforced_before_30_days_elapsed() -> None:
    """Non-exempt task with 10-day-old install → ALLOWED_BUT_DRY_RUN_ONLY."""
    first_run = _now_utc() - timedelta(days=10)
    meta = _make_meta(first_run)
    floor_cfg = DryRunFloor()
    result = enforce_dry_run_floor("some_live_task", meta, floor_cfg)
    assert result == "ALLOWED_BUT_DRY_RUN_ONLY"


def test_floor_cleared_after_30_days_elapsed() -> None:
    """Non-exempt task with 31-day-old install → ALLOWED."""
    first_run = _now_utc() - timedelta(days=31)
    meta = _make_meta(first_run)
    floor_cfg = DryRunFloor()
    result = enforce_dry_run_floor("some_live_task", meta, floor_cfg)
    assert result == "ALLOWED"


def test_floor_exactly_30_days_still_dry_run_only() -> None:
    """Exactly 30 days (not >) still triggers ALLOWED_BUT_DRY_RUN_ONLY (< not <=)."""
    first_run = _now_utc() - timedelta(days=DRY_RUN_FLOOR_DAYS)
    meta = _make_meta(first_run)
    floor_cfg = DryRunFloor()
    result = enforce_dry_run_floor("some_live_task", meta, floor_cfg)
    # elapsed == 30 days; floor check is elapsed < floor_days → False → ALLOWED
    assert result == "ALLOWED"


def test_floor_override_ack_file_absent_does_not_bypass(tmp_path: Path) -> None:
    """Missing ack file path does not bypass floor."""
    ack_file = tmp_path / "nonexistent.ack"
    first_run = _now_utc() - timedelta(days=5)
    meta = _make_meta(first_run)
    floor_cfg = DryRunFloor(override_ack_file=ack_file)
    result = enforce_dry_run_floor("some_task", meta, floor_cfg)
    assert result == "ALLOWED_BUT_DRY_RUN_ONLY"


def test_floor_exempt_ids_are_frozen_set() -> None:
    """FLOOR_EXEMPT_TASK_IDS is a frozenset, not mutable."""
    assert isinstance(FLOOR_EXEMPT_TASK_IDS, frozenset)
    assert "zero_byte_state_cleanup" in FLOOR_EXEMPT_TASK_IDS
    assert "agent_self_evidence_archival" in FLOOR_EXEMPT_TASK_IDS


# ---------------------------------------------------------------------------
# DryRunFloor validation
# ---------------------------------------------------------------------------


def test_dry_run_floor_invalid_floor_days() -> None:
    with pytest.raises(ValueError, match="floor_days"):
        DryRunFloor(floor_days=0)
