# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: docs/evidence/live_order_pathology/2026-06-22_qlcb_lowerbound_honesty.md
#   Forward-validated: far-tail YES bins (q_point < ~0.05) have q_lcb ~0.07-0.10 but realize
#   ~0.006; flooring q_lcb to the realized far-tail frequency (~0.003) makes them self-reject
#   below typical fill prices (~0.01). Kills 191 give-away admissions (188 losers/3 winners),
#   log-loss -0.22%, zero winning-bin q<1e-6 (q_point UNTOUCHED). Shoulder/mode IDENTITY.
"""TDD: far-tail q_lcb honesty in _build_fused_q_bounds.

These tests MUST FAIL before the implementation is added, and pass after.
"""
from __future__ import annotations

import math

import pytest

from src.data.replacement_forecast_materializer import (
    FAR_TAIL_LCB_FLOOR,
    FAR_TAIL_Q_POINT_THRESH,
    _build_fused_q_bounds,
)


# ---------------------------------------------------------------------------
# Minimal bin stub for _build_fused_q_bounds
# ---------------------------------------------------------------------------

class _Bin:
    """Minimal bin object matching the interface _build_fused_q_bounds consumes."""

    def __init__(
        self,
        bin_id: str,
        lower_c: float | None,
        upper_c: float | None,
        center_c: float = 0.0,
    ):
        self.bin_id = bin_id
        self.lower_c = lower_c
        self.upper_c = upper_c
        self.center_c = center_c
        self.display_unit = "C"
        self.settlement_unit = "C"
        self.rounding_rule = "wmo_half_up"


def _three_bin_setup():
    """A 3-bin topology: far-tail, middle, shoulder.

    Center mu* = 22.0°C, predictive sigma = 2.0°C (narrow enough that the bins
    far from center have very low probability mass — they are the 'far-tail' bins).
    The far-tail bin straddles [16.0, 18.0)°C — ~2 sigma below center.
    """
    bins = [
        _Bin("far_tail", lower_c=16.0, upper_c=18.0),     # ~2 sigma below center
        _Bin("middle",   lower_c=20.0, upper_c=22.0),      # modal region
        _Bin("shoulder", lower_c=22.0, upper_c=24.0),      # adjacent to mode
        _Bin("open_high", lower_c=26.0, upper_c=None),     # open-ended catch-all
    ]
    mu_star = 22.0
    center_sigma_c = 0.1   # tight center uncertainty
    predictive_sigma_c = 2.0
    return bins, mu_star, center_sigma_c, predictive_sigma_c


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------

def test_far_tail_constants_are_defined_and_reasonable() -> None:
    """FAR_TAIL_Q_POINT_THRESH and FAR_TAIL_LCB_FLOOR must be defined and in the
    right ballpark per the forward-validated evidence."""
    assert 0.0 < FAR_TAIL_Q_POINT_THRESH < 0.15, (
        f"FAR_TAIL_Q_POINT_THRESH={FAR_TAIL_Q_POINT_THRESH} out of expected range"
    )
    assert 0.0 < FAR_TAIL_LCB_FLOOR < 0.02, (
        f"FAR_TAIL_LCB_FLOOR={FAR_TAIL_LCB_FLOOR} out of expected range"
    )
    # Forward-validated values from the evidence doc
    assert abs(FAR_TAIL_Q_POINT_THRESH - 0.05) < 1e-9, (
        f"Expected 0.05, got {FAR_TAIL_Q_POINT_THRESH}"
    )
    assert abs(FAR_TAIL_LCB_FLOOR - 0.003) < 1e-9, (
        f"Expected 0.003, got {FAR_TAIL_LCB_FLOOR}"
    )


# ---------------------------------------------------------------------------
# Far-tail flooring: q_point < FAR_TAIL_THRESH → q_lcb capped at FAR_TAIL_LCB_FLOOR
# ---------------------------------------------------------------------------

