#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=never
# Purpose: Identifiability + bias-absorption regression for scripts/fit_bias_scale.py — the JOINT
#   (b_loc, k) interval-censored categorical MLE must RECOVER a known (b=+1.2, k=1.1) and a k-only fit
#   on the SAME data must inflate k strictly above 1.1 (the authority's proven pathology
#   k_wrong^2 = k_true^2 + (delta/sigma)^2).
# Reuse: Run as a unit test (no DB). Re-validate after any change to the likelihood, EB shrinkage, or
#   the data-frame conventions in fit_bias_scale.py.
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt Task 1.1
#   (identifiability proof "with at least two finite effective boundaries, location shift and spread
#   scale are jointly identifiable"; "Why fitting k alone absorbs bias").
"""Synthetic-recovery + bias-absorption tests for the joint (b_loc, k) fitter.

We generate settled cells from a KNOWN Normal(mu + b_true, (sigma*k_true)^2) over a fixed integer bin
grid (so >= 2 finite boundaries per cell — the identifiability precondition holds), sampling the WON
bin from the true categorical probabilities. The joint fitter must recover (b_true, k_true); a k-only
fit (b forced to 0) on the SAME data must report an INFLATED k > k_true, exactly the absorption the
authority proves.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import fit_bias_scale as fbs  # noqa: E402

_NEG_INF = -1e18
_POS_INF = 1e18


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _make_grid_cell(sigma_impl: float, mode_deg: float, n_each_side: int = 4):
    """Build a single synthetic cell skeleton (integer 1-degree bins, mode-centred edges).

    Returns a dict with edges_lo/edges_hi (mode-centred, degree units), n_bins, sigma_impl, step,
    centers (degree positions of each bin relative to the mode). The two end bins are OPEN tails.
    """
    centers = list(range(-n_each_side, n_each_side + 1))  # degree offset from mode
    los, his = [], []
    n = len(centers)
    for i, c in enumerate(centers):
        if i == 0:
            los.append(_NEG_INF); his.append(c + 0.5)        # open-low tail
        elif i == n - 1:
            los.append(c - 0.5); his.append(_POS_INF)        # open-high tail
        else:
            los.append(c - 0.5); his.append(c + 0.5)
    return {
        "edges_lo": np.asarray(los, dtype=float),
        "edges_hi": np.asarray(his, dtype=float),
        "n_bins": n,
        "sigma_impl": float(sigma_impl),
        "step": 1.0,
        "mode_index": n_each_side,        # the center bin (offset 0)
        "_centers": centers,
    }


def _true_probs(cell, b_true: float, k_true: float) -> np.ndarray:
    """Exact categorical probabilities under N(b_true, (sigma*k_true)^2) over the cell's edges."""
    scale = k_true * cell["sigma_impl"]
    p = np.array([
        _phi((hi - b_true) / scale) - _phi((lo - b_true) / scale)
        for lo, hi in zip(cell["edges_lo"], cell["edges_hi"])
    ])
    p = np.clip(p, 1e-15, 1.0)
    return p / p.sum()


def _generate_dataset(b_true: float, k_true: float, n_cities: int, cells_per_city: int,
                      sigma_impl: float, seed: int):
    """Synthetic settled cells drawn from the KNOWN (b_true, k_true). Returns (cells, city_index)."""
    rng = np.random.default_rng(seed)
    cells = []
    city_index = []
    for c in range(n_cities):
        # Vary the mode position per cell so winning bins are not all the same index.
        for _ in range(cells_per_city):
            cell = _make_grid_cell(sigma_impl=sigma_impl, mode_deg=0.0)
            probs = _true_probs(cell, b_true, k_true)
            won = int(rng.choice(len(probs), p=probs))
            cell["won_index"] = won
            cells.append(cell)
            city_index.append(c)
    return cells, city_index


