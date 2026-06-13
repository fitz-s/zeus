# Created: 2026-06-13
# Last reused/audited: 2026-06-13
# Authority basis: docs/authority/statistical_calibration_addendum_2026-06-13.md
#   (A2 BH/FDR condemned -> EB selection shrinkage + posterior-utility license;
#   D3 Tweedie license at N>=200; winner's-curse slope diagnostic) + consult2 Q3
#   reference impls. Relationship-first per repo law: the load-bearing test
#   (test_shrinkage_pulls_argmax_toward_mean) verifies the CROSS-CANDIDATE
#   property that holds across the selection boundary — the raw argmax of a pure
#   winner's-curse universe is inflated by ~s*sqrt(2 ln N), and EB shrinkage
#   pulls the selected edge back toward the cross-sectional mean (~0).
"""Relationship + unit tests for src/strategy/selection_shrinkage.py (C2)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.strategy.selection_shrinkage import (  # noqa: E402
    TWEEDIE_MIN_CANDIDATES,
    eb_shrink_edges,
    effective_num_tests_from_cov,
    expected_max_standard_normal,
    expected_log_growth_binary,
    kelly_fraction_binary,
    lfsr,
    select_license,
    tweedie_shrink,
)


# ---------------------------------------------------------------------------
# RELATIONSHIP TEST (load-bearing): shrinkage pulls the argmax toward the mean
# ---------------------------------------------------------------------------
def test_shrinkage_pulls_argmax_toward_cross_sectional_mean():
    """Winner's-curse setup: N=100 NULL edges + iid noise.

    The cross-module invariant: the RAW selected max edge of a pure-noise
    universe is selection-inflated to ~ s*sqrt(2 ln N) (>= ~2.5 SE here), while
    the EB-SHRUNK selected max collapses toward the cross-sectional mean (~0).
    A selection gate that ranks on the raw edge would license phantom edges;
    one that ranks on the shrunk edge does not. This is the property that holds
    ACROSS the boundary where the candidate universe flows into selection.
    """
    rng = np.random.default_rng(20260613)
    N = 100
    s_each = 1.0
    e_hat = rng.normal(0.0, s_each, size=N)  # all true edges = 0
    s = np.full(N, s_each)

    raw_max = float(np.max(e_hat))
    res = eb_shrink_edges(e_hat, s)
    shrunk_max = float(np.max(res.shrunk_mean))

    # Raw max is selection-inflated: comfortably above 1.5 SE by construction.
    expected_inflation = s_each * expected_max_standard_normal(N)
    assert raw_max > 1.5 * s_each
    assert raw_max == pytest.approx(expected_inflation, abs=1.2)

    # Shrunk max is pulled back hard toward the (zero) cross-sectional mean.
    assert abs(shrunk_max) < 0.5 * raw_max
    assert abs(res.mu) < 0.3
    # The shrunk selected edge is far smaller than the raw selected edge.
    assert shrunk_max < raw_max - 1.0


def test_shrinkage_preserves_a_real_signal():
    """One candidate has a genuine large edge; EB must NOT shrink it to noise.

    Relationship guard against over-shrinkage: when true signal exists, tau^2
    grows and the strong candidate's shrunk mean stays well above the null
    crowd — the gate stays sensitive, not just conservative.
    """
    rng = np.random.default_rng(7)
    N = 60
    e_hat = rng.normal(0.0, 1.0, size=N)
    e_hat[0] = 8.0  # a real, large, separated edge
    s = np.full(N, 1.0)
    res = eb_shrink_edges(e_hat, s)
    # The strong candidate remains the argmax and stays well separated from the
    # null crowd after shrinkage (it is NOT collapsed to the grand mean). The
    # null candidates have |e_hat| ~ 1; the survivor stays several SD above.
    assert int(np.argmax(res.shrunk_mean)) == 0
    assert res.shrunk_mean[0] > 3.0
    # And it is far above the second-largest shrunk edge (the strong signal is
    # preserved, not flattened into the noise band).
    second = float(np.sort(res.shrunk_mean)[-2])
    assert res.shrunk_mean[0] > second + 1.5


# ---------------------------------------------------------------------------
# lfsr correctness on synthetic posteriors
# ---------------------------------------------------------------------------
def test_lfsr_from_samples_matches_fraction():
    samples = np.array([-1.0, -0.5, 0.0, 0.5, 1.0, 2.0])
    # values <= 0: -1, -0.5, 0  -> 3/6
    assert lfsr(samples) == pytest.approx(0.5)


def test_lfsr_normal_approx_matches_phi():
    # P(e <= 0) for N(2, 1) = Phi(-2) ~ 0.0228
    val = lfsr(e_hat=2.0, s=1.0)
    assert val == pytest.approx(0.5 * (1.0 + math.erf(-2.0 / math.sqrt(2.0))), abs=1e-12)
    assert val == pytest.approx(0.02275, abs=1e-4)


def test_lfsr_symmetric_zero_mean_is_half():
    assert lfsr(e_hat=0.0, s=1.0) == pytest.approx(0.5)
    # large-sample empirical agreement
    rng = np.random.default_rng(1)
    big = rng.normal(2.0, 1.0, size=200_000)
    assert lfsr(big) == pytest.approx(0.02275, abs=3e-3)


def test_lfsr_degenerate_point_mass():
    assert lfsr(e_hat=0.5, s=0.0) == 0.0   # deterministically positive
    assert lfsr(e_hat=-0.5, s=0.0) == 1.0  # deterministically <= 0


def test_lfsr_requires_an_input():
    with pytest.raises(ValueError):
        lfsr()


# ---------------------------------------------------------------------------
# effective_num_tests and expected-max sanity (authority A2 anchors)
# ---------------------------------------------------------------------------
def test_effective_num_tests_independent_equals_n():
    cov = np.eye(24)
    assert effective_num_tests_from_cov(cov) == pytest.approx(24.0, abs=1e-9)


def test_effective_num_tests_perfectly_correlated_is_one():
    R = np.ones((10, 10))
    assert effective_num_tests_from_cov(R) == pytest.approx(1.0, abs=1e-6)


def test_expected_max_anchors_from_authority():
    # Authority A2: N=24 ~ 1.79, N=288 ~ 2.73.
    assert expected_max_standard_normal(24) == pytest.approx(1.79, abs=0.1)
    assert expected_max_standard_normal(288) == pytest.approx(2.73, abs=0.1)


# ---------------------------------------------------------------------------
# Family-clustered tau^2 (Fable failure-mode (i))
# ---------------------------------------------------------------------------
def test_family_clustering_shrinks_harder_than_naive_pooling():
    """Within-family anticorrelation must not masquerade as prior signal.

    Construct mutually-exclusive families whose member edges sum to ~0 (strong
    within-family anticorrelation) with NO between-family signal. Naive pooling
    sees large cross-sectional variance and under-shrinks; family-aware tau^2
    (subtracting average within-family covariance) shrinks at least as hard.
    """
    rng = np.random.default_rng(99)
    families: list[int] = []
    e_list: list[float] = []
    for fam in range(30):
        a = rng.normal(0.0, 2.0)
        # two anticorrelated bins: b ~ -a (mutually exclusive sum-to-one shape)
        b = -a + rng.normal(0.0, 0.1)
        e_list += [a, b]
        families += [fam, fam]
    e_hat = np.array(e_list)
    s = np.full(e_hat.size, 0.5)

    res_naive = eb_shrink_edges(e_hat, s)
    res_family = eb_shrink_edges(e_hat, s, families=families)

    # Family-aware fit must not OVER-state prior variance vs the naive fit.
    assert res_family.tau2 <= res_naive.tau2 + 1e-9
    # And it shrinks the selected max at least as hard.
    assert float(np.max(res_family.shrunk_mean)) <= float(np.max(res_naive.shrunk_mean)) + 1e-6


# ---------------------------------------------------------------------------
# Tweedie license boundary (authority D3: N >= 200)
# ---------------------------------------------------------------------------
def test_tweedie_below_license_raises():
    e_hat = np.linspace(-1, 1, TWEEDIE_MIN_CANDIDATES - 1)
    s = np.full(e_hat.size, 1.0)
    with pytest.raises(AssertionError):
        tweedie_shrink(e_hat, s)


def test_tweedie_at_license_runs_and_corrects_toward_mean():
    rng = np.random.default_rng(3)
    N = 400
    e_hat = rng.normal(0.0, 1.0, size=N)  # null universe
    s = np.full(N, 1.0)
    corrected = tweedie_shrink(e_hat, s)
    assert corrected.shape == (N,)
    # Tweedie pulls the selection-inflated max back below the raw max.
    assert float(np.max(corrected)) < float(np.max(e_hat))


# ---------------------------------------------------------------------------
# Posterior-utility license
# ---------------------------------------------------------------------------
def test_license_passes_confident_growth_positive_edge():
    # q=0.86 vs price=0.72 (the Wuhan/Ankara NO harvest from the autopsy):
    # confident, growth-positive -> licensed.
    lic = select_license(
        edge_shrunk=0.14,
        edge_shrunk_posterior_sd=0.03,
        q_posterior=0.86,
        price=0.72,
        e_min=0.0,
        pi_min=0.90,
    )
    assert lic.licensed is True
    assert lic.reason == "PASS"
    assert lic.expected_log_growth > 0.0
    assert lic.kelly_fraction > 0.0


def test_license_blocks_low_probability_edge():
    # Wide posterior: P(e>0) < 0.90 even though the point edge is positive.
    lic = select_license(
        edge_shrunk=0.02,
        edge_shrunk_posterior_sd=0.10,
        q_posterior=0.55,
        price=0.53,
        e_min=0.0,
        pi_min=0.90,
    )
    assert lic.licensed is False
    assert lic.reason.startswith("PROB_EDGE_BELOW_PI_MIN")


def test_license_blocks_non_positive_growth():
    # Edge confidently positive but price so high Kelly growth is ~0/negative
    # is hard to construct cleanly; instead use no real edge (q<=price) so
    # growth is non-positive while we force prob_edge high via tight sd.
    lic = select_license(
        edge_shrunk=0.001,
        edge_shrunk_posterior_sd=1e-6,
        q_posterior=0.50,
        price=0.50,
        e_min=0.0,
        pi_min=0.90,
    )
    assert lic.licensed is False
    assert lic.reason.startswith("NON_POSITIVE_LOG_GROWTH")


def test_license_samples_path_agrees_with_normal_approx():
    rng = np.random.default_rng(11)
    samples = rng.normal(0.14, 0.03, size=50_000)
    lic_s = select_license(
        edge_shrunk=0.14,
        edge_shrunk_posterior_sd=0.03,
        q_posterior=0.86,
        price=0.72,
        edge_samples=samples,
    )
    lic_n = select_license(
        edge_shrunk=0.14,
        edge_shrunk_posterior_sd=0.03,
        q_posterior=0.86,
        price=0.72,
    )
    assert lic_s.prob_edge == pytest.approx(lic_n.prob_edge, abs=0.02)
    assert lic_s.licensed == lic_n.licensed


def test_kelly_and_growth_are_consistent():
    f = float(kelly_fraction_binary(0.86, 0.72))
    g = float(expected_log_growth_binary(0.86, 0.72, f))
    assert f > 0.0
    assert g > 0.0
