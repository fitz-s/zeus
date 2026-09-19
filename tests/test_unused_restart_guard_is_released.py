"""A guard whose restart never ran must not stop entries forever.

`deploy_live.py` arms the durable entry pause BEFORE the obligation gate, because
that gate requires the pause witness and testing it first would refuse
circularly. When the gate then refuses, the restart does not happen -- and the
guard cannot clear itself, because `prove_deploy_live_restart_guard` goes green
only once the runtime serves `expected_sha`, which only a restart achieves.
On 2026-09-18 that left entries paused indefinitely while capital-recovery
blockers grew, with the fix for the underlying stall committed but unloadable.
"""

from __future__ import annotations

import pytest

from src.control import control_plane
from src.state.db import apply_architecture_kernel_schema, get_world_connection


@pytest.fixture(autouse=True)
def _bootstrap_world_schema():
    """The TI-1 autouse redirect points the world DB at a per-test mirror.

    That file is empty until the kernel schema is applied, so every control
    override write needs this first. Same shape as
    tests/test_pause_entries_precedence.py.
    """

    conn = get_world_connection()
    apply_architecture_kernel_schema(conn)
    conn.commit()
    conn.close()
    yield


def _arm(sha: str = "a" * 40):
    result = control_plane.arm_deploy_live_restart_guard(sha)
    assert result["status"] == "armed", result
    return control_plane.get_active_deploy_live_restart_guard()


class TestReleaseUnusedGuard:
    def test_release_clears_the_pause_the_refused_restart_armed(self):
        witness = _arm()
        assert control_plane.is_entries_paused() is True

        result = control_plane.release_unused_deploy_live_restart_guard(witness)

        assert result["status"] == "released"
        assert control_plane.is_entries_paused() is False
        assert control_plane.get_active_deploy_live_restart_guard() is None

    def test_release_needs_no_runtime_proof(self):
        """There is no green proof to be had for a restart that never ran."""

        witness = _arm()
        proof = control_plane.prove_deploy_live_restart_guard(witness)
        assert proof["green"] is not True

        assert (
            control_plane.release_unused_deploy_live_restart_guard(witness)["status"]
            == "released"
        )

    def test_a_newer_guard_generation_is_left_selected(self):
        stale = _arm("a" * 40)
        newer = control_plane.arm_deploy_live_restart_guard("b" * 40)
        assert newer["status"] == "armed"

        result = control_plane.release_unused_deploy_live_restart_guard(stale)

        assert result["status"] == "noop"
        assert control_plane.is_entries_paused() is True

    def test_releasing_twice_is_a_noop(self):
        witness = _arm()
        assert (
            control_plane.release_unused_deploy_live_restart_guard(witness)["status"]
            == "released"
        )
        second = control_plane.release_unused_deploy_live_restart_guard(witness)
        assert second["status"] == "noop"

    def test_an_operator_pause_is_never_released(self):
        """arm() preserves an operator pause; release must not undo one."""

        control_plane.pause_entries(
            "operator halt",
            issued_by="operator",
        )
        armed = control_plane.arm_deploy_live_restart_guard("c" * 40)
        assert armed["status"] == "preserved"
        assert control_plane.get_active_deploy_live_restart_guard() is None
        assert control_plane.is_entries_paused() is True
