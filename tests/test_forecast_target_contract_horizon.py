# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: fix/#134 — evaluate_horizon_coverage contract for D+10 step range
"""evaluate_horizon_coverage relationship tests — fix/#134.

Verifies the horizon coverage predicate correctly gates D+10 steps
against the new 282h live_max ceiling from source_release_calendar.yaml.
"""

from __future__ import annotations

import pytest

from src.data.forecast_target_contract import evaluate_horizon_coverage, LIVE_ELIGIBLE, BLOCKED


class TestEvaluateHorizonCoverageD10:
    """Relationship: live_max_step_hours=282 covers all D+10 required steps."""

    def test_passes_for_max_step_252(self) -> None:
        """Step 252h (UTC+12 D+10 boundary) must be LIVE_ELIGIBLE at live_max=282."""
        decision = evaluate_horizon_coverage(
            required_steps=(246, 252),
            live_max_step_hours=282,
        )
        assert decision.status == LIVE_ELIGIBLE

    def test_passes_for_max_step_246(self) -> None:
        """Step 246h must be LIVE_ELIGIBLE at live_max=282."""
        decision = evaluate_horizon_coverage(
            required_steps=(240, 246),
            live_max_step_hours=282,
        )
        assert decision.status == LIVE_ELIGIBLE

    def test_passes_for_max_step_282(self) -> None:
        """Max boundary step 282h must be LIVE_ELIGIBLE at live_max=282."""
        decision = evaluate_horizon_coverage(
            required_steps=(276, 282),
            live_max_step_hours=282,
        )
        assert decision.status == LIVE_ELIGIBLE

    def test_blocks_at_step_283(self) -> None:
        """Step 283h exceeds live_max=282 and must be BLOCKED."""
        decision = evaluate_horizon_coverage(
            required_steps=(283,),
            live_max_step_hours=282,
        )
        assert decision.status == BLOCKED
        assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes

    def test_blocks_when_required_steps_empty(self) -> None:
        """Empty required_steps must be BLOCKED with MISSING_REQUIRED_STEPS."""
        decision = evaluate_horizon_coverage(
            required_steps=(),
            live_max_step_hours=282,
        )
        assert decision.status == BLOCKED
        assert "MISSING_REQUIRED_STEPS" in decision.reason_codes

    @pytest.mark.parametrize("step", [246, 252])
    def test_old_240_ceiling_blocked_these_steps(self, step: int) -> None:
        """Regression: steps ≥246 were BLOCKED under old 240h ceiling (pre fix/#134)."""
        decision = evaluate_horizon_coverage(
            required_steps=(step,),
            live_max_step_hours=240,
        )
        assert decision.status == BLOCKED, (
            f"Step {step}h should have been blocked under old 240h limit. "
            "This test anchors that fix/#134 is load-bearing."
        )