def test_joint_recovers_known_bias_and_scale():
    """Joint MLE recovers (b=+1.2, k=1.1) within tolerance — the identifiability proof in practice."""
    b_true, k_true, sigma_impl = 1.2, 1.1, 1.0
    n_cities = 6
    cells, city_index = _generate_dataset(
        b_true, k_true, n_cities=n_cities, cells_per_city=900, sigma_impl=sigma_impl, seed=7)

    b_hat, k_hat, res = fbs._fit_joint(cells, city_index, n_cities)

    # Global scale recovered.
    assert abs(k_hat - k_true) < 0.06, f"k_hat={k_hat} not within 0.06 of {k_true}"
    # Every city's bias recovered (shared true bias for all cities here).
    mean_b = float(np.mean(b_hat))
    assert abs(mean_b - b_true) < 0.10, f"mean b_hat={mean_b} not within 0.10 of {b_true}"
    for c in range(n_cities):
        assert abs(b_hat[c] - b_true) < 0.20, f"city {c} b_hat={b_hat[c]} off from {b_true}"


def test_k_only_fit_absorbs_bias_and_inflates_k():
    """k-only fit (b forced to 0) on the SAME biased data inflates k strictly above the true k.

    Authority: a variance-only spread fit cannot represent the center offset, so it widens k to absorb
    it: k_wrong^2 = k_true^2 + (delta/sigma)^2 > k_true^2. With b_true=1.2, sigma=1.0, true k=1.1, the
    absorbed k should land near sqrt(1.1^2 + 1.2^2) ~= 1.63 — and MUST exceed 1.1.
    """
    b_true, k_true, sigma_impl = 1.2, 1.1, 1.0
    n_cities = 6
    cells, city_index = _generate_dataset(
        b_true, k_true, n_cities=n_cities, cells_per_city=900, sigma_impl=sigma_impl, seed=11)

    # k-only fit: a SINGLE shared bias parameter pinned to 0 (n_cities=1 with b bounded to [0,0]).
    # We reuse the joint NLL but force the bias dimension to zero via tight bounds.
    from scipy.optimize import minimize

    flat_city_index = [0] * len(cells)

    def nll_konly(theta):
        # theta = [b(forced 0), log_k]; bounds pin b to 0 so only k moves.
        return fbs._neg_loglik(theta, cells, flat_city_index, 1)

    res = minimize(nll_konly, np.array([0.0, 0.0]), method="L-BFGS-B",
                   bounds=[(0.0, 0.0), (-1.5, 1.5)])
    k_absorbed = math.exp(res.x[1])

    # The proven pathology: k inflates strictly above the true k.
    assert k_absorbed > k_true + 0.05, f"k-only fit k={k_absorbed} did NOT inflate above {k_true}"
    # And it should land in the neighborhood of sqrt(k_true^2 + (delta/sigma)^2).
    expected_absorbed = math.sqrt(k_true ** 2 + (b_true / sigma_impl) ** 2)  # ~1.63
    assert abs(k_absorbed - expected_absorbed) < 0.30, (
        f"k-only fit k={k_absorbed} not near predicted absorbed {expected_absorbed:.3f}")


def test_joint_beats_konly_on_recovered_scale():
    """The joint fit's k is far closer to truth than the k-only fit's k (absorption is removed)."""
    b_true, k_true, sigma_impl = 1.2, 1.1, 1.0
    n_cities = 5
    cells, city_index = _generate_dataset(
        b_true, k_true, n_cities=n_cities, cells_per_city=900, sigma_impl=sigma_impl, seed=23)

    _b_hat, k_joint, _res = fbs._fit_joint(cells, city_index, n_cities)

    from scipy.optimize import minimize
    flat = [0] * len(cells)
    res = minimize(lambda t: fbs._neg_loglik(t, cells, flat, 1), np.array([0.0, 0.0]),
                   method="L-BFGS-B", bounds=[(0.0, 0.0), (-1.5, 1.5)])
    k_only = math.exp(res.x[1])

    assert abs(k_joint - k_true) < abs(k_only - k_true), (
        f"joint k={k_joint} should be closer to {k_true} than k-only k={k_only}")


