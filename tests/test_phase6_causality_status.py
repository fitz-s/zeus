"""Relationship tests for Phase 6 INV-16 causality_status enforcement.

Phase: 6 (Day0 low nowcast signal)
R-numbers covered: INV-16 (causality_status != 'OK' is a separate reject axis)

These tests encode future invariants that are out of Phase 3 scope.
They MUST FAIL until Phase 6 lands the nowcast path and evaluator causality gate.

Moved from test_phase3_observation_closure.py at Phase 3 critic review (CRITICAL-2):
the causality_status machinery belongs to Phase 6 (Day0 low nowcast), not Phase 3
(observation client low_so_far + source registry collapse).

Law: zeus_dual_track_architecture.md §5 + INV-16 — a causality_status != 'OK'
Day0 slot must be rejected before reaching any historical Platt lookup, even when
low_so_far is present. This is a SEPARATE reject axis from OBSERVATION_UNAVAILABLE_LOW.
"""
from __future__ import annotations

import unittest

import pytest


class TestCausalityStatusRejectAxis(unittest.TestCase):
    """INV-16: a low-track Day0 slot with causality_status != 'OK' must be
    rejected before reaching any Platt lookup — even when low_so_far is present.

    This is a SEPARATE reject axis from the OBSERVATION_UNAVAILABLE_LOW gate.
    The law (zeus_dual_track_architecture.md §5, INV-16) says:
      'Runtime code must not route such slots through a historical forecast Platt
      lookup. Low Day0 for a non-OK causality status must go through the nowcast
      path… If any of those inputs is missing, the decision is a clean reject,
      not a silent degrade to high path.'

    These tests MUST FAIL until Phase 6 because:
    1. Day0ObservationContext doesn't carry a causality_status field.
    2. The evaluator has no explicit causality_status gate for the low track.
    3. The string 'N/A_CAUSAL_DAY_ALREADY_STARTED' does not appear in evaluator.py
       as a branch condition.
    """

    def _import_context(self):
        try:
            from src.data.observation_client import Day0ObservationContext
            return Day0ObservationContext
        except ImportError:
            self.fail(
                "Day0ObservationContext not importable from observation_client"
            )

    def test_evaluator_has_causality_status_reject_gate_for_low_track(self):
        """INV-16: evaluator.py must contain an explicit causality_status guard
        for the low track, distinct from the low_so_far=None guard.

        After Phase 6, the evaluator must check causality_status and route
        N/A_CAUSAL_DAY_ALREADY_STARTED slots to the nowcast path, not Platt.

        This test fails until Phase 6 because the causality guard does not exist.
        """
        self._import_context()

        import src.engine.evaluator as ev_mod
        import inspect
        source = inspect.getsource(ev_mod)

        self.assertIn(
            "N/A_CAUSAL_DAY_ALREADY_STARTED",
            source,
            "evaluator.py must explicitly handle causality_status='N/A_CAUSAL_DAY_ALREADY_STARTED' "
            "for the low track (INV-16). This is a separate gate from low_so_far=None.",
        )

    def test_causality_status_reject_is_distinct_from_observation_unavailable(self):
        """INV-16: the evaluator must use a distinct rejection_stage for
        causality violations vs. observation unavailability.

        OBSERVATION_UNAVAILABLE_LOW = provider couldn't fetch low_so_far.
        CAUSAL_SLOT_NOT_OK = the Day0 slot is N/A_CAUSAL_DAY_ALREADY_STARTED
        and must not touch a historical Platt model.

        These are two separate failure modes; mixing them hides the cause from
        operators and the learning pipeline.
        """
        self._import_context()

        import src.engine.evaluator as ev_mod
        import inspect
        source = inspect.getsource(ev_mod)

        self.assertIn(
            "OBSERVATION_UNAVAILABLE_LOW",
            source,
            "evaluator must use OBSERVATION_UNAVAILABLE_LOW for missing low_so_far",
        )
        self.assertIn(
            "CAUSAL_SLOT_NOT_OK",
            source,
            "evaluator must use a distinct rejection_stage for causality_status != OK "
            "(INV-16). The label 'CAUSAL_SLOT_NOT_OK' or equivalent must be present.",
        )

    @pytest.mark.xfail(reason="P10E: Day0ObservationContext.causality_status field addition required; tracked by ticket")
    def test_day0_observation_context_carries_causality_status(self):
        """INV-16: Day0ObservationContext must carry causality_status so the
        evaluator can gate on it without re-querying the snapshot table.

        If the context does not carry causality_status, the evaluator has no
        choice but to either ignore it (INV-16 violation) or re-query the
        DB mid-evaluation (architectural seam violation).
        """
        ctx_cls = self._import_context()
        import dataclasses

        fields = {f.name for f in dataclasses.fields(ctx_cls)}
        self.assertIn(
            "causality_status",
            fields,
            "Day0ObservationContext must carry causality_status so the evaluator "
            "can enforce INV-16 without a secondary DB lookup",
        )


if __name__ == "__main__":
    unittest.main()
