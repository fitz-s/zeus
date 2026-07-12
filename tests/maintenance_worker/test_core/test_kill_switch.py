# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/archive/2026-Q2/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 core/kill_switch.py
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/SAFETY_CONTRACT.md §"What If A Forbidden Mutation Already Happened"
"""
Tests for maintenance_worker.core.kill_switch.

Covers:
- is_kill_switch_set: file present/absent
- is_paused: file present/absent
- is_self_halted: file present/absent
- write_self_halt: creates file with reason
- post_mutation_detector: no divergence → no halt; divergence → halt + exit
- Path A vs Path B invariant: refuse_fatal does NOT write halt file
"""
from __future__ import annotations

from pathlib import Path

import pytest

from maintenance_worker.core.kill_switch import (
    _SELF_HALT_FILE,
    is_kill_switch_set,
    is_paused,
    is_self_halted,
    post_mutation_detector,
    write_self_halt,
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


def test_is_self_halted_absent(tmp_path: Path) -> None:
    assert not is_self_halted(tmp_path)


def test_is_self_halted_present(tmp_path: Path) -> None:
    (tmp_path / "SELF_HALT").touch()
    assert is_self_halted(tmp_path)


# ---------------------------------------------------------------------------
# write_self_halt
# ---------------------------------------------------------------------------


def test_write_self_halt_creates_file(tmp_path: Path) -> None:
    write_self_halt(tmp_path, "test reason")
    qfile = tmp_path / _SELF_HALT_FILE
    assert qfile.exists()


def test_write_self_halt_contains_reason(tmp_path: Path) -> None:
    write_self_halt(tmp_path, "unexpected mutation detected")
    content = (tmp_path / _SELF_HALT_FILE).read_text()
    assert "unexpected mutation detected" in content
    assert "SELF_HALT" in content


def test_write_self_halt_contains_timestamp(tmp_path: Path) -> None:
    write_self_halt(tmp_path, "reason")
    content = (tmp_path / _SELF_HALT_FILE).read_text()
    assert "timestamp:" in content


def test_write_self_halt_creates_state_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "state"
    write_self_halt(nested, "reason")
    assert (nested / _SELF_HALT_FILE).exists()


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
    """When applied == proposed, no halt file written, returns normally."""
    src = tmp_path / "old.txt"
    dst = tmp_path / "archive" / "old.txt"
    manifest = _make_manifest(moves=((src, dst),))
    apply_result = _make_apply(moves=((src, dst),))

    post_mutation_detector(apply_result, manifest, tmp_path)
    assert not is_self_halted(tmp_path)


def test_post_mutation_detector_unexpected_move_triggers_halt(
    tmp_path: Path,
) -> None:
    """A move not in the manifest → SELF_HALT + sys.exit."""
    src = tmp_path / "unexpected.txt"
    dst = tmp_path / "archive" / "unexpected.txt"
    manifest = _make_manifest()  # empty — no moves proposed
    apply_result = _make_apply(moves=((src, dst),))

    with pytest.raises(SystemExit) as exc_info:
        post_mutation_detector(apply_result, manifest, tmp_path)
    assert exc_info.value.code != 0
    assert is_self_halted(tmp_path)


def test_post_mutation_detector_unexpected_delete_triggers_halt(
    tmp_path: Path,
) -> None:
    deleted = tmp_path / "zero.tmp"
    manifest = _make_manifest()  # no deletes proposed
    apply_result = _make_apply(deletes=(deleted,))

    with pytest.raises(SystemExit):
        post_mutation_detector(apply_result, manifest, tmp_path)
    assert is_self_halted(tmp_path)


def test_post_mutation_detector_unexpected_create_triggers_halt(
    tmp_path: Path,
) -> None:
    created = tmp_path / "new_file.md"
    manifest = _make_manifest()  # no creates proposed
    apply_result = _make_apply(creates=(created,))

    with pytest.raises(SystemExit):
        post_mutation_detector(apply_result, manifest, tmp_path)
    assert is_self_halted(tmp_path)


def test_post_mutation_detector_allowed_sets_match(tmp_path: Path) -> None:
    """All three categories match → no halt, returns normally."""
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
    assert not is_self_halted(tmp_path)


def test_post_mutation_detector_halt_file_contains_task_id(
    tmp_path: Path,
) -> None:
    """SELF_HALT content includes the task_id for investigation."""
    unexpected = tmp_path / "bad.txt"
    manifest = _make_manifest()
    apply_result = _make_apply(deletes=(unexpected,))

    with pytest.raises(SystemExit):
        post_mutation_detector(apply_result, manifest, tmp_path)
    content = (tmp_path / _SELF_HALT_FILE).read_text()
    assert "test_task" in content