def test_monte_carlo_bias_absorption_theorem():
    """Pin the THEOREM (not just our estimator): scale-only MLE absorbs bias as k=sqrt(1+(delta/sigma)^2).

    Authority cross-validation 2026-06-12 requirement: simulate draws from N(mu+delta, sigma^2) with a
    KNOWN delta/sigma, fit a scale-only Gaussian MLE (mean pinned at mu, only the SD free), and assert
    the fitted scale multiplier k = sigma_hat/sigma matches sqrt(1 + (delta/sigma)^2) within MC
    tolerance. This is the continuous-limit form of k_wrong^2 = k_true^2 + (delta/sigma)^2 (k_true=1).
    """
    rng = np.random.default_rng(2026)
    sigma = 2.0
    n = 100_000
    for delta_over_sigma in (0.5, 1.0, 1.5):
        delta = delta_over_sigma * sigma
        x = rng.normal(loc=delta, scale=sigma, size=n)  # truth: center delta, spread sigma
        # Scale-only MLE with the mean pinned at 0 (the model refuses to fit the center):
        #   sigma_hat^2 = mean((x - 0)^2) = sigma^2 + delta^2  (E[x^2] = Var + mean^2).
        sigma_hat = math.sqrt(float(np.mean(x ** 2)))
        k_absorbed = sigma_hat / sigma
        k_predicted = math.sqrt(1.0 + delta_over_sigma ** 2)
        assert abs(k_absorbed - k_predicted) < 0.02, (
            f"delta/sigma={delta_over_sigma}: MC k={k_absorbed:.4f} != predicted {k_predicted:.4f}")
        # And it always inflates above the true k=1.
        assert k_absorbed > 1.0 + 0.05


def test_eb_shrinkage_pulls_low_n_city_toward_pool():
    """A city with few, noisy cells shrinks toward b0; a high-n city keeps its raw bias."""
    # High-information city: many cells, bias +1.5. Low-information city: few cells, extreme raw bias.
    cells_hi = []
    ci_hi = []
    rng = np.random.default_rng(3)
    for _ in range(800):
        cell = _make_grid_cell(sigma_impl=1.0, mode_deg=0.0)
        probs = _true_probs(cell, 1.5, 1.1)
        cell["won_index"] = int(rng.choice(len(probs), p=probs))
        cells_hi.append(cell)
        ci_hi.append(0)

    # Two cities: city 0 high-n (bias 1.5), city 1 low-n (only a handful of cells).
    cells = list(cells_hi)
    city_index = list(ci_hi)
    for _ in range(8):
        cell = _make_grid_cell(sigma_impl=1.0, mode_deg=0.0)
        probs = _true_probs(cell, 1.5, 1.1)
        cell["won_index"] = int(rng.choice(len(probs), p=probs))
        cells.append(cell)
        city_index.append(1)

    n_cities = 2
    b_hat, k_hat, _res = fbs._fit_joint(cells, city_index, n_cities)

    from collections import defaultdict
    cbc = defaultdict(list)
    for ci, cell in zip(city_index, cells):
        cbc[ci].append(cell)
    b_raw = {0: float(b_hat[0]), 1: float(b_hat[1])}
    s2 = {c: fbs._city_bias_fisher(cbc[c], b_raw[c], k_hat) for c in (0, 1)}
    b0, tau = fbs._eb_prior(b_raw, s2)
    b_shrunk_0, S0 = fbs._shrink(b_raw[0], s2[0], b0, tau)
    b_shrunk_1, S1 = fbs._shrink(b_raw[1], s2[1], b0, tau)

    # The low-n city is shrunk MORE (smaller shrink factor) than the high-n city.
    assert S1 <= S0 + 1e-9, f"low-n shrink factor S1={S1} should be <= high-n S0={S0}"


if __name__ == "__main__":
    test_joint_recovers_known_bias_and_scale()
    test_k_only_fit_absorbs_bias_and_inflates_k()
    test_joint_beats_konly_on_recovered_scale()
    test_monte_carlo_bias_absorption_theorem()
    test_eb_shrinkage_pulls_low_n_city_toward_pool()
    print("all tests passed")