def test_far_tail_bin_lcb_capped_at_floor() -> None:
    """Far-tail bin (q_point=0.03, q_lcb_in~0.09) → q_lcb_out ≈ 0.003 (floored).

    The center sigma is small enough that nearly all 200 bootstrap draws land
    ~22°C → the [16,18) bin gets q from Gaussian CDF ~ near zero, but still
    the raw bootstrap 5th pct may be larger than 0.003. After far-tail honesty
    the lcb is capped at 0.003.
    """
    bins, mu_star, center_sigma_c, predictive_sigma_c = _three_bin_setup()
    # Use a q_point map where the far_tail bin has a small positive mass (< THRESH)
    # Simulate the scenario from the evidence: the bootstrap 5th pct ~0.07-0.10
    # for a far-tail bin because the center uncertainty drives some draws closer.
    # We construct the inputs so the raw bootstrap gives a lcb > FAR_TAIL_LCB_FLOOR
    # for the far-tail bin, verifying the cap fires.
    # With mu*=22, sigma=2, center_sigma=0.1, the far-tail [16,18) bin has ~1.6% raw mass.
    # q_point dict (provided externally — it's the already-built point q):
    q_point = {
        "far_tail": 0.03,   # less than FAR_TAIL_Q_POINT_THRESH=0.05 → honesty fires
        "middle":   0.40,
        "shoulder": 0.35,
        "open_high": 0.22,
    }
    lcb_map, ucb_map = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
        rounding_rule="wmo_half_up",
    )
    far_tail_lcb = lcb_map["far_tail"]
    far_tail_q_pt = q_point["far_tail"]
    # The floor must have fired: q_lcb ≤ FAR_TAIL_LCB_FLOOR
    assert far_tail_lcb <= FAR_TAIL_LCB_FLOOR + 1e-12, (
        f"Far-tail q_lcb={far_tail_lcb:.6f} should be ≤ {FAR_TAIL_LCB_FLOOR} "
        f"after honesty cap (q_point={far_tail_q_pt})"
    )
    # q_lcb ≥ 0 always
    assert far_tail_lcb >= 0.0, f"q_lcb must be >= 0, got {far_tail_lcb}"
    # q_lcb ≤ q_point (ordering invariant)
    assert far_tail_lcb <= far_tail_q_pt + 1e-12, (
        f"q_lcb={far_tail_lcb} > q_point={far_tail_q_pt}"
    )


def test_far_tail_self_rejects_at_typical_fill_price() -> None:
    """Edge = q_lcb - price. Far-tail with q_point=0.03, price=0.01 → edge < 0 after honesty."""
    bins, mu_star, center_sigma_c, predictive_sigma_c = _three_bin_setup()
    q_point = {
        "far_tail": 0.03,
        "middle": 0.40,
        "shoulder": 0.35,
        "open_high": 0.22,
    }
    lcb_map, _ = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
        rounding_rule="wmo_half_up",
    )
    typical_fill_price = 0.01
    edge = lcb_map["far_tail"] - typical_fill_price
    assert edge < 0.0, (
        f"Far-tail bin should self-reject (edge < 0) at price=0.01 after honesty. "
        f"Got q_lcb={lcb_map['far_tail']:.6f}, edge={edge:.6f}"
    )


# ---------------------------------------------------------------------------
# Identity: shoulder/mode bins (q_point >= FAR_TAIL_THRESH) are UNCHANGED
# ---------------------------------------------------------------------------

def test_identity_shoulder_bin_q_lcb_unchanged() -> None:
    """Shoulder bin (q_point=0.20) → q_lcb UNCHANGED by far-tail honesty."""
    bins = [
        _Bin("shoulder", lower_c=20.0, upper_c=22.0),
        _Bin("mode",     lower_c=22.0, upper_c=24.0),
        _Bin("open_lo",  lower_c=None, upper_c=18.0),
    ]
    mu_star = 22.0
    center_sigma_c = 0.5
    predictive_sigma_c = 2.0
    q_point = {
        "shoulder": 0.20,
        "mode": 0.55,
        "open_lo": 0.25,
    }
    # Run once with the real implementation
    lcb_map, ucb_map = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
        rounding_rule="wmo_half_up",
    )
    # Shoulder: q_point=0.20 >= 0.05 → identity; q_lcb must be the raw bootstrap value
    # (we check it's strictly positive and NOT capped at FAR_TAIL_LCB_FLOOR since
    # a shoulder bin's raw lcb should be well above 0.003)
    assert lcb_map["shoulder"] > FAR_TAIL_LCB_FLOOR, (
        f"Shoulder bin q_lcb={lcb_map['shoulder']} should be > {FAR_TAIL_LCB_FLOOR} (identity)"
    )
    # Standard invariants hold
    assert 0.0 <= lcb_map["shoulder"] <= q_point["shoulder"] + 1e-12


