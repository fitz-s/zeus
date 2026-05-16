# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p3_topology_v_next_phase2_shadow/SCAFFOLD.md §5 probe4
"""
Probe 4 — SLICING_PRESSURE: friction_state is wired through to v_next.admit.

IMPORTANT: SLICING_PRESSURE issue emission is deferred to P2 packet per P1 SCAFFOLD §5.2.
The P1 implementation tracks friction_budget_used but does NOT yet emit a slicing_pressure
issue code. This probe tests the PLUMBING (friction_state passes through and
friction_budget_used increments) rather than the emission logic.

Kill criterion: across 3 calls sharing a mutable friction_state dict,
friction_budget_used in the third shadow envelope is >= 3 (incrementing per call).
If this assertion fails, friction_state is not being passed to v_next.admit by the shim.
"""
import pytest

from scripts.topology_v_next.cli_integration_shim import maybe_shadow_compare


FILES_A = ["src/calibration/platt.py", "tests/test_calibration_platt.py", "scripts/topology_doctor.py"]
FILES_B = ["src/calibration/platt.py", "tests/test_calibration_platt.py"]
FILES_C = ["src/calibration/platt.py"]

PAYLOAD = {
    "ok": True,
    "admission": {"status": "admitted"},
    "route_card": {},
    "task_blockers": [],
    "admission_blockers": [],
}


class TestProbe4SlicingPressurePlumbing:

    def test_friction_budget_increments_across_calls(self, monkeypatch):
        """friction_budget_used increases with each call sharing friction_state."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        friction_state: dict = {}

        r1 = maybe_shadow_compare(
            {**PAYLOAD}, task="call 1", files=FILES_A, intent="modify_existing",
            v_next_shadow=True, friction_state=friction_state,
        )
        r2 = maybe_shadow_compare(
            {**PAYLOAD}, task="call 2", files=FILES_B, intent="modify_existing",
            v_next_shadow=True, friction_state=friction_state,
        )
        r3 = maybe_shadow_compare(
            {**PAYLOAD}, task="call 3", files=FILES_C, intent="modify_existing",
            v_next_shadow=True, friction_state=friction_state,
        )

        fb1 = r1["v_next_shadow"]["friction_budget_used"]
        fb2 = r2["v_next_shadow"]["friction_budget_used"]
        fb3 = r3["v_next_shadow"]["friction_budget_used"]

        # Kill criterion: budget must increment
        assert fb1 < fb2 <= fb3, (
            f"SLICING_PRESSURE plumbing broken: friction_budget_used did not increment. "
            f"Got fb1={fb1}, fb2={fb2}, fb3={fb3}. "
            f"friction_state may not be passed through to v_next.admit."
        )
        assert fb3 >= 3, (
            f"friction_budget_used after 3 calls should be >= 3, got {fb3}"
        )

    def test_independent_calls_do_not_share_budget(self, monkeypatch):
        """Separate friction_state dicts do not share counts."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        fs1: dict = {}
        fs2: dict = {}

        r_a = maybe_shadow_compare(
            {**PAYLOAD}, task="call a1", files=FILES_A, intent="modify_existing",
            v_next_shadow=True, friction_state=fs1,
        )
        r_b = maybe_shadow_compare(
            {**PAYLOAD}, task="call b1", files=FILES_A, intent="modify_existing",
            v_next_shadow=True, friction_state=fs2,
        )

        # Both start at 1 since they have separate friction_state dicts
        assert r_a["v_next_shadow"]["friction_budget_used"] == 1
        assert r_b["v_next_shadow"]["friction_budget_used"] == 1

    def test_none_friction_state_does_not_crash(self, monkeypatch):
        """friction_state=None (default) is handled gracefully."""
        monkeypatch.setattr(
            "scripts.topology_v_next.cli_integration_shim.log_divergence",
            lambda record: None,
        )

        result = maybe_shadow_compare(
            {**PAYLOAD}, task="call no state", files=FILES_C, intent="modify_existing",
            v_next_shadow=True, friction_state=None,
        )
        shadow = result["v_next_shadow"]
        assert shadow.get("error") is None
        # With friction_state=None, friction_budget_used defaults to 1
        assert shadow["friction_budget_used"] == 1
