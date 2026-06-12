# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: objective-math audit 2026-06-11 (docs/evidence/2026_06_11_objective_math/).
#   Part A: the replacement chain's tradable q carries NO market term, so max(q_lcb-price) ranks
#   the model<->market disagreement FIRST. On the sigma-flattened fused q this is a phantom NO
#   edge in the adjacent-center class C3 (-4.8pt mean, 74% of cells, 30-54pt tails). The market
#   anchor caps a near-center NO's tradable q_lcb at the legacy alpha-blend of model-NO with the
#   market-implied NO. These are RELATIONSHIP pins (cross-module invariants), not function tests:
#   they assert the property that holds when model-q flows into the market-anchored cap.
"""Market-anchor antibodies — class-conditional tradable-q_lcb relationship pins.

Category being killed: a near-center buy_no candidate whose tradable q_lcb_no exceeds the
market-anchored belief (the phantom NO edge the ranking objective chases first). The cap makes
that category bounded by construction. Also pinned: the far-NO harvest (C4) is untouched, the
cap is one-sided (never widens an edge -> never creates a trade), and a missing market price is
fail-open (no fabricated haircut)."""
from __future__ import annotations

import math

import pytest

from src.strategy.live_inference.market_anchor import (
    DEFAULT_NEAR_CENTER_STEPS,
    market_anchored_no_lcb,
)


# -- C1-C3 (near-center): the cap MUST bound q_lcb_no at the alpha-market-anchor -----------------

def test_c3_phantom_edge_is_capped_at_market_anchor():
    """The audit's C3 exemplar shape: model claims a more confident NO than the market backs.
    The tradable q_lcb_no must be lowered to exactly alpha*q_model_no + (1-alpha)*q_market_no."""
    alpha = 0.4
    q_model_no = 0.83  # fused q_no near center (model under-weights its own adjacent bin)
    q_market_no = 0.778  # sharper market prices the NO lower (the YES bin is likelier than we say)
    r = market_anchored_no_lcb(
        q_lcb_no=0.83, q_model_no=q_model_no, market_no_price=q_market_no,
        alpha=alpha, bin_distance_steps=1.0,  # within 1.5-step near-center reach
    )
    expected = alpha * q_model_no + (1.0 - alpha) * q_market_no
    assert r.capped is True
    assert r.q_lcb_no_out == pytest.approx(expected, abs=1e-9)
    # RELATIONSHIP INVARIANT: a near-center NO's tradable lower bound can never exceed the anchor.
    assert r.q_lcb_no_out <= r.q_anchor_no + 1e-12


@pytest.mark.parametrize("dist", [0.0, 0.25, 1.0, 1.5])
def test_near_center_classes_all_within_reach_are_capped(dist):
    """C1 (dist 0, inside bin), C2 (boundary zone ~0.25), C3 (<=1.5 steps) are all in scope."""
    r = market_anchored_no_lcb(
        q_lcb_no=0.90, q_model_no=0.90, market_no_price=0.70,
        alpha=0.4, bin_distance_steps=dist,
    )
    assert r.capped is True
    assert r.q_lcb_no_out < 0.90


def test_missing_distance_fails_toward_applying_cap():
    """A NO leg with no measurable center is itself the incident category -> treat as near-center."""
    r = market_anchored_no_lcb(
        q_lcb_no=0.90, q_model_no=0.90, market_no_price=0.70,
        alpha=0.4, bin_distance_steps=None,
    )
    assert r.capped is True


# -- C4 (far NO, the harvest): MUST be untouched within epsilon ---------------------------------

@pytest.mark.parametrize("dist", [1.51, 2.0, 3.0, 5.0])
def test_c4_far_harvest_is_untouched(dist):
    """The favorite-longshot harvest (far NO) had market~=model in the audit (gap -0.2pt). The cap
    is scoped OUT of C4 so the legitimate edge survives byte-identical regardless of the market."""
    q_in = 0.91
    r = market_anchored_no_lcb(
        q_lcb_no=q_in, q_model_no=q_in, market_no_price=0.50,  # even a wildly-different market
        alpha=0.4, bin_distance_steps=dist,
    )
    assert r.capped is False
    assert r.q_lcb_no_out == pytest.approx(q_in, abs=1e-12)


