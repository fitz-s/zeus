# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3 SHADOW FLAG (edli.q_lcb_settlement_coverage_gate_enabled,
#   default FALSE) + ARM-gate coverage predicate. Rule-6 (overconfidence=ruin) made
#   structural: the settlement-coverage SHRINK is HIGH risk, so it is gated OFF by
#   default and the coverage table is computed-but-not-applied. With the flag OFF the
#   live q_lcb is byte-identical to today. The ARM gate, however, reads the coverage
#   verdict UNCONDITIONALLY (an arm decision must never be made on an UNLICENSED LCB).
"""Flag-gating + ARM-gate relationship tests for K3 settlement-coverage.

Two relationships:
  1. SHADOW SAFETY: flag OFF -> the coverage shrink is NOT applied; the q_lcb the
     consumer reads equals the pre-coverage (legacy) q_lcb, source unchanged. This
     is the "byte-identical to today" contract for the live decision.
  2. ARM SAFETY: the ARM gate blocks when ANY traded cohort's coverage verdict is
     UNLICENSED, or when coverage_ratio is None / |ratio-1| >= 0.10 — independent
     of the shrink flag. Arming on an LCB the settled record refuses is forbidden.

Written RED-first.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 1. SHADOW SAFETY — flag OFF leaves q_lcb byte-identical (no shrink applied).
# ---------------------------------------------------------------------------
def test_apply_coverage_flag_off_is_byte_identical():
    """apply_settlement_coverage(..., enabled=False) returns the INPUT q_lcb
    unchanged even when the coverage verdict is UNLICENSED — the shrink is
    computed but NOT applied. The live decision is byte-identical to legacy."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        apply_settlement_coverage,
    )

    # An UNLICENSED verdict that WOULD shrink 0.94 -> 0.65 if applied.
    verdict = CoverageVerdict(
        status="UNLICENSED",
        q_lcb_in=0.94,
        q_lcb_out=0.65,
        n_settlement_observations=50,
        coverage_ratio=0.70,
        realized_win_rate=0.66,
        calibration_source="SETTLEMENT_ISOTONIC",
    )
    out = apply_settlement_coverage(q_lcb=0.94, verdict=verdict, enabled=False)
    assert out == pytest.approx(0.94)  # FLAG OFF -> unchanged, byte-identical


def test_apply_coverage_flag_on_applies_shrink():
    """With the flag ON, the same UNLICENSED verdict shrinks the q_lcb to the
    verdict's q_lcb_out (0.65). Flag ON is the only way the live LCB moves."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        apply_settlement_coverage,
    )

    verdict = CoverageVerdict(
        status="UNLICENSED",
        q_lcb_in=0.94,
        q_lcb_out=0.65,
        n_settlement_observations=50,
        coverage_ratio=0.70,
        realized_win_rate=0.66,
        calibration_source="SETTLEMENT_ISOTONIC",
    )
    out = apply_settlement_coverage(q_lcb=0.94, verdict=verdict, enabled=True)
    assert out == pytest.approx(0.65)


def test_apply_coverage_flag_on_licensed_is_unchanged():
    """Flag ON + LICENSED verdict -> q_lcb unchanged (the settled record backs the
    claim; nothing to shrink). Only UNLICENSED moves the number."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        apply_settlement_coverage,
    )

    verdict = CoverageVerdict(
        status="LICENSED",
        q_lcb_in=0.70,
        q_lcb_out=0.70,
        n_settlement_observations=40,
        coverage_ratio=1.03,
        realized_win_rate=0.725,
        calibration_source="SETTLEMENT_ISOTONIC",
    )
    out = apply_settlement_coverage(q_lcb=0.70, verdict=verdict, enabled=True)
    assert out == pytest.approx(0.70)


