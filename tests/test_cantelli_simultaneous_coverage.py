# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/evidence/upstream_physical_2026_07_17/cantelli_simultaneous_coverage.md
#   §Consult v2 (f) — simultaneous-coverage accounting for the composed finite-evidence
#   floor max(CP_rho, Cantelli). Verdict: NO-CHANGE-WITH-PROOF. These tests pin the
#   mathematical facts that justify serving the composed floor unchanged.
"""Property tests for the CP+Cantelli composed finite-evidence floor.

The serving floor per bin is ``F = max(CP_rho, Cantelli)`` (both one-sided 95%
UCBs on the SAME per-bin settlement probability ``q_bin``). The math verdict:

  1. POINTWISE VALID: ``max`` of two valid (1-α) one-sided UCBs is a valid (1-α)
     one-sided UCB — ``{q>max} ⊆ {q>A}`` so ``P(q>max) ≤ P(q>A) ≤ α``. The max is
     strictly MORE conservative than either term, never less.
  2. PLUG-IN NEUTRALIZED: the Cantelli term uses ESTIMATED (μ,σ) as if known, but
     it can only ever LOWER the composed floor by being optimistically small — and
     whenever ``Cantelli < CP_rho`` the max returns ``CP_rho`` exactly, masking the
     Cantelli error. So the composed floor's marginal coverage is bounded below by
     the CP term's, which the settled-archive measurement shows covers post-ρ.
  3. CONSERVATIVE DIRECTION: the composed floor is monotone non-decreasing in σ
     (a wider predictive spread only raises Cantelli) and in ρ (raises CP) — every
     lever moves it in the conservative direction only.

These are the invariants that make "serve the composed floor unchanged" correct.
"""
from __future__ import annotations

import math

from src.data.replacement_forecast_materializer import (
    _current_evidence_tail_ucb_floors,
    _finite_evidence_binomial_ucb,
)


class _Bin:
    def __init__(self, bin_id: str, lower_c: float | None, upper_c: float | None) -> None:
        self.bin_id = bin_id
        self.lower_c = lower_c
        self.upper_c = upper_c


def _cantelli(mu: float, sigma: float, low: float | None, high: float | None) -> float:
    """The serving one-sided Cantelli moment mass for a preimage-shifted bin."""
    var = sigma * sigma
    if low is not None and low > mu:
        gap = low - mu
        return var / (var + gap * gap)
    if high is not None and high < mu:
        gap = mu - high
        return var / (var + gap * gap)
    return 0.0


def _floors(mu, sigma, bins, members, *, metric=None, half_step=0.5, rule="wmo_half_up"):
    return _current_evidence_tail_ucb_floors(
        mu_star=mu,
        predictive_sigma_c=sigma,
        bins=bins,
        half_step=half_step,
        rounding_rule=rule,
        members_c=members,
        metric=metric,
    )


def test_composed_floor_dominates_both_terms() -> None:
    """F(bin) == max(CP(k,n), Cantelli(gap)) and is >= each term for every bin."""
    bins = [
        _Bin("far_lo", None, 16.0),
        _Bin("near_lo", 17.0, 17.0),
        _Bin("center", 20.0, 20.0),
        _Bin("near_hi", 23.0, 23.0),
        _Bin("far_hi", 26.0, None),
    ]
    mu, sigma = 20.0, 2.0
    # Members: modal at 20, a couple in each shoulder, none in the far tails.
    members = [16.0] * 1 + [17.0] * 3 + [20.0] * 41 + [23.0] * 5 + [26.0] * 1
    floors = _floors(mu, sigma, bins, members)
    low_off, high_off = -0.5, 0.5
    for b in bins:
        low = None if b.lower_c is None else b.lower_c + low_off
        high = None if b.upper_c is None else b.upper_c + high_off
        cant = _cantelli(mu, sigma, low, high)
        # member hits inside this preimage
        k = sum(1 for v in members if (low is None or v >= low) and (high is None or v < high))
        cp = _finite_evidence_binomial_ucb(k, len(members))
        assert floors[b.bin_id] >= cp - 1e-15
        assert floors[b.bin_id] >= cant - 1e-15
        assert math.isclose(floors[b.bin_id], max(cp, cant), rel_tol=0.0, abs_tol=1e-12)


