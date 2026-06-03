# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: Task #135 mainstream-forecast direction-agreement gate
#   (/tmp/arm-truth.md keystone finding); DIRECTION LAW
#   (feedback_buy_direction_semantic); operator ARM criterion. These are
#   RELATIONSHIP tests (cross-module invariant: OUR forecast point flows into
#   the gate alongside an INDEPENDENT mainstream point and the traded bin's
#   direction — the property that must hold across that boundary is that a
#   bias-warped forecast can never produce an arm-eligible candidate).
"""Relationship + function tests for the mainstream-agreement gate (#135).

The gate is the K-structural antibody that makes the cold/warm-bias
false-positive CATEGORY impossible: a candidate is arm/trade-eligible ONLY
if our forecast AGREES with an independent mainstream within tolerance AND
the traded direction is consistent with BOTH the mainstream-implied bin and
our own modal bin. trade_score can NEVER buy back eligibility once the gate
fails — it is a standard, not a re-weight.

Per the operator selection-fix rule, every relationship test below uses a
DISTINCT per-candidate fixture (real SF / Tel Aviv / Wellington / Panama
numbers from the live shadow board) and asserts the PRE-fix RED behavior is
impossible under the gate.
"""
from __future__ import annotations

import pytest

from src.types.market import Bin


# ---------------------------------------------------------------------------
# Fixtures — distinct per-candidate, drawn from the live 2026-06-03 board
# (/tmp/arm-truth.md). Each builds the family bin set + traded bin so the
# gate sees a complete, realistic topology.
# ---------------------------------------------------------------------------


def _f_bins(lows_highs, *, open_low_label, open_high_label):
    """Build a complete °F family: open-low shoulder, range bins, open-high shoulder."""
    bins = [Bin(low=None, high=lows_highs[0][0] - 1, label=open_low_label, unit="F")]
    for lo, hi in lows_highs:
        bins.append(Bin(low=lo, high=hi, label=f"{lo}-{hi}°F", unit="F"))
    bins.append(Bin(low=lows_highs[-1][1] + 1, high=None, label=open_high_label, unit="F"))
    return bins


def _c_point_bins(values, *, open_low_label, open_high_label):
    """Build a complete °C family of point bins plus shoulders."""
    bins = [Bin(low=None, high=values[0] - 1, label=open_low_label, unit="C")]
    for v in values:
        bins.append(Bin(low=v, high=v, label=f"{v}°C", unit="C"))
    bins.append(Bin(low=values[-1] + 1, high=None, label=open_high_label, unit="C"))
    return bins


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 1 — cold-biased SF buy_no over a warm bin ⇒ gate FAILS.
# Our ECMWF reads 61.5°F; mainstream says 66°F. The traded bin "≥66°F" is the
# bias projecting false confidence that SF can't hit a temp mainstream says it
# WILL. |our − mainstream| = 4.5°F > 2°F tolerance ⇒ mainstream_close=False.
# ---------------------------------------------------------------------------


def test_cold_biased_buy_no_over_warm_bin_fails_mainstream_close():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _f_bins(
        [(62, 63), (64, 65)],
        open_low_label="61°F or below",
        open_high_label="66°F or higher",
    )
    traded_bin = bins[-1]  # "66°F or higher"
    verdict = evaluate_mainstream_agreement(
        city="San Francisco",
        target_date="2026-06-03",
        unit="F",
        our_point=61.5,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",
        members=[60.0, 61.0, 61.5, 62.0, 61.0],  # modal bin = 61 or below
        mainstream_point=66.0,
    )
    assert verdict.mainstream_available is True
    assert verdict.mainstream_close is False  # 4.5°F > 2°F tolerance
    assert verdict.passed is False
    assert verdict.fail_reason == "MAINSTREAM_NOT_CLOSE"


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 2 — agreeing Wellington buy_yes ⇒ gate PASSES.
# Our 15.2°C ≈ modal 15°C ≈ mainstream 15°C (Δ +0.2°C). Direction correct
# (buy_yes on the bin our forecast lands in AND the mainstream lands in).
# ---------------------------------------------------------------------------