def test_near_center_reach_boundary_is_inclusive():
    """Exactly at the reach (1.5 steps) is near-center (capped); just beyond is far (untouched)."""
    at = market_anchored_no_lcb(q_lcb_no=0.9, q_model_no=0.9, market_no_price=0.6,
                                alpha=0.4, bin_distance_steps=DEFAULT_NEAR_CENTER_STEPS)
    beyond = market_anchored_no_lcb(q_lcb_no=0.9, q_model_no=0.9, market_no_price=0.6,
                                    alpha=0.4, bin_distance_steps=DEFAULT_NEAR_CENTER_STEPS + 1e-6)
    assert at.capped is True
    assert beyond.capped is False


# -- One-sided honesty: the cap can only LOWER q_lcb_no (never create a trade) -------------------

def test_cap_never_widens_when_model_weaker_than_market():
    """When the model is LESS confident than the market (anchor >= input), the lower bound stands.
    The cap is a one-sided haircut, exactly like the settlement-coverage shrink — it must never
    raise q_lcb (which would manufacture an edge)."""
    r = market_anchored_no_lcb(
        q_lcb_no=0.70, q_model_no=0.70, market_no_price=0.95,
        alpha=0.4, bin_distance_steps=1.0,
    )
    assert r.capped is False
    assert r.q_lcb_no_out == pytest.approx(0.70, abs=1e-12)
    # Even though the anchor would be HIGHER, the output never exceeds the input.
    assert r.q_lcb_no_out <= 0.70 + 1e-12


def test_output_is_always_le_input():
    """Exhaustive one-sidedness over a grid: q_lcb_no_out <= q_lcb_no for every configuration."""
    for q_in in (0.3, 0.6, 0.83, 0.95):
        for mkt in (0.1, 0.5, 0.778, 0.99):
            for a in (0.2, 0.4, 0.65):
                for d in (0.0, 1.0, 1.5):
                    r = market_anchored_no_lcb(
                        q_lcb_no=q_in, q_model_no=q_in, market_no_price=mkt,
                        alpha=a, bin_distance_steps=d,
                    )
                    assert r.q_lcb_no_out <= q_in + 1e-12


# -- Fail-open: a missing market price must NOT fabricate a haircut ------------------------------

def test_missing_market_price_is_fail_open():
    r = market_anchored_no_lcb(
        q_lcb_no=0.83, q_model_no=0.83, market_no_price=None,
        alpha=0.4, bin_distance_steps=1.0,
    )
    assert r.capped is False
    assert r.q_lcb_no_out == pytest.approx(0.83, abs=1e-12)


def test_nonfinite_market_price_is_fail_open():
    for bad in (float("nan"), float("inf")):
        r = market_anchored_no_lcb(
            q_lcb_no=0.83, q_model_no=0.83, market_no_price=bad,
            alpha=0.4, bin_distance_steps=1.0,
        )
        assert r.capped is False
        assert r.q_lcb_no_out == pytest.approx(0.83, abs=1e-12)


def test_degenerate_lcb_passes_through():
    r = market_anchored_no_lcb(
        q_lcb_no=float("nan"), q_model_no=0.5, market_no_price=0.4,
        alpha=0.4, bin_distance_steps=1.0,
    )
    assert r.capped is False
    assert math.isnan(r.q_lcb_no_out)


# -- alpha semantics: higher alpha = trust model more = weaker cap -------------------------------

def test_higher_alpha_weakens_the_cap():
    low = market_anchored_no_lcb(q_lcb_no=0.9, q_model_no=0.9, market_no_price=0.6,
                                 alpha=0.25, bin_distance_steps=1.0)
    high = market_anchored_no_lcb(q_lcb_no=0.9, q_model_no=0.9, market_no_price=0.6,
                                  alpha=0.65, bin_distance_steps=1.0)
    # Both cap (model 0.9 > market 0.6), but higher alpha leaves a HIGHER (less-haircut) bound.
    assert low.capped and high.capped
    assert high.q_lcb_no_out > low.q_lcb_no_out