def test_cantelli_masked_when_cp_binds() -> None:
    """When CP >= Cantelli the composed floor is EXACTLY CP — plug-in μ,σ error masked."""
    # A modest-gap bin with a small sigma makes Cantelli tiny; zero member hits keep CP
    # at the ~0.057 zero-hit floor, so CP dominates and the floor must equal CP exactly.
    bins = [_Bin("gap_bin", 30.0, 30.0)]
    mu, sigma = 20.0, 0.3
    members = [20.0] * 51  # zero hits in the 30C bin
    low = 30.0 - 0.5
    cant = _cantelli(mu, sigma, low, None)
    cp = _finite_evidence_binomial_ucb(0, 51)
    assert cant < cp  # Cantelli optimistically small here
    floor = _floors(mu, sigma, bins, members)["gap_bin"]
    assert math.isclose(floor, cp, rel_tol=0.0, abs_tol=1e-15)
    # Perturbing sigma DOWN (more plug-in optimism) cannot move the floor while CP binds.
    floor_smaller_sigma = _floors(mu, 0.1, bins, members)["gap_bin"]
    assert math.isclose(floor_smaller_sigma, cp, rel_tol=0.0, abs_tol=1e-15)


def test_composed_floor_monotone_nondecreasing_in_sigma() -> None:
    """Widening the predictive spread only raises the composed floor (conservative)."""
    bins = [_Bin("tail", 25.0, 25.0)]
    mu = 20.0
    members = [20.0] * 51  # zero hits -> CP term fixed; only Cantelli moves with sigma
    prev = -1.0
    for sigma in (0.5, 1.0, 2.0, 4.0, 8.0):
        cur = _floors(mu, sigma, bins, members)["tail"]
        assert cur >= prev - 1e-15, sigma
        prev = cur
    # Large sigma drives Cantelli above the zero-hit CP floor -> floor strictly exceeds CP.
    cp = _finite_evidence_binomial_ucb(0, 51)
    assert _floors(mu, 8.0, bins, members)["tail"] > cp


def test_composed_floor_identity_when_bin_straddles_mean() -> None:
    """A bin straddling μ has Cantelli == 0, so the floor is the pure CP term."""
    bins = [_Bin("center", 20.0, 20.0)]
    mu, sigma = 20.0, 2.0
    members = [20.0] * 30 + [19.0] * 11 + [21.0] * 10  # 30 hits in the 20C preimage
    k = 30
    cp = _finite_evidence_binomial_ucb(k, len(members))
    floor = _floors(mu, sigma, bins, members)["center"]
    assert math.isclose(floor, cp, rel_tol=0.0, abs_tol=1e-12)


def test_composed_floor_is_valid_one_sided_ucb_pointwise() -> None:
    """The composed floor is >= both 95% UCBs, hence a valid 95% UCB (exceedance <= 5%)."""
    # Two independently-valid 95% UCBs A (CP) and B (Cantelli); their max cannot have
    # exceedance above either, so P(q > max) <= min(P(q>A), P(q>B)) <= 0.05.
    bins = [_Bin("b", 24.0, 24.0)]
    mu, sigma = 20.0, 2.5
    members = [20.0] * 49 + [24.0] * 2  # k=2 hits
    low = 24.0 - 0.5
    cant = _cantelli(mu, sigma, low, None)
    cp = _finite_evidence_binomial_ucb(2, 51)
    floor = _floors(mu, sigma, bins, members)["b"]
    assert floor >= cp and floor >= cant
    assert math.isclose(floor, max(cp, cant), abs_tol=1e-12)
