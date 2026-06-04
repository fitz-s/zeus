# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3 (q_lcb settlement-backward-coverage). K3 root: q_lcb
#   was never settlement-grounded (identity-Platt, 0 rows) -> the live LCB ran
#   ~26pt overconfident. The fix: read the REALIZED settlement win-rate in the band
#   a q_lcb claims, isotonic-calibrate it, and refuse to license an LCB that the
#   settled record does not back. Truth = settlement_outcomes graded ONLY through
#   the spine grade_receipt (src.contracts.graded_receipt) — NOT a fresh string
#   join (which would re-introduce the startswith('no_'/'below') mis-grade D1 killed).
#   Direction Law: buy_yes WIN iff settled_bin==traded_bin; buy_no WIN iff !=.
"""Relationship tests for settlement_backward_coverage_check().

RELATIONSHIP under test: an LCB the forecast bootstrap CLAIMS (q_lcb=0.94 = "at
least 94% of the time the NO mass is realized") flows across the settlement
boundary into the SETTLED record. The property that must hold: realized win-rate
in that band >= the claimed band, within coverage tolerance. When the settled
record says only 66% realized, the 0.94 claim is UNLICENSED and the LCB is shrunk
to the realized rate minus a 1pp honesty margin. The verdict is built from
GradedReceipt verdicts (the spine truth fn), never from a bare value-equality join.

Written RED-first: src.calibration.settlement_backward_coverage does not exist yet.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# A graded observation = (q_lcb the receipt claimed at decision time, won bool
# from grade_receipt). The coverage check consumes a stream of these, all in the
# same (city, metric, season) cohort. The `won` field comes from grade_receipt —
# the test builds it via the real spine fn so the relationship crosses the real
# grading boundary, not a hand-set bool.
# ---------------------------------------------------------------------------
from src.contracts.graded_receipt import grade_receipt
from src.contracts.settlement_semantics import SettlementSemantics
from src.types.market import Bin


class _FakeSettlement:
    def __init__(self, settlement_value: float, settlement_unit: str):
        self.settlement_value = settlement_value
        self.settlement_unit = settlement_unit


def _graded_won(bin_low, bin_high, unit, label, direction, settled_value) -> bool:
    """Grade ONE observation through the real spine grade_receipt -> won bool.

    This is the load-bearing line: realized win/loss is decided by grade_receipt,
    so the coverage table inherits the Direction Law + BinKind + unit antibodies.
    """
    b = Bin(low=bin_low, high=bin_high, unit=unit, label=label)
    sem = (
        SettlementSemantics.default_wu_fahrenheit("KTST")
        if unit == "F"
        else SettlementSemantics.default_wu_celsius("CTST")
    )
    s = _FakeSettlement(settlement_value=settled_value, settlement_unit=unit)
    return grade_receipt(b, direction, s, semantics=sem).won


def _band_obs(q_lcb: float, won: bool):
    """A coverage observation: the LCB the receipt claimed + the graded outcome."""
    from src.calibration.settlement_backward_coverage import CoverageObservation

    return CoverageObservation(q_lcb=q_lcb, won=won)


# ---------------------------------------------------------------------------
# LICENSED — realized win-rate matches the claimed band: q_lcb unchanged.
# ---------------------------------------------------------------------------
def test_coverage_licensed_leaves_qlcb_unchanged():
    """A claimed band of 0.70 with realized win-rate ~0.71 across 40 settled
    observations is LICENSED: the settled record backs the claim, so q_lcb is
    returned unchanged with calibration_source SETTLEMENT_ISOTONIC."""
    from src.calibration.settlement_backward_coverage import (
        settlement_backward_coverage_check,
    )

    # 40 observations in a 0.70 band; 29/40 = 0.725 realized (>= 0.70 - tol).
    obs = [_band_obs(0.70, won=(i < 29)) for i in range(40)]
    verdict = settlement_backward_coverage_check(
        city="Tokyo", metric="high", season="JJA", q_lcb=0.70,
        observations=obs, min_n=30,
    )
    assert verdict.status == "LICENSED"
    assert verdict.q_lcb_out == pytest.approx(0.70)
    assert verdict.n_settlement_observations == 40


# ---------------------------------------------------------------------------
# UNLICENSED — the headline case: claimed 0.94, realized 0.66 -> shrink to 0.65.
# ---------------------------------------------------------------------------
def test_coverage_unlicensed_shrinks_to_realized_minus_1pp():
    """q_lcb=0.94 claimed, but realized win-rate is 0.66 across 50 settled
    observations. The settled record does NOT back 0.94 -> UNLICENSED, and the
    LCB is shrunk to realized - 1pp = 0.65, source SETTLEMENT_ISOTONIC."""
    from src.calibration.settlement_backward_coverage import (
        settlement_backward_coverage_check,
    )

    # 50 obs, 33/50 = 0.66 realized.
    obs = [_band_obs(0.94, won=(i < 33)) for i in range(50)]
    verdict = settlement_backward_coverage_check(
        city="Tel Aviv", metric="high", season="JJA", q_lcb=0.94,
        observations=obs, min_n=30,
    )
    assert verdict.status == "UNLICENSED"
    assert verdict.q_lcb_out == pytest.approx(0.65, abs=1e-9)
    assert verdict.realized_win_rate == pytest.approx(0.66, abs=1e-9)
    assert verdict.calibration_source == "SETTLEMENT_ISOTONIC"


# ---------------------------------------------------------------------------
# INSUFFICIENT_DATA — fewer than min_n settled obs: unchanged + WARN.
# ---------------------------------------------------------------------------
def test_coverage_insufficient_data_leaves_qlcb_unchanged_and_warns(caplog):
    """Only 12 settled observations (< min_n=30). The settled record cannot
    confirm OR refute the claim -> INSUFFICIENT_DATA: q_lcb is unchanged (the MC
    LCB stands) and a WARN is logged. Refusing to act on thin data is the honest
    direction (we do NOT shrink on noise, nor license on noise)."""
    import logging

    from src.calibration.settlement_backward_coverage import (
        settlement_backward_coverage_check,
    )

    obs = [_band_obs(0.94, won=(i < 8)) for i in range(12)]  # 8/12 but n<30
    with caplog.at_level(logging.WARNING):
        verdict = settlement_backward_coverage_check(
            city="Wellington", metric="high", season="DJF", q_lcb=0.94,
            observations=obs, min_n=30,
        )
    assert verdict.status == "INSUFFICIENT_DATA"
    assert verdict.q_lcb_out == pytest.approx(0.94)  # unchanged
    assert any("INSUFFICIENT" in r.message or "insufficient" in r.message.lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# RELATIONSHIP — the verdict is built from grade_receipt verdicts. The UNLICENSED
# losses are exactly the buy_no-on-the-settled-bin cases the Direction Law marks
# as losses. This proves the coverage table inherits the spine grading, not a
# hand-rolled string compare.
# ---------------------------------------------------------------------------
def test_coverage_losses_are_grade_receipt_direction_law_losses():
    """Build the win/loss stream through the REAL grade_receipt: a buy_no on the
    65°F bin LOSES iff the settlement lands in 64-65°F. A coverage cohort built
    from these real verdicts must shrink when too many buy_no's hit the settled
    bin — the exact failure mode #135 targets."""
    from src.calibration.settlement_backward_coverage import (
        settlement_backward_coverage_check,
    )

    obs = []
    # 30 buy_no on "64-65°F": 20 settle elsewhere (WIN), 10 settle IN-bin (LOSS).
    for i in range(30):
        settled = 70.0 if i < 20 else 64.0  # in-bin 64-65 for the last 10
        won = _graded_won(64.0, 65.0, "F", "64-65°F", "buy_no", settled)
        obs.append(_band_obs(0.90, won=won))
    # realized = 20/30 = 0.6667; claimed 0.90 -> UNLICENSED, shrink to ~0.657.
    verdict = settlement_backward_coverage_check(
        city="San Francisco", metric="high", season="JJA", q_lcb=0.90,
        observations=obs, min_n=30,
    )
    assert verdict.status == "UNLICENSED"
    assert verdict.realized_win_rate == pytest.approx(20.0 / 30.0, abs=1e-9)
    assert verdict.q_lcb_out == pytest.approx(20.0 / 30.0 - 0.01, abs=1e-9)
