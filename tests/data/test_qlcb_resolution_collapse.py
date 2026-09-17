"""q_lcb must stay a real bound when the bootstrap percentile runs out of resolution.

Above q_point ~0.95 nearly every draw lands in the same bin, so the 5th percentile is 1.0 —
higher than the point estimate it bounds. Clipping it back onto q_point produced a q_lcb equal
to q_point, which the Day0 transform check refuses as degenerate, so the strongest evidence in
the system was the only evidence that could not trade.
"""
from __future__ import annotations

import math

import pytest

from src.data.replacement_forecast_materializer import _finite_draw_lower_bound


def test_unanimous_draws_still_yield_a_bound_below_the_point_estimate():
    """The case that used to degenerate: every draw agrees."""
    q_point = 0.9874
    bound = _finite_draw_lower_bound([1.0] * 500, q_point=q_point)
    assert bound < q_point, "a unanimous bootstrap must not flatten q_lcb onto q_point"
    assert bound > 0.9, f"500 unanimous draws should still support a strong bound, got {bound}"


def test_fewer_draws_give_a_more_conservative_bound():
    """The haircut is the finite-draw penalty, so it must shrink as evidence grows."""
    small = _finite_draw_lower_bound([1.0] * 10, q_point=0.9)
    large = _finite_draw_lower_bound([1.0] * 1000, q_point=0.9)
    assert small < large < 0.9


def test_dissent_lowers_the_bound():
    unanimous = _finite_draw_lower_bound([1.0] * 500, q_point=0.98)
    with_dissent = _finite_draw_lower_bound([1.0] * 450 + [0.0] * 50, q_point=0.98)
    assert with_dissent < unanimous


@pytest.mark.parametrize(
    "samples, q_point",
    [([], 0.5), ([0.0] * 500, 0.5), ([1.0] * 500, 0.0), ([1.0] * 500, float("nan"))],
)
def test_degenerate_inputs_return_zero_rather_than_raising(samples, q_point):
    """A bound this path cannot construct must read as no bound, never as an exception."""
    assert _finite_draw_lower_bound(samples, q_point=q_point) == 0.0


def test_bound_never_exceeds_the_point_estimate():
    for n in (5, 50, 500, 5000):
        for q in (0.05, 0.5, 0.95, 0.999):
            bound = _finite_draw_lower_bound([1.0] * n, q_point=q)
            assert 0.0 <= bound <= q
            assert math.isfinite(bound)
