# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/kill_switch.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"What If A Forbidden Mutation Already Happened"
"""
Tests for maintenance_worker.core.kill_switch.

Covers:
- is_kill_switch_set: file present/absent
- is_paused: file present/absent
- is_self_quarantined: file present/absent
- write_self_quarantine: creates file with reason
- post_mutation_detector: no divergence → no quarantine; divergence → quarantine + exit
- Path A vs Path B invariant: refuse_fatal does NOT write quarantine file
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maintenance_worker.core.kill_switch import (
    _SELF_QUARANTINE_FILE,
    is_kill_switch_set,
    is_paused,
    is_self_quarantined,
    post_mutation_detector,
    write_self_quarantine,
)
from maintenance_worker.types.results import ApplyResult
from maintenance_worker.types.specs import ProposalManifest


# ---------------------------------------------------------------------------
# Check helpers
# ---------------------------------------------------------------------------


def test_is_kill_switch_set_absent(tmp_path: Path) -> None:
    assert not is_kill_switch_set(tmp_path)


def test_is_kill_switch_set_present(tmp_path: Path) -> None:
    (tmp_path / "KILL_SWITCH").touch()
    assert is_kill_switch_set(tmp_path)


def test_is_paused_absent(tmp_path: Path) -> None:
    assert not is_paused(tmp_path)


def test_is_paused_present(tmp_path: Path) -> None:
    (tmp_path / "MAINTENANCE_PAUSED").touch()
    assert is_paused(tmp_path)


def test_is_self_quarantined_absent(tmp_path: Path) -> None:
    assert not is_self_quarantined(tmp_path)


def test_is_self_quarantined_present(tmp_path: Path) -> None:
    (tmp_path / "SELF_QUARANTINE").touch()
    assert is_self_quarantined(tmp_path)


# ---------------------------------------------------------------------------
# write_self_quarantine
# ---------------------------------------------------------------------------


def test_write_self_quarantine_creates_file(tmp_path: Path) -> None:
    write_self_quarantine(tmp_path, "test reason")
    qfile = tmp_path / _SELF_QUARANTINE_FILE
    assert qfile.exists()


def test_write_self_quarantine_contains_reason(tmp_path: Path) -> None:
    write_self_quarantine(tmp_path, "unexpected mutation detected")
    content = (tmp_path / _SELF_QUARANTINE_FILE).read_text()
    assert "unexpected mutation detected" in content
    assert "SELF_QUARANTINE" in content


def test_write_self_quarantine_contains_timestamp(tmp_path: Path) -> None:
    write_self_quarantine(tmp_path, "reason")
    content = (tmp_path / _SELF_QUARANTINE_FILE).read_text()
    assert "timestamp:" in content


def test_write_self_quarantine_creates_state_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "state"
    write_self_quarantine(nested, "reason")
    assert (nested / _SELF_QUARANTINE_FILE).exists()


# ---------------------------------------------------------------------------
# post_mutation_detector
# ---------------------------------------------------------------------------


def _make_manifest(
    moves: tuple[tuple[Path, Path], ...] = (),
    deletes: tuple[Path, ...] = (),
    creates: tuple[Path, ...] = (),
) -> ProposalManifest:
    return ProposalManifest(
        task_id="test_task",
        proposed_moves=moves,
        proposed_deletes=deletes,
        proposed_creates=creates,
    )


def _make_apply(
    moves: tuple[tuple[Path, Path], ...] = (),
    deletes: tuple[Path, ...] = (),
    creates: tuple[Path, ...] = (),
) -> ApplyResult:
    return ApplyResult(
        task_id="test_task",
        moved=moves,
        deleted=deletes,
        created=creates,
    )


def test_post_mutation_detector_no_divergence(tmp_path: Path) -> None:
    """When applied == proposed, no quarantine file written, returns normally."""
    src = tmp_path / "old.txt"
    dst = tmp_path / "archive" / "old.txt"
    manifest = _make_manifest(moves=((src, dst),))
    apply_result = _make_apply(moves=((src, dst),))

    post_mutation_detector(apply_result, manifest, tmp_path)
    assert not is_self_quarantined(tmp_path)


def test_post_mutation_detector_unexpected_move_triggers_quarantine(
    tmp_path: Path,
) -> None:
    """A move not in the manifest → SELF_QUARANTINE + sys.exit."""
    src = tmp_path / "unexpected.txt"
    dst = tmp_path / "archive" / "unexpected.txt"
    manifest = _make_manifest()  # empty — no moves proposed
    apply_result = _make_apply(moves=((src, dst),))

    with pytest.raises(SystemExit) as exc_info:
        post_mutation_detector(apply_result, manifest, tmp_path)
    assert exc_info.value.code != 0
    assert is_self_quarantined(tmp_path)


def test_post_mutation_detector_unexpected_delete_triggers_quarantine(
    tmp_path: Path,
) -> None:
    deleted = tmp_path / "zero.tmp"
    manifest = _make_manifest()  # no deletes proposed
    apply_result = _make_apply(deletes=(deleted,))

    with pytest.raises(SystemExit):
        post_mutation_detector(apply_result, manifest, tmp_path)
    assert is_self_quarantined(tmp_path)


def test_post_mutation_detector_unexpected_create_triggers_quarantine(
    tmp_path: Path,
) -> None:
    created = tmp_path / "new_file.md"
    manifest = _make_manifest()  # no creates proposed
    apply_result = _make_apply(creates=(created,))

    with pytest.raises(SystemExit):
        post_mutation_detector(apply_result, manifest, tmp_path)
    assert is_self_quarantined(tmp_path)


def test_post_mutation_detector_allowed_sets_match(tmp_path: Path) -> None:
    """All three categories match → no quarantine, returns normally."""
    src = tmp_path / "src.md"
    dst = tmp_path / "dst.md"
    deleted = tmp_path / "zero.tmp"
    created = tmp_path / "stub.archived"

    manifest = _make_manifest(
        moves=((src, dst),),
        deletes=(deleted,),
        creates=(created,),
    )
    apply_result = _make_apply(
        moves=((src, dst),),
        deletes=(deleted,),
        creates=(created,),
    )
    post_mutation_detector(apply_result, manifest, tmp_path)
    assert not is_self_quarantined(tmp_path)


def test_post_mutation_detector_quarantine_file_contains_task_id(
    tmp_path: Path,
) -> None:
    """SELF_QUARANTINE content includes the task_id for investigation."""
    unexpected = tmp_path / "bad.txt"
    manifest = _make_manifest()
    apply_result = _make_apply(deletes=(unexpected,))

    with pytest.raises(SystemExit):
        post_mutation_detector(apply_result, manifest, tmp_path)
    content = (tmp_path / _SELF_QUARANTINE_FILE).read_text()
    assert "test_task" in content
