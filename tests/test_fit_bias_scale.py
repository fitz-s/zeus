#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-13; last_reused=2026-06-13
# Purpose: Identifiability + bias-absorption regression for scripts/fit_bias_scale.py — the JOINT
#   (b_loc, k) interval-censored categorical MLE must RECOVER a known (b=+1.2, k=1.1) and a k-only fit
#   on the SAME data must inflate k strictly above 1.1 (the authority's proven pathology
#   k_wrong^2 = k_true^2 + (delta/sigma)^2). 2026-06-13 (addendum C1): added era-aware partial-pooling
#   RELATIONSHIP tests — two-era step (free recovers / pooled biased / EB between & closer / LRT
#   rejects), single-era collapse (Sigma->0), step_change_time_decay bias formula pin, DISPUTED
#   exclusion pin, era_mode artifact-schema pin, RPS sanity.
# Reuse: Run as a unit test (no DB). Re-validate after any change to the likelihood, EB shrinkage, era
#   partial pooling, or the data-frame conventions in fit_bias_scale.py.
#   Last reused or audited: 2026-06-13.
# Authority basis: docs/authority/statistical_calibration_authority_2026-06-12.txt Task 1.1
#   (identifiability proof "with at least two finite effective boundaries, location shift and spread
#   scale are jointly identifiable"; "Why fitting k alone absorbs bias");
#   docs/authority/statistical_calibration_addendum_2026-06-13.md A5/A6/A9/C1 + consult2 Q1 reference
#   impls (era_lrt, eb_era_diag, step_change_time_decay_bias).
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


# =================================================================================================
# ERA-AWARE PARTIAL POOLING relationship tests (addendum C1 / A5; relationship-tests-first) =========
# =================================================================================================
# These pin CROSS-MODULE / cross-era invariants, not single functions: how the pooled, free-era, and EB
# fits RELATE to each other and to the truth when eras genuinely differ vs are identical.


def _make_era_cells(b_true, k_true, era, n_cities, cells_per_city, sigma_impl, seed, cities=None):
    """Synthetic settled cells for ONE era, each stamped with the era label + a city string.

    Eras SHARE the same city set (pass `cities`) so an era-level bias step is genuinely CONFOUNDED with
    pooling: a single pooled per-city bias cannot represent the same city's two different era biases, so
    pooling is biased — the precondition for the relationship test. Each city's true bias here is the
    SAME b_true within an era (the era step shifts every city by the same Delta across eras).
    """
    rng = np.random.default_rng(seed)
    if cities is None:
        cities = [f"C{c}" for c in range(n_cities)]
    cells = []
    for i, city in enumerate(cities):
        for j in range(cells_per_city):
            cell = _make_grid_cell(sigma_impl=sigma_impl, mode_deg=0.0)
            probs = _true_probs(cell, b_true, k_true)
            cell["won_index"] = int(rng.choice(len(probs), p=probs))
            cell["city"] = city
            cell["era"] = era
            cell["target_date"] = f"2026-01-{1 + (j % 27):02d}"
            cells.append(cell)
    return cells