def test_agreeing_forecast_correct_direction_buy_yes_passes():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    verdict = evaluate_mainstream_agreement(
        city="Wellington",
        target_date="2026-06-04",
        unit="C",
        our_point=15.2,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.1, 15.2, 15.3, 15.0],  # modal bin = 15
        mainstream_point=15.0,
    )
    assert verdict.mainstream_available is True
    assert verdict.mainstream_close is True
    assert verdict.direction_agrees_mainstream is True
    assert verdict.direction_agrees_our_modal is True
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 3 — buy_yes whose traded bin ≠ our own modal bin ⇒ FAILS.
# Tel Aviv 06-03: our modal bin is 26°C but the candidate is buy_yes on 25°C.
# This is the direction-inversion the operator flagged — buy_yes must be on
# our modal bin.
# ---------------------------------------------------------------------------


def test_buy_yes_on_non_modal_bin_fails_inversion():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [24, 25, 26, 27],
        open_low_label="23°C or below",
        open_high_label="28°C or higher",
    )
    # traded_bin = 26°C; mainstream_point = 26.0 → mainstream bin = 26 = traded_bin
    # → check 3 (direction_agrees_mainstream) PASSES for buy_yes (traded IS mainstream bin).
    # our_point = 25.4 → |25.4 - 26.0| = 0.6 ≤ 1.5 → check 2 PASSES.
    # members all round to 25 → our modal = 25 ≠ traded 26 → check 4 FAILS.
    # Inversion is the SOLE failure, cleanly isolated.
    traded_bin = next(b for b in bins if b.label == "26°C")
    verdict = evaluate_mainstream_agreement(
        city="Tel Aviv",
        target_date="2026-06-03",
        unit="C",
        our_point=25.4,
        bins=bins,
        # all members round (WMO half-up) to 25 → modal bin = 25 ≠ traded 26
        members=[25.4, 25.3, 25.2, 25.0, 24.9],
        traded_bin=traded_bin,
        direction="buy_yes",
        mainstream_point=26.0,  # Δ = 25.4 - 26.0 = -0.6 ≤ 1.5 → close
    )
    assert verdict.mainstream_close is True   # checks 1+2 pass
    assert verdict.direction_agrees_mainstream is True  # check 3 passes: traded=26=mainstream bin
    assert verdict.direction_agrees_our_modal is False  # check 4 fails: our modal=25 ≠ traded=26
    assert verdict.passed is False
    assert verdict.fail_reason == "DIRECTION_INVERSION_VS_OUR_MODAL"


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 4 — mainstream unavailable ⇒ gate FAIL_CLOSED (not pass).
# No-mainstream = ineligible, NEVER auto-pass.
# ---------------------------------------------------------------------------


def test_mainstream_unavailable_fails_closed():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    verdict = evaluate_mainstream_agreement(
        city="Wellington",
        target_date="2026-06-04",
        unit="C",
        our_point=15.2,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.1, 15.2, 15.3, 15.0],
        mainstream_point=None,  # unavailable / stale
    )
    assert verdict.mainstream_available is False
    assert verdict.passed is False
    assert verdict.fail_reason == "MAINSTREAM_FAIL_CLOSED"


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST 5 — buy_no on a bin == mainstream modal ⇒ FAILS.
# We'd be shorting the likely outcome. Mainstream point lands inside the
# traded bin, so a buy_no there contradicts mainstream.
# ---------------------------------------------------------------------------


def test_buy_no_on_mainstream_modal_bin_fails():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [27, 28, 29, 30],
        open_low_label="26°C or below",
        open_high_label="31°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "28°C")
    verdict = evaluate_mainstream_agreement(
        city="Panama City",
        target_date="2026-06-04",
        unit="C",
        our_point=28.4,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",  # shorting 28°C while mainstream says 28°C
        members=[28.0, 28.1, 28.4, 28.6, 28.0],
        mainstream_point=28.0,
    )
    assert verdict.mainstream_close is True  # forecast agrees with mainstream
    assert verdict.direction_agrees_mainstream is False  # but shorting the modal
    assert verdict.passed is False
    assert verdict.fail_reason == "DIRECTION_AGREES_MAINSTREAM_SHORTING_LIKELY"


