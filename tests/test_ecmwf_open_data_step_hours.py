# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: fix/#134 — extend ECMWF Opendata STEP_HOURS to D+10 (steps 228-252)
"""STEP_HOURS coverage contract tests for ecmwf_open_data — fix/#134.

Verifies that:
1. STEP_HOURS includes all steps 228-252h required for D+10 readiness rows.
2. Max step is 282h (LOW 282h authority).
3. The 3h stride is maintained throughout.
4. evaluate_horizon_coverage passes for all D+10 required steps given live_max=282.
"""

from __future__ import annotations

import pytest

from src.data.ecmwf_open_data import STEP_HOURS
from src.data.forecast_target_contract import evaluate_horizon_coverage

# D+10 samples from the dossier (cities that were BLOCKED):
# AMSTERDAM: expected_steps=[228,234,240,246,252]
# ANKARA:    expected_steps=[228,234,240,246,252]
# BEIJING:   expected_steps=[222,228,234,240,246]
D10_SAMPLE_STEPS = [
    (228, 234, 240, 246, 252),  # AMSTERDAM / ANKARA
    (222, 228, 234, 240, 246),  # BEIJING
]


def test_step_hours_includes_d10_range() -> None:
    """All steps in [228, 252] at 6h stride must be in STEP_HOURS (D+10 coverage)."""
    step_set = set(STEP_HOURS)
    for step in range(228, 253, 6):
        assert step in step_set, (
            f"STEP_HOURS missing step {step}h — required for D+10 readiness rows. "
            f"Max STEP_HOURS={max(STEP_HOURS)}"
        )


def test_step_hours_max_is_282() -> None:
    """STEP_HOURS must reach 282h to satisfy LOW 282h authority (fix/#134)."""
    assert max(STEP_HOURS) == 282, (
        f"Expected max(STEP_HOURS)=282, got {max(STEP_HOURS)}. "
        "LOW D+10 authority requires coverage to 282h."
    )


def test_step_hours_stride_is_3h() -> None:
    """All consecutive STEP_HOURS must differ by 3h (A1+3h authority)."""
    steps = sorted(STEP_HOURS)
    for i in range(1, len(steps)):
        assert steps[i] - steps[i - 1] == 3, (
            f"STEP_HOURS stride broken at index {i}: "
            f"{steps[i-1]}h → {steps[i]}h (expected 3h step)"
        )


def test_step_hours_starts_at_3() -> None:
    """STEP_HOURS must begin at 3h (A1+3h authority — no step 0 or 6 as first)."""
    assert STEP_HOURS[0] == 3


@pytest.mark.parametrize("required_steps", D10_SAMPLE_STEPS)
def test_evaluate_horizon_coverage_passes_for_d10_with_live_max_282(
    required_steps: tuple[int, ...],
) -> None:
    """evaluate_horizon_coverage must return LIVE_ELIGIBLE for D+10 steps at live_max=282."""
    decision = evaluate_horizon_coverage(
        required_steps=required_steps,
        live_max_step_hours=282,
    )
    assert decision.status == "LIVE_ELIGIBLE", (
        f"Expected LIVE_ELIGIBLE for required_steps={required_steps} "
        f"with live_max=282, got {decision.status}: {decision.reason_codes}"
    )


def test_evaluate_horizon_coverage_blocks_above_282() -> None:
    """evaluate_horizon_coverage must block when required steps exceed 282h."""
    decision = evaluate_horizon_coverage(
        required_steps=(288,),
        live_max_step_hours=282,
    )
    assert decision.status == "BLOCKED"
    assert "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in decision.reason_codes


def test_evaluate_horizon_coverage_old_240_limit_blocks_d10() -> None:
    """Under the old 240h limit, D+10 steps (246, 252) must be BLOCKED — regression anchor."""
    decision = evaluate_horizon_coverage(
        required_steps=(228, 234, 240, 246, 252),
        live_max_step_hours=240,
    )
    assert decision.status == "BLOCKED", (
        "Old 240h limit should have blocked these steps — "
        "this test anchors that the 276→282h extension is load-bearing."
    )