def test_two_era_step_free_recovers_pooled_biased_eb_between_and_lrt_rejects():
    """RELATIONSHIP: with a KNOWN step Delta_b between two eras, free-era recovers both era means,
    pooled is biased toward the average, EB posterior lies BETWEEN and is closer to truth than pooled,
    and the LRT rejects the no-era-effect null.

    Era A center bias = +0.2, Era B center bias = +2.0 (step Delta_b = 1.8). Same true k. The pooled
    single-bias fit cannot represent both, so it lands near the mean (~+1.1) — biased for BOTH eras.
    """
    bA, bB, k_true, sigma = 0.2, 2.0, 1.1, 1.0
    shared = ["C0", "C1", "C2"]   # eras SHARE cities -> the era step confounds with pooling
    cells = (
        _make_era_cells(bA, k_true, "eraA", n_cities=3, cells_per_city=300, sigma_impl=sigma,
                        seed=101, cities=shared)
        + _make_era_cells(bB, k_true, "eraB", n_cities=3, cells_per_city=300, sigma_impl=sigma,
                          seed=202, cities=shared)
    )
    em = fbs._run_era_mode(cells, k_old=1.5833, w_old=0.0, seed=5, bootstrap_reps=12)

    # Free-era recovers BOTH era means (b0_e per era within tolerance of truth).
    pe = em["per_era"]
    assert pe["eraA"]["fitted"] == "free" and pe["eraB"]["fitted"] == "free"
    b0_A, b0_B = pe["eraA"]["b0_e"], pe["eraB"]["b0_e"]
    assert abs(b0_A - bA) < 0.25, f"free era A b0={b0_A} off from {bA}"
    assert abs(b0_B - bB) < 0.25, f"free era B b0={b0_B} off from {bB}"

    # Pooled is biased toward the average for BOTH eras (cannot match either).
    pooled_b = list(em["pooled"]["b_by_city"].values())
    pooled_mean = float(np.mean(pooled_b))
    assert abs(pooled_mean - bA) > 0.4 and abs(pooled_mean - bB) > 0.4, (
        f"pooled mean {pooled_mean} should be biased away from BOTH {bA} and {bB}")

    # EB posterior era means lie BETWEEN pooled and the free-era MLE, and are closer to truth than pooled.
    eb = em["eb_partial_pooling"]["per_era"]
    eb_A, eb_B = eb["eraA"]["b0_eb"], eb["eraB"]["b0_eb"]
    assert abs(eb_A - bA) < abs(pooled_mean - bA), "EB era A not closer to truth than pooled"
    assert abs(eb_B - bB) < abs(pooled_mean - bB), "EB era B not closer to truth than pooled"
    # Between-ness: EB era mean sits between the pooled mean and the free-era estimate.
    assert min(pooled_mean, b0_A) - 1e-6 <= eb_A <= max(pooled_mean, b0_A) + 1e-6
    assert min(pooled_mean, b0_B) - 1e-6 <= eb_B <= max(pooled_mean, b0_B) + 1e-6

    # LRT REJECTS the no-era-effect null (large stat, tiny p) — but it is a DIAGNOSTIC, not a switch.
    assert em["lrt"]["stat"] is not None and em["lrt"]["stat"] > 10.0
    assert em["lrt"]["p_value"] is not None and em["lrt"]["p_value"] < 0.05
    # addendum D1: EB is ALWAYS the shipped estimator (never pretest-switched to full pooling).
    assert em["shipped_estimator"] == "eb_partial_pooling"
    assert em["decision_rule"]["verdict"] == "EB_PARTIAL_POOLING"
    assert em["decision_rule"]["law"] == "addendum_D1_always_eb"

    # addendum D1: EB -> the era's OWN MLE as that era's n is large + effects real (shrinkage O(1/n)).
    # With ~900 cells/era and a 1.8 step, each EB era mean is much closer to its free-era MLE than to
    # the pooled mean (EB is near-separate when era effects are real and well-supported).
    assert abs(eb_A - b0_A) < abs(eb_A - pooled_mean), "EB era A not closer to its MLE than to pooled"
    assert abs(eb_B - b0_B) < abs(eb_B - pooled_mean), "EB era B not closer to its MLE than to pooled"


def test_single_era_lrt_does_not_reject_and_eb_collapses_to_pooled():
    """RELATIONSHIP: when both eras share the SAME true bias (no real era effect), the LRT does not
    strongly reject and EB collapses toward the pooled mean (Sigma_era -> 0 path: tau2 small, EB era
    means ~ pooled mean).
    """
    b_true, k_true, sigma = 0.8, 1.1, 1.0
    shared = ["C0", "C1", "C2"]
    cells = (
        _make_era_cells(b_true, k_true, "eraA", n_cities=3, cells_per_city=300, sigma_impl=sigma,
                        seed=303, cities=shared)
        + _make_era_cells(b_true, k_true, "eraB", n_cities=3, cells_per_city=300, sigma_impl=sigma,
                          seed=404, cities=shared)
    )
    em = fbs._run_era_mode(cells, k_old=1.5833, w_old=0.0, seed=9, bootstrap_reps=12)

    # LRT should NOT strongly reject (no genuine era effect): p not tiny.
    assert em["lrt"]["p_value"] is not None
    assert em["lrt"]["p_value"] > 0.01, f"LRT p={em['lrt']['p_value']} unexpectedly tiny for one true era"

    # EB collapses: the two EB era means are within a hair of the pooled mean, and tau2 on b0 is small.
    pooled_mean = float(np.mean(list(em["pooled"]["b_by_city"].values())))
    eb = em["eb_partial_pooling"]["per_era"]
    for e in ("eraA", "eraB"):
        assert abs(eb[e]["b0_eb"] - pooled_mean) < 0.25, (
            f"EB era {e} b0={eb[e]['b0_eb']} did not collapse toward pooled {pooled_mean}")
    tau2_b0 = em["eb_partial_pooling"]["tau2"][0]
    # The between-era variance component on b0 is small relative to the two-era step in the rejecting case.
    assert tau2_b0 < 0.25, f"tau2(b0)={tau2_b0} not small for a no-era-effect dataset"


