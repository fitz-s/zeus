# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/evidence/deadloop_2026-06-14/qlcb_suppression.md + operator RULE 1.
#   The K3 settlement-coverage gate measured CLIMATOLOGY (one constant claimed_q_lcb stamped
#   on every settled day -> isotonic degenerates to np.mean(ys) = unconditional bin base
#   rate), then shrank any forecast concentrating above that base rate to base_rate-0.01 —
#   crushing real, market-corroborated edge to climatology (Singapore high 31C: +0.060 ev/$
#   -> -0.730 ev/$). This module pins the REBUILT calibration semantics: the gate now
#   measures (per-day ACTUAL claimed q_lcb, realized 0/1) calibration, INSUFFICIENT_DATA is
#   LICENSED-by-default (inert + non-blocking), UNLICENSED fires ONLY on PROVEN
#   overconfidence, and the shrink stays one-directional (no UP arm — P3 KILL N7).
"""RED-on-revert tests for the settlement-coverage CALIBRATION rebuild.

Each test states the RED it would produce against the pre-rebuild code:
  * CALIBRATED forecast (per-day claimed ~= realized): the OLD climatology code SHRINKS it
    to the bin base rate; the rebuilt code leaves it UNCHANGED (LICENSED).
  * INSUFFICIENT_DATA / absent per-day claim history: q_lcb UNCHANGED and ARM NOT blocked
    (the OLD arm gate BLOCKED on coverage_ratio is None).
  * PROVEN OVERCONFIDENT (enough days, realized << claimed): UNLICENSED -> shrunk + ARM
    blocks (honest protection preserved, both old and new).
  * CONSERVATIVE (realized ABOVE claimed, ratio > 1): LICENSED + ARM NOT blocked (the OLD
    arm gate BLOCKED on |ratio-1| >= 0.10, wrongly refusing a conservative band).
  * The ARM gate consumes the corrected verdict (no block on INSUFFICIENT_DATA; block on
    proven UNLICENSED).
"""
from __future__ import annotations

import random

import pytest

from src.calibration.settlement_backward_coverage import (
    CoverageObservation,
    apply_settlement_coverage,
    arm_gate_coverage_blocks,
    settlement_backward_coverage_check,
)


def _calibrated_stream(n: int = 40, seed: int = 7) -> list[CoverageObservation]:
    """Per-day claimed bands VARY day to day; realized rate tracks the claim (calibrated).

    The KEY structural property vs the climatology defect: the claimed q_lcb is NOT a single
    constant — it varies per settled day, so the isotonic reads a genuine claimed->realized
    curve instead of degenerating to the pooled bin base rate.
    """
    rng = random.Random(seed)
    out: list[CoverageObservation] = []
    for _ in range(n):
        claimed = round(rng.uniform(0.45, 0.55), 4)
        # realized tracks the claim (~0.50): a calibrated / mildly conservative band.
        out.append(CoverageObservation(q_lcb=claimed, won=rng.random() < 0.52))
    return out


# ---------------------------------------------------------------------------
# 1. CALIBRATED forecast -> LICENSED -> q_lcb UNCHANGED.
#    OLD climatology code shrank it to the bin base rate => RED on revert.
# ---------------------------------------------------------------------------
def test_calibrated_forecast_is_licensed_and_unchanged():
    obs = _calibrated_stream()
    verdict = settlement_backward_coverage_check(
        city="Singapore", metric="high", season="summer",
        q_lcb=0.5088, observations=obs, min_n=30,
    )
    out = apply_settlement_coverage(q_lcb=0.5088, verdict=verdict)
    blocked, _reason = arm_gate_coverage_blocks(verdict)

    assert verdict.status == "LICENSED"
    # The whole point: a calibrated, concentrated forecast is NOT shrunk to climatology.
    assert out == pytest.approx(0.5088)
    assert blocked is False


def test_climatology_constant_claim_would_shrink_calibrated_forecast_RED():
    """RED-on-revert anchor: the OLD live builder stamped ONE constant claimed band on
    every settled day, so the isotonic returned the unconditional bin base rate and the
    verdict came back UNLICENSED -> shrink. We reproduce that DEGENERATE input here and
    assert the verdict shrinks it — documenting the exact behavior the rebuild removes.

    Singapore high 31C: claimed 0.5088, 12 wins / 86 pooled settled days => base rate
    0.1395 => shrink target 0.1295 (== the live log's 0.129535)."""
    constant_claim_climatology = [
        CoverageObservation(q_lcb=0.5088, won=(i < 12)) for i in range(86)
    ]
    verdict = settlement_backward_coverage_check(
        city="Singapore", metric="high", season="summer",
        q_lcb=0.5088, observations=constant_claim_climatology, min_n=30,
    )
    out = apply_settlement_coverage(q_lcb=0.5088, verdict=verdict)
    # This is the SUPPRESSION: a constant-claim (climatology) stream shrinks the real
    # forecast to base_rate - 0.01. The rebuilt OBSERVATION BUILDER never produces a
    # constant-claim stream (it carries per-day actual claims), so this degenerate input
    # cannot occur on the live path post-rebuild — but the verdict math, given a genuinely
    # overconfident pooled rate, still shrinks (which is correct for PROVEN overconfidence).
    assert verdict.status == "UNLICENSED"
    assert out == pytest.approx(0.1295, abs=1e-4)
    # Contrast: the CALIBRATED per-day stream above is LICENSED + unchanged. Same q_lcb,
    # opposite verdict — the difference is per-day claims vs one constant (climatology).