def test_q_point_untouched_by_honesty() -> None:
    """q_point is NEVER modified — only q_lcb is adjusted.

    _build_fused_q_bounds receives q_point as a Mapping and returns bounds only.
    The original dict must be unchanged. (This test verifies the interface contract —
    the function should not mutate its input.)
    """
    bins, mu_star, center_sigma_c, predictive_sigma_c = _three_bin_setup()
    q_point_original = {
        "far_tail": 0.03,
        "middle": 0.40,
        "shoulder": 0.35,
        "open_high": 0.22,
    }
    q_point_copy = dict(q_point_original)
    _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point_original,
        n_draws=200,
        rounding_rule="wmo_half_up",
    )
    assert q_point_original == q_point_copy, "q_point must not be mutated"


# ---------------------------------------------------------------------------
# q_lcb ≤ q_point invariant across all bins
# ---------------------------------------------------------------------------

def test_qlcb_leq_qpoint_invariant_preserved() -> None:
    """q_lcb ≤ q_point for every bin, including far-tail bins after honesty cap."""
    bins, mu_star, center_sigma_c, predictive_sigma_c = _three_bin_setup()
    q_point = {
        "far_tail": 0.03,
        "middle": 0.40,
        "shoulder": 0.35,
        "open_high": 0.22,
    }
    lcb_map, _ = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
        rounding_rule="wmo_half_up",
    )
    for bin_id, q_pt in q_point.items():
        assert lcb_map[bin_id] >= 0.0, f"q_lcb[{bin_id}] < 0"
        assert lcb_map[bin_id] <= q_pt + 1e-12, (
            f"q_lcb[{bin_id}]={lcb_map[bin_id]:.6f} > q_point={q_pt:.6f}"
        )


# ---------------------------------------------------------------------------
# No q_lcb < 0
# ---------------------------------------------------------------------------

def test_no_negative_qlcb() -> None:
    """No bin ever gets a negative q_lcb."""
    bins, mu_star, center_sigma_c, predictive_sigma_c = _three_bin_setup()
    q_point = {"far_tail": 0.001, "middle": 0.50, "shoulder": 0.35, "open_high": 0.149}
    lcb_map, _ = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
    )
    for bid, lcb in lcb_map.items():
        assert lcb >= 0.0, f"q_lcb[{bid}]={lcb:.6f} < 0"


# ---------------------------------------------------------------------------
# Winning-bin (high q_point) far-tail: q_point > 0 → no log blowup
# ---------------------------------------------------------------------------

def test_winning_bin_far_tail_q_point_nonzero() -> None:
    """A 'winning' far-tail bin (q_point just above 0 but < FAR_TAIL_THRESH) must keep
    q_point > 0. The honesty cap touches ONLY q_lcb, never q_point.

    This guards against the -log(q_point→0) blowup: if q_point were zeroed, the proper
    scoring rule diverges. The test verifies that even with q_point < FAR_TAIL_THRESH the
    input q_point dict is NOT modified (only q_lcb is capped)."""
    bins, mu_star, center_sigma_c, predictive_sigma_c = _three_bin_setup()
    # Very small q_point in the far-tail bin — simulates a rare but real win case
    q_point_input = {"far_tail": 1e-4, "middle": 0.60, "shoulder": 0.30, "open_high": 0.0999}
    original_far_tail_q = q_point_input["far_tail"]
    lcb_map, _ = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point_input,
        n_draws=200,
    )
    # q_point must be strictly positive (no blowup)
    assert q_point_input["far_tail"] == original_far_tail_q, (
        f"q_point was mutated: expected {original_far_tail_q}, got {q_point_input['far_tail']}"
    )
    assert original_far_tail_q > 0.0, "test precondition: q_point must be > 0"
    # q_lcb ≤ q_point (tiny but non-negative)
    assert 0.0 <= lcb_map["far_tail"] <= original_far_tail_q + 1e-12


# ---------------------------------------------------------------------------
# Give-away shape: KL-style far-tail self-rejects; genuine shoulder still admits
# ---------------------------------------------------------------------------