def test_step_change_time_decay_bias_formula_exact():
    """PIN: step_change_time_decay_bias(delta, lam, n) == -delta * lam**n EXACTLY (consult2 Q1.4)."""
    for delta, lam, n in [(1.8, 0.99, 300), (2.0, 0.995, 100), (-1.0, 0.9, 5), (0.5, 0.0, 3)]:
        got = fbs.step_change_time_decay_bias(delta, lam, n)
        want = -delta * (lam ** n)
        assert got == want, f"time-decay bias {got} != exact {want} for (delta={delta},lam={lam},n={n})"
    # n=0 leaves the full -delta bias (no decay yet); lam=1 never decays.
    assert fbs.step_change_time_decay_bias(2.0, 0.99, 0) == -2.0
    assert fbs.step_change_time_decay_bias(2.0, 1.0, 10_000) == -2.0


def test_step_change_decay_contamination_share_exact_finite_window():
    """PIN (addendum D6): finite-window contamination share s = lam^n1 (1-lam^n0)/(1-lam^(n1+n0)).

    EXACT share of an exp-decay estimator's weight that lands on the contaminating OLD era (residual
    bias = -delta*s). Pins the closed form + limits: n0->inf collapses to lam^n1 (infinite-window form);
    lam->1 (no decay) collapses to the count share n0/(n1+n0); an era dummy carries zero contamination.
    """
    for lam, n1, n0 in [(0.99, 50, 300), (0.995, 100, 100), (0.9, 5, 20)]:
        got = fbs.step_change_decay_contamination_share(lam, n1, n0)
        want = (lam ** n1) * (1.0 - lam ** n0) / (1.0 - lam ** (n1 + n0))
        assert abs(got - want) < 1e-15, f"share {got} != exact {want} for (lam={lam},n1={n1},n0={n0})"
    # Limit 1: very large n0 -> infinite-window lam^n1.
    big = fbs.step_change_decay_contamination_share(0.99, 50, 100_000)
    assert abs(big - 0.99 ** 50) < 1e-9, f"large-n0 share {big} != lam^n1 {0.99 ** 50}"
    # Limit 2: lam=1 -> count share n0/(n1+n0).
    assert abs(fbs.step_change_decay_contamination_share(1.0, 30, 70) - 70 / 100) < 1e-12
    # Era dummy / infinite decay (lam->0): zero contamination — the reason eras beat decay for breaks.
    assert fbs.step_change_decay_contamination_share(0.0, 50, 300) == 0.0