# ---------------------------------------------------------------------------
# 2. INSUFFICIENT_DATA / absent per-day claim history -> UNCHANGED + ARM NOT blocked.
#    OLD arm gate BLOCKED on coverage_ratio is None => RED on revert.
# ---------------------------------------------------------------------------
def test_insufficient_data_is_inert_and_arm_not_blocked():
    thin = [CoverageObservation(q_lcb=0.51, won=True) for _ in range(5)]
    verdict = settlement_backward_coverage_check(
        city="Singapore", metric="high", season="summer",
        q_lcb=0.5088, observations=thin, min_n=30,
    )
    out = apply_settlement_coverage(q_lcb=0.5088, verdict=verdict)
    blocked, _reason = arm_gate_coverage_blocks(verdict)

    assert verdict.status == "INSUFFICIENT_DATA"
    assert out == pytest.approx(0.5088)  # never shrink on thin data
    assert blocked is False  # RULE 1: lack of data is NOT proof of overconfidence


def test_absent_claim_history_is_insufficient_data():
    """An EMPTY observation stream (no per-day claim history at all — the live state today
    where per-band settled-claim-days < min_n everywhere) is INSUFFICIENT_DATA, inert,
    non-blocking."""
    verdict = settlement_backward_coverage_check(
        city="Singapore", metric="high", season="summer",
        q_lcb=0.5088, observations=[], min_n=30,
    )
    out = apply_settlement_coverage(q_lcb=0.5088, verdict=verdict)
    blocked, _reason = arm_gate_coverage_blocks(verdict)
    assert verdict.status == "INSUFFICIENT_DATA"
    assert out == pytest.approx(0.5088)
    assert blocked is False


# ---------------------------------------------------------------------------
# 3. PROVEN OVERCONFIDENT -> UNLICENSED -> shrunk + ARM blocks (honest protection kept).
# ---------------------------------------------------------------------------
def test_proven_overconfident_is_unlicensed_shrunk_and_arm_blocks():
    rng = random.Random(11)
    over = [
        CoverageObservation(q_lcb=round(rng.uniform(0.88, 0.92), 4), won=rng.random() < 0.55)
        for _ in range(40)
    ]
    verdict = settlement_backward_coverage_check(
        city="X", metric="high", season="summer",
        q_lcb=0.90, observations=over, min_n=30,
    )
    out = apply_settlement_coverage(q_lcb=0.90, verdict=verdict)
    blocked, reason = arm_gate_coverage_blocks(verdict)

    assert verdict.status == "UNLICENSED"
    assert out < 0.90  # the shrink fired (honest, proven overconfidence)
    assert blocked is True
    assert "UNLICENSED" in reason


# ---------------------------------------------------------------------------
# 4. CONSERVATIVE (realized ABOVE claimed, ratio > 1) -> LICENSED + ARM NOT blocked.
#    OLD arm gate BLOCKED on |ratio-1| >= 0.10 => RED on revert (it refused a band the
#    settled record OVER-backs — the opposite of overconfident).
# ---------------------------------------------------------------------------
def test_conservative_band_ratio_above_one_is_licensed_and_not_blocked():
    rng = random.Random(13)
    cons = [
        CoverageObservation(q_lcb=round(rng.uniform(0.40, 0.50), 4), won=rng.random() < 0.70)
        for _ in range(40)
    ]
    verdict = settlement_backward_coverage_check(
        city="Y", metric="high", season="summer",
        q_lcb=0.45, observations=cons, min_n=30,
    )
    blocked, _reason = arm_gate_coverage_blocks(verdict)

    assert verdict.status == "LICENSED"
    assert verdict.coverage_ratio is not None and verdict.coverage_ratio > 1.0
    assert blocked is False  # a conservative band is exactly what we WANT to arm on


# ---------------------------------------------------------------------------
# 5. The ARM gate consumes the corrected verdict end-to-end (the consumer contract).
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status,ratio,expect_blocked",
    [
        ("UNLICENSED", 0.70, True),          # proven overconfident -> block
        ("INSUFFICIENT_DATA", None, False),  # thin -> NOT block (rebuild)
        ("LICENSED", 1.00, False),           # calibrated -> NOT block
        ("LICENSED", 1.25, False),           # conservative (ratio>1) -> NOT block (rebuild)
    ],
)
def test_arm_gate_blocks_only_on_proven_overconfidence(status, ratio, expect_blocked):
    from src.calibration.settlement_backward_coverage import CoverageVerdict

    verdict = CoverageVerdict(
        status=status,
        q_lcb_in=0.90 if status == "UNLICENSED" else 0.50,
        q_lcb_out=0.65 if status == "UNLICENSED" else 0.50,
        n_settlement_observations=40 if ratio is not None else 5,
        coverage_ratio=ratio,
        realized_win_rate=(ratio * 0.50) if ratio is not None else None,
    )
    blocked, _reason = arm_gate_coverage_blocks(verdict)
    assert blocked is expect_blocked