def test_apply_coverage_flag_on_insufficient_is_unchanged():
    """Flag ON + INSUFFICIENT_DATA -> unchanged. We never shrink on thin data."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        apply_settlement_coverage,
    )

    verdict = CoverageVerdict(
        status="INSUFFICIENT_DATA",
        q_lcb_in=0.94,
        q_lcb_out=0.94,
        n_settlement_observations=12,
        coverage_ratio=None,
        realized_win_rate=None,
        calibration_source="SETTLEMENT_ISOTONIC",
    )
    out = apply_settlement_coverage(q_lcb=0.94, verdict=verdict, enabled=True)
    assert out == pytest.approx(0.94)


# ---------------------------------------------------------------------------
# 2. ARM SAFETY — the ARM gate blocks on UNLICENSED / bad coverage_ratio,
#    INDEPENDENT of the shrink flag (you cannot arm on an LCB the settled record
#    refuses, even while the live shrink is still shadowed OFF).
# ---------------------------------------------------------------------------
def test_arm_gate_blocks_on_unlicensed():
    """An UNLICENSED coverage verdict blocks the ARM gate."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        arm_gate_coverage_blocks,
    )

    verdict = CoverageVerdict(
        status="UNLICENSED",
        q_lcb_in=0.94, q_lcb_out=0.65,
        n_settlement_observations=50, coverage_ratio=0.70,
        realized_win_rate=0.66, calibration_source="SETTLEMENT_ISOTONIC",
    )
    blocked, reason = arm_gate_coverage_blocks(verdict)
    assert blocked is True
    assert "UNLICENSED" in reason


def test_arm_gate_blocks_on_coverage_ratio_none():
    """coverage_ratio None (no settled backing) blocks the ARM gate — the gate
    requires coverage_ratio is not None and |ratio-1| < 0.10."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        arm_gate_coverage_blocks,
    )

    verdict = CoverageVerdict(
        status="INSUFFICIENT_DATA",
        q_lcb_in=0.94, q_lcb_out=0.94,
        n_settlement_observations=12, coverage_ratio=None,
        realized_win_rate=None, calibration_source="SETTLEMENT_ISOTONIC",
    )
    blocked, reason = arm_gate_coverage_blocks(verdict)
    assert blocked is True
    assert "coverage_ratio" in reason


def test_arm_gate_blocks_on_coverage_ratio_far_from_one():
    """coverage_ratio with |ratio-1| >= 0.10 blocks the ARM gate even if LICENSED-
    shaped — the band is mis-calibrated by more than the 10% tolerance."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        arm_gate_coverage_blocks,
    )

    verdict = CoverageVerdict(
        status="LICENSED",
        q_lcb_in=0.70, q_lcb_out=0.70,
        n_settlement_observations=40, coverage_ratio=1.25,  # |1.25-1|=0.25 > 0.10
        realized_win_rate=0.875, calibration_source="SETTLEMENT_ISOTONIC",
    )
    blocked, reason = arm_gate_coverage_blocks(verdict)
    assert blocked is True
    assert "coverage_ratio" in reason


def test_arm_gate_passes_on_licensed_in_tolerance():
    """A LICENSED verdict with coverage_ratio within 10% of 1.0 does NOT block."""
    from src.calibration.settlement_backward_coverage import (
        CoverageVerdict,
        arm_gate_coverage_blocks,
    )

    verdict = CoverageVerdict(
        status="LICENSED",
        q_lcb_in=0.70, q_lcb_out=0.70,
        n_settlement_observations=40, coverage_ratio=1.03,  # |1.03-1|=0.03 < 0.10
        realized_win_rate=0.721, calibration_source="SETTLEMENT_ISOTONIC",
    )
    blocked, reason = arm_gate_coverage_blocks(verdict)
    assert blocked is False
    assert reason == ""


# ---------------------------------------------------------------------------
# 3. (REMOVED 2026-06-03, Phase-2 K3 adversarial-verify) The former
#    ``k_cov_from_settlement_coverage`` fold helper and its four tests are removed.
#    The function had ZERO live callers (the "fold into EMOS k_cov gated by the K3
#    flag" claim was false). The live settlement-coverage mechanism is the per-
#    (bin,direction) shrink in _maybe_apply_settlement_coverage_to_lcb; folding a
#    per-(bin,direction) coverage_ratio into the per-FAMILY EMOS k_cov is ill-defined
#    and would double-apply settlement coverage on top of the shrink. One mechanism,
#    not two. See src/calibration/emos_ci_license.py for the removal rationale.
# ---------------------------------------------------------------------------