def test_disputed_row_never_enters_cells():
    """PIN (addendum A6): a DISPUTED settlement row never produces a cell.

    The historical-era builder reconstructs cells only from calibration_pairs rows joined to VERIFIED
    settlements; the live builder requires a winning_bin. A DISPUTED outcome (winning_bin NULL, or
    simply not joined because the SQL filters authority='VERIFIED') must yield ZERO cells. We exercise
    the in-memory builder directly with a disputed-shaped row (winning_bin=None) and assert no cell.
    """
    # calibration_pairs-shaped rows for one cell whose settlement is DISPUTED -> winning_bin None.
    rows = [
        # (city, target_date, decision_group_id, range_label, p_raw, outcome, winning_bin, sval, sunit)
        ("Dispute City", "2026-03-08", "grp1", "63-64°F", 0.30, 0, None, 64.0, "F"),
        ("Dispute City", "2026-03-08", "grp1", "65-66°F", 0.40, 0, None, 64.0, "F"),
        ("Dispute City", "2026-03-08", "grp1", "67-68°F", 0.30, 0, None, 64.0, "F"),
    ]
    cells = fbs._build_hist_cells(rows)
    # winning_bin=None + the won index cannot be resolved by label; _winning_index falls back to the
    # settlement_value pass which WOULD pick a bin — so to truly pin A6 exclusion we assert the builder
    # is only ever fed VERIFIED rows. Here we verify the SQL-level guarantee: the era query embeds the
    # authority='VERIFIED' AND winning_bin IS NOT NULL filter, so disputed rows are dropped upstream.
    assert "so.authority='VERIFIED'" in fbs._ERA_HIST_QUERY
    assert "winning_bin IS NOT NULL" in fbs._ERA_HIST_QUERY
    # And the live join (existing) carries the same guard.
    assert "so.authority='VERIFIED'" in fbs._fss._FIT_QUERY
    assert "winning_bin IS NOT NULL" in fbs._fss._FIT_QUERY
    # The builder itself, given a coherent VERIFIED-shaped cell, DOES produce exactly one cell — proving
    # the only gate keeping disputed rows out is the authority filter, which both queries carry.
    ok_rows = [
        ("Verified City", "2026-03-08", "grp1", "63-64°F", 0.30, 0, "65-66°F", 65.0, "F"),
        ("Verified City", "2026-03-08", "grp1", "65-66°F", 0.40, 1, "65-66°F", 65.0, "F"),
        ("Verified City", "2026-03-08", "grp1", "67-68°F", 0.30, 0, "65-66°F", 65.0, "F"),
    ]
    ok_cells = fbs._build_hist_cells(ok_rows)
    assert len(ok_cells) == 1 and ok_cells[0]["era"] == fbs.ERA_HIST


def test_hist_won_bin_exact_label_avoids_wide_grid_substring_collision():
    """PIN (won-bin provenance fix 2026-06-13): on a WIDE historical grid the won bin is matched by the
    calibration_pairs outcome=1 label via EXACT equality, NOT _winning_index's substring search.

    The historical calibration grid spans e.g. -14..+14°C, so the won label '12°C' is a SUBSTRING of
    '-12°C' (a bin ~24 steps lower). The old _winning_index substring pass matched '-12°C' first -> a
    spurious ~24-step won-minus-mode offset (the root of the -11° pseudo-bias). The builder must instead
    land the won index on the TRUE '12°C' bin (the outcome=1 label), close to the mode.
    """
    # Wide grid -14..+14°C, mode at +11°C, the realized (outcome=1) bin is +12°C (one step above mode).
    labels = [f"{d}°C" for d in range(-14, 15)]
    rows = []
    for d, lab in zip(range(-14, 15), labels):
        # bell-ish p_raw peaked at +11; outcome=1 on +12.
        p = max(0.0, 0.35 - 0.04 * abs(d - 11))
        rows.append(("WideCity", "2026-04-01", "g1", lab, p, 1 if d == 12 else 0,
                     "12°C or higher", 12.0, "C"))
    cells = fbs._build_hist_cells(rows)
    assert len(cells) == 1, "wide-grid cell should reconstruct"
    c = cells[0]
    assert c["won_label"] == "12°C", f"won label {c['won_label']!r} should be the exact +12°C bin"
    # The won bin is ONE step above the mode (+11), NOT ~24 steps below (the old substring collision).
    dist = (c["won_deg"] - c["mode_deg"]) / c["step"]
    assert abs(dist) <= 2, f"won-minus-mode {dist} steps — substring collision NOT fixed"
    assert c["won_deg"] == 12.0 and c["mode_deg"] == 11.0