# ---------------------------------------------------------------------------
# POSITIVE CONTROL — Panama ≥31°C buy_no (the genuine arm candidate).
# Our 28.4°C ≈ mainstream 28°C; we short ≥31°C which mainstream says won't
# settle (28 ≪ 31). Correct direction both ways ⇒ PASSES.
# ---------------------------------------------------------------------------


def test_panama_short_unlikely_high_bin_passes():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _c_point_bins(
        [27, 28, 29, 30],
        open_low_label="26°C or below",
        open_high_label="31°C or higher",
    )
    traded_bin = bins[-1]  # "31°C or higher"
    verdict = evaluate_mainstream_agreement(
        city="Panama City",
        target_date="2026-06-04",
        unit="C",
        our_point=28.4,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",
        members=[28.0, 28.1, 28.4, 28.6, 28.0],
        mainstream_point=28.0,
    )
    assert verdict.mainstream_close is True
    assert verdict.direction_agrees_mainstream is True  # 28 not in ≥31 bin
    assert verdict.direction_agrees_our_modal is True   # 28 not our modal 28? see below
    assert verdict.passed is True


# ---------------------------------------------------------------------------
# FUNCTION TESTS — tolerance boundaries, unit handling, shoulder bins.
# ---------------------------------------------------------------------------


def test_tolerance_boundary_celsius_exactly_at_limit_passes():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement, TOLERANCE_C

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    # our_point exactly TOLERANCE_C away from mainstream → close (<=)
    verdict = evaluate_mainstream_agreement(
        city="X",
        target_date="2026-06-04",
        unit="C",
        our_point=15.0,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.0, 15.0, 15.0, 15.0],
        mainstream_point=15.0 + TOLERANCE_C,
    )
    assert verdict.mainstream_close is True


def test_tolerance_boundary_celsius_just_over_limit_fails():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement, TOLERANCE_C

    bins = _c_point_bins(
        [13, 14, 15, 16],
        open_low_label="12°C or below",
        open_high_label="17°C or higher",
    )
    traded_bin = next(b for b in bins if b.label == "15°C")
    verdict = evaluate_mainstream_agreement(
        city="X",
        target_date="2026-06-04",
        unit="C",
        our_point=15.0,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_yes",
        members=[15.0, 15.0, 15.0, 15.0, 15.0],
        mainstream_point=15.0 + TOLERANCE_C + 0.01,
    )
    assert verdict.mainstream_close is False


def test_fahrenheit_tolerance_is_two_degrees():
    from src.strategy.mainstream_agreement import TOLERANCE_F, TOLERANCE_C

    assert TOLERANCE_F == 2.0
    assert TOLERANCE_C == 1.5


def test_deltas_recorded_in_verdict():
    from src.strategy.mainstream_agreement import evaluate_mainstream_agreement

    bins = _f_bins(
        [(62, 63), (64, 65)],
        open_low_label="61°F or below",
        open_high_label="66°F or higher",
    )
    traded_bin = bins[-1]
    verdict = evaluate_mainstream_agreement(
        city="San Francisco",
        target_date="2026-06-03",
        unit="F",
        our_point=61.5,
        bins=bins,
        traded_bin=traded_bin,
        direction="buy_no",
        members=[60.0, 61.0, 61.5, 62.0, 61.0],
        mainstream_point=66.0,
    )
    # delta must be signed our - mainstream and exposed for the receipt tag
    assert verdict.forecast_delta == pytest.approx(61.5 - 66.0)
    d = verdict.to_dict()
    assert d["mainstream_agreement_pass"] is False
    assert d["mainstream_point"] == 66.0
    assert d["our_point"] == 61.5
    assert d["forecast_delta"] == pytest.approx(-4.5)
    assert d["fail_reason"] == "MAINSTREAM_NOT_CLOSE"