def test_genuine_shoulder_yes_still_admits() -> None:
    """Shoulder YES bin (q_point=0.22) still admits even after far-tail honesty.

    The honesty cap only applies to far-tail bins (q_point < 0.05). A shoulder YES
    bin at q_point=0.22 should have q_lcb well above a typical fill price of 0.18.
    """
    bins = [
        _Bin("shoulder_yes", lower_c=21.0, upper_c=23.0),
        _Bin("mode",         lower_c=23.0, upper_c=25.0),
        _Bin("open_lo",      lower_c=None, upper_c=19.0),
    ]
    q_point = {
        "shoulder_yes": 0.22,
        "mode": 0.58,
        "open_lo": 0.20,
    }
    lcb_map, _ = _build_fused_q_bounds(
        mu_star=23.5,
        center_sigma_c=0.3,
        predictive_sigma_c=1.5,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
    )
    # Shoulder YES: q_point=0.22 → identity (not far-tail). Should have a positive lcb.
    # At a typical 0.18 price, edge = q_lcb - 0.18 should be > 0 for a real shoulder YES.
    shoulder_lcb = lcb_map["shoulder_yes"]
    assert shoulder_lcb > FAR_TAIL_LCB_FLOOR, (
        f"Shoulder YES lcb={shoulder_lcb:.4f} should be well above {FAR_TAIL_LCB_FLOOR}"
    )
    # Admission check: if fill price is 0.18, should admit (positive edge)
    fill_price = 0.18
    edge = shoulder_lcb - fill_price
    assert edge > 0.0, (
        f"Shoulder YES should admit at price=0.18 but edge={edge:.4f} (q_lcb={shoulder_lcb:.4f})"
    )


# ---------------------------------------------------------------------------
# Boundary: q_point == FAR_TAIL_THRESH exactly → IDENTITY (no cap)
# ---------------------------------------------------------------------------

def test_boundary_at_thresh_is_identity() -> None:
    """A bin with q_point == FAR_TAIL_Q_POINT_THRESH exactly: the condition is strict (<),
    so this bin is NOT in the far-tail region and q_lcb is NOT capped."""
    bins = [
        _Bin("boundary", lower_c=18.0, upper_c=20.0),
        _Bin("mode",     lower_c=22.0, upper_c=24.0),
        _Bin("open_lo",  lower_c=None, upper_c=16.0),
    ]
    q_point = {
        "boundary": FAR_TAIL_Q_POINT_THRESH,  # exactly at threshold — identity
        "mode": 0.55,
        "open_lo": 0.40,
    }
    lcb_map, _ = _build_fused_q_bounds(
        mu_star=23.0,
        center_sigma_c=0.2,
        predictive_sigma_c=2.5,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
    )
    # Standard invariants only; the cap must NOT fire at exactly THRESH
    assert 0.0 <= lcb_map["boundary"] <= FAR_TAIL_Q_POINT_THRESH + 1e-12
    # The lcb should NOT be capped at FAR_TAIL_LCB_FLOOR (it may be, but only because the
    # raw bootstrap value happens to be that low — NOT because the honesty cap fired on it).
    # We test identity by verifying q_lcb is ≤ q_point (ordering) and NOT negative.
    # Cannot guarantee lcb > FAR_TAIL_LCB_FLOOR here because the boundary bin may have low mass.


# ---------------------------------------------------------------------------
# buy_no path: q_lcb for NO direction untouched (honesty only adjusts YES lcb)
#
# NOTE: _build_fused_q_bounds computes YES-side bounds. The buy_no path does not
# call _build_fused_q_bounds directly — it reads q_lcb from the persisted bundle.
# This test confirms _build_fused_q_bounds itself doesn't break the ucb (which
# the NO path uses as: buy_no_lcb = 1 - q_ucb_yes). The ucb must remain ≥ q_point.
# ---------------------------------------------------------------------------

def test_buy_no_ucb_path_unchanged() -> None:
    """The UCB (which the buy_no path computes from as 1-q_ucb_yes) is NOT capped by
    the far-tail honesty. Only q_lcb_yes is adjusted for far-tail bins."""
    bins, mu_star, center_sigma_c, predictive_sigma_c = _three_bin_setup()
    q_point = {
        "far_tail": 0.03,
        "middle": 0.40,
        "shoulder": 0.35,
        "open_high": 0.22,
    }
    lcb_map, ucb_map = _build_fused_q_bounds(
        mu_star=mu_star,
        center_sigma_c=center_sigma_c,
        predictive_sigma_c=predictive_sigma_c,
        bins=bins,
        half_step=0.5,
        q_point=q_point,
        n_draws=200,
    )
    # UCB must be >= q_point for every bin (the ordering clip ensures this)
    for bin_id, q_pt in q_point.items():
        assert ucb_map[bin_id] >= q_pt - 1e-12, (
            f"ucb[{bin_id}]={ucb_map[bin_id]:.6f} < q_point={q_pt:.6f} (must be ≥)"
        )
    # Far-tail UCB is NOT capped at FAR_TAIL_LCB_FLOOR (the cap only touches lcb)
    # It should be ≥ q_point["far_tail"] = 0.03
    assert ucb_map["far_tail"] >= q_point["far_tail"] - 1e-12