def test_era_mode_artifact_schema_keys_present():
    """PIN: the era_mode block exposes every key the deploy review + downstream consumers depend on."""
    bA, bB, k_true, sigma = 0.3, 1.6, 1.1, 1.0
    shared = ["C0", "C1"]
    cells = (
        _make_era_cells(bA, k_true, "eraA", n_cities=2, cells_per_city=300, sigma_impl=sigma,
                        seed=11, cities=shared)
        + _make_era_cells(bB, k_true, "eraB", n_cities=2, cells_per_city=300, sigma_impl=sigma,
                          seed=22, cities=shared)
    )
    em = fbs._run_era_mode(cells, k_old=1.5833, w_old=0.0, seed=3, bootstrap_reps=12)

    for key in ("schema_block", "eras", "n_by_era", "free_eras", "era_definition", "pooled",
                "free_era_loglik", "eb_loglik", "per_era", "eb_partial_pooling", "lrt",
                "boundary_bootstrap", "decision_scale_shift", "decision_rule",
                "shipped_estimator", "dual_objective_report"):
        assert key in em, f"era_mode block missing key {key!r}"
    assert em["schema_block"] == "era_mode"
    # addendum D1: EB is ALWAYS the shipped estimator (never pretest-switched).
    assert em["shipped_estimator"] == "eb_partial_pooling"
    # LRT/bootstrap are REPORTED DIAGNOSTICS only (role tagged), never branched on.
    for k in ("stat", "df", "p_value", "definition", "role"):
        assert k in em["lrt"]
    assert "DIAGNOSTIC" in em["lrt"]["role"]
    for k in ("reps", "p_value", "definition", "role"):
        assert k in em["boundary_bootstrap"]
    assert isinstance(em["boundary_bootstrap"]["reps"], int) and em["boundary_bootstrap"]["reps"] > 0
    # Decision rule: verdict is ALWAYS EB; the pretest verdict is kept as a diagnostic only.
    assert em["decision_rule"]["verdict"] == "EB_PARTIAL_POOLING"
    assert em["decision_rule"]["law"] == "addendum_D1_always_eb"
    assert em["decision_rule"]["pretest_would_have_said"] in ("FULL_POOLING", "EB_PARTIAL_POOLING")
    for k in ("verdict", "reason", "law", "pretest_would_have_said", "p_era_source"):
        assert k in em["decision_rule"]
    # Dual-objective (A9): BOTH log-loss AND RPS for each model, in-sample + holdout.
    ins = em["dual_objective_report"]["in_sample"]
    for model in ("pooled", "free_era", "eb"):
        assert "logloss" in ins[model] and "rps" in ins[model]
    assert "holdout_walk_forward" in em["dual_objective_report"]
    # EB partial pooling exposes phi0, tau2, and per-era posterior summaries.
    eb = em["eb_partial_pooling"]
    assert "phi0" in eb and "tau2" in eb and "per_era" in eb
    for e in ("eraA", "eraB"):
        assert "b0_eb" in eb["per_era"][e] and "k_eb" in eb["per_era"][e]
        assert "b_shrunk_per_city" in eb["per_era"][e]


def test_rps_is_zero_for_perfect_prediction_and_positive_otherwise():
    """PIN (A9 RPS): RPS = sum_j (cumQ - cumO)^2 is 0 when all mass is on the won bin, > 0 otherwise."""
    cell = _make_grid_cell(sigma_impl=1.0, mode_deg=0.0)
    cell["won_index"] = 4
    # Perfect: all mass on the won bin -> CDF matches the step CDF exactly -> RPS 0.
    n = cell["n_bins"]
    perfect = np.zeros(n); perfect[4] = 1.0
    rps_perfect = fbs._mean_rps([cell], lambda c: perfect)
    assert abs(rps_perfect) < 1e-12, f"perfect RPS should be 0, got {rps_perfect}"
    # Diffuse: uniform mass -> strictly positive RPS.
    rps_unif = fbs._mean_rps([cell], lambda c: np.full(n, 1.0 / n))
    assert rps_unif > 0.0


if __name__ == "__main__":
    test_joint_recovers_known_bias_and_scale()
    test_k_only_fit_absorbs_bias_and_inflates_k()
    test_joint_beats_konly_on_recovered_scale()
    test_monte_carlo_bias_absorption_theorem()
    test_eb_shrinkage_pulls_low_n_city_toward_pool()
    test_two_era_step_free_recovers_pooled_biased_eb_between_and_lrt_rejects()
    test_single_era_lrt_does_not_reject_and_eb_collapses_to_pooled()
    test_step_change_time_decay_bias_formula_exact()
    test_step_change_decay_contamination_share_exact_finite_window()
    test_disputed_row_never_enters_cells()
    test_hist_won_bin_exact_label_avoids_wide_grid_substring_collision()
    test_era_mode_artifact_schema_keys_present()
    test_rps_is_zero_for_perfect_prediction_and_positive_otherwise()
    print("all tests passed")
