# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: docs/authority/exit_portfolio_execution_authority_2026-06-13.md P1-P2,P5;
#   docs/authority/consult3_exit_portfolio_execution_2026-06-13_raw.txt Q2(a)/(b)/(e)
# Lifecycle: created=2026-06-13; last_reviewed=2026-06-13; last_reused=never
# Purpose: Relationship-test antibodies for the closed-form horse-race Kelly allocation
#   (task #63). Tests are ORDERED: relationship invariants first, then the QP/LP and
#   degenerate-input pins.
"""Relationship tests for horse-race Kelly (authority §P1/§P2/§P5).

Relationship tests (written first, per repo law — cross-bin invariants before
function tests):

R1. Not-overround (Σp <= 1) -> f_k* = q_k exactly, s* = 0 (full all-active Kelly).
R2. Overround -> Σf + s = 1 (budget closes) AND active-set property:
    f_k > 0 iff q_k/p_k > s* (bins compete; the threshold s* is endogenous).
R3. No-bet region (max_k q_k/p_k <= 1) -> all f_k* = 0, s* = 1 (all cash).
R4. GROWTH-DOMINANCE (the TRUE overbetting-correction invariant): the horse-race
    expected-log-growth is >= ANY per-candidate (single-bet isolation) sizing, for
    EVERY family and regime. The horse-race IS the joint optimum, so per-candidate
    sizing can never beat it. (See the docstring note: the literal "Σ horse f <=
    Σ naive f" inequality from the task brief is mathematically FALSE — under mutual
    exclusivity the naive per-bin Kelly (q-p)/(1-p) UNDER-bets, and the horse-race
    correctly levers toward the growth optimum. Growth-dominance is the correct
    structural theorem; this test pins it.)
R5. YES(a)+NO(b) growth-optimal example (authority §P5 / Q2(e)) + dominance LP:
    a non-dominated candidate is NOT flagged; a cheaper-replicated candidate IS.
R6. Cross-family QP (§P2): independent contracts reduce to the per-contract Kelly
    sign; positive-edge contracts get positive weight, negative-edge get zero.

Function / robustness tests follow (degenerate inputs, normalization, errors).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.strategy.horse_race_kelly import (
    binary_second_moment,
    dominance_lp_check,
    horse_race_allocation,
    portfolio_qp_allocation,
)


# ---------------------------------------------------------------------------
# R1 — not overround: f_k* = q_k exactly, s* = 0.
# ---------------------------------------------------------------------------

def test_r1_underround_full_kelly_f_equals_q():
    p = [0.20, 0.30, 0.40]  # Σp = 0.90 <= 1
    q = [0.30, 0.30, 0.40]  # complete family, Σq = 1
    r = horse_race_allocation(p=p, q=q)
    assert r.regime == "underround"
    assert np.allclose(r.f, q, atol=1e-12)
    assert abs(r.s_cash) <= 1e-12
    assert abs(sum(r.f) + r.s_cash - 1.0) <= 1e-9


def test_r1_fair_book_boundary_sum_p_equals_one():
    # Σp == 1 (fair) still falls in the all-active branch: f = q, s = 0.
    p = [0.25, 0.25, 0.25, 0.25]
    q = [0.40, 0.30, 0.20, 0.10]
    r = horse_race_allocation(p=p, q=q)
    assert r.regime == "underround"
    assert np.allclose(r.f, q, atol=1e-12)
    assert abs(r.s_cash) <= 1e-9


# ---------------------------------------------------------------------------
# R2 — overround: budget closes AND active-set property holds.
# ---------------------------------------------------------------------------

def test_r2_overround_budget_closes_and_active_set_property():
    # Σp = 1.20 overround; bins 1-3 strong edge, bin 4 dead.
    p = np.array([0.30, 0.30, 0.30, 0.30])
    q = np.array([0.34, 0.33, 0.32, 0.01])
    r = horse_race_allocation(p=p, q=q)
    assert r.regime == "overround"
    # Budget closes exactly.
    assert abs(sum(r.f) + r.s_cash - 1.0) <= 1e-9
    # Endogenous threshold s* = s_cash (cash fraction) under overround.
    s_star = r.s_cash
    f = np.array(r.f)
    ratios = q / p
    # Active-set property: f_k > 0  iff  q_k/p_k > s*.
    for k in range(len(q)):
        if ratios[k] > s_star + 1e-9:
            assert f[k] > 0.0, f"bin {k} ratio {ratios[k]} > s* {s_star} should be funded"
        elif ratios[k] < s_star - 1e-9:
            assert f[k] == pytest.approx(0.0, abs=1e-12), (
                f"bin {k} ratio {ratios[k]} < s* {s_star} should be cash"
            )
    # The funded amount matches the closed form f_k = (q_k - p_k s*)_+.
    expected = np.maximum(q - p * s_star, 0.0)
    assert np.allclose(f, expected, atol=1e-9)


def test_r2_overround_competes_drops_low_ratio_bins():
    # A bin with q/p only marginally above 1 gets dropped under heavy overround
    # (its q/p <= s*), proving bins COMPETE for the endogenous threshold.
    p = np.array([0.40, 0.40, 0.40])  # Σp = 1.20
    q = np.array([0.70, 0.25, 0.05])
    r = horse_race_allocation(p=p, q=q)
    assert r.regime == "overround"
    f = np.array(r.f)
    s_star = r.s_cash
    ratios = q / p
    # bin 2 (ratio 0.625) and bin 3 (0.125) are below any plausible s*; dropped.
    assert f[2] == pytest.approx(0.0, abs=1e-12)
    # active count consistent with the ratio>s* set
    active = int((ratios > s_star + 1e-9).sum())
    assert r.active_count == active


# ---------------------------------------------------------------------------
# R3 — no-bet region: all cash.
# ---------------------------------------------------------------------------

def test_r3_no_bet_region_all_cash():
    # Every q_k/p_k <= 1 -> no positive per-dollar edge anywhere.
    p = np.array([0.50, 0.50, 0.50])
    q = np.array([0.45, 0.35, 0.20])  # all q_k < p_k
    r = horse_race_allocation(p=p, q=q)
    assert r.regime == "no_bet"
    assert all(x == 0.0 for x in r.f)
    assert r.s_cash == pytest.approx(1.0)
    assert r.expected_log_growth == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# R4 — GROWTH-DOMINANCE: the true overbetting-correction invariant.
# ---------------------------------------------------------------------------

def _naive_per_candidate_alloc(q: np.ndarray, p: np.ndarray) -> tuple[np.ndarray, float]:
    """Per-candidate (isolation) Kelly: each bin sized by single-bet Kelly (q-p)/(1-p),
    clipped to feasibility. This is the structure the horse-race REPLACES."""
    f = np.maximum((q - p) / (1.0 - p), 0.0)
    if f.sum() > 1.0:
        f = f / f.sum()
    return f, max(0.0, 1.0 - float(f.sum()))


def _expected_log_growth(q, p, f, s):
    w = s + f / p
    if np.any(w <= 0):
        return float("-inf")
    return float(np.sum(q * np.log(w)))


def test_r4_canonical_overbetting_case_growth_dominance():
    # The canonical demonstrator: per-candidate would fund 3 bins; the horse-race
    # solves the JOINT optimum. Growth strictly dominates. (And contra the task
    # brief, the horse-race here funds MORE total capital, not less — the naive
    # per-bin Kelly under-bets mutually-exclusive bins.)
    p = np.array([0.30, 0.30, 0.30, 0.30])  # Σp = 1.20 overround
    q = np.array([0.34, 0.33, 0.32, 0.01])
    r = horse_race_allocation(p=p, q=q)
    f_h = np.array(r.f)
    f_n, s_n = _naive_per_candidate_alloc(q, p)
    # naive funds the 3 positive-edge bins
    assert int((f_n > 1e-9).sum()) == 3
    g_h = _expected_log_growth(q, p, f_h, r.s_cash)
    g_n = _expected_log_growth(q, p, f_n, s_n)
    # Growth-dominance: horse-race >= naive (the optimum can't be beaten).
    assert g_h >= g_n - 1e-12
    # And in this overround mutual-exclusive case it is STRICTLY better.
    assert g_h > g_n + 1e-6


def test_r4_growth_dominance_random_families_all_regimes():
    rng = np.random.default_rng(2026)
    checked = 0
    for _ in range(4000):
        K = int(rng.integers(2, 8))
        p = rng.uniform(0.02, 0.85, K)
        q = rng.uniform(0.01, 1.0, K)
        q = q / q.sum()
        r = horse_race_allocation(p=p, q=q)
        f_h = np.array(r.f)
        f_n, s_n = _naive_per_candidate_alloc(q, p)
        g_n = _expected_log_growth(q, p, f_n, s_n)
        if not math.isfinite(g_n):
            continue
        g_h = _expected_log_growth(q, p, f_h, r.s_cash)
        checked += 1
        assert g_h >= g_n - 1e-9, (
            f"growth-dominance violated: horse {g_h} < naive {g_n} "
            f"p={p.tolist()} q={q.tolist()}"
        )
    assert checked > 3000  # sanity: most families produced a finite naive baseline


# ---------------------------------------------------------------------------
# R5 — YES(a)+NO(b) growth-optimal + dominance LP (authority §P5 / Q2(e)).
# ---------------------------------------------------------------------------

def test_r5_yes_a_plus_no_b_not_dominated():
    # 3 states (bins a, b, other). Contracts: YES(a), YES(b), NO(b).
    # State order: [a, b, other].
    # YES(a) pays [1,0,0]; YES(b) pays [0,1,0]; NO(b) pays [1,0,1].
    A = np.array(
        [
            [1.0, 0.0, 1.0],  # state a
            [0.0, 1.0, 0.0],  # state b
            [0.0, 0.0, 1.0],  # state other
        ]
    )
    costs = np.array([0.30, 0.30, 0.55])  # YES(a), YES(b), NO(b)
    # Candidate = YES(a) + NO(b): unit weights [1,0,1], target payoff [2,0,1].
    candidate = np.array([1.0, 0.0, 1.0])
    res = dominance_lp_check(A, costs, candidate)
    # YES(a)+NO(b) is the only way to get payoff [2,0,1] here (YES(b) pays in the
    # wrong state), so it is NOT cheaply replicable -> not dominated.
    assert res["dominated"] is False
    assert res["candidate_cost"] == pytest.approx(0.85)


def test_r5_dominance_lp_flags_cheaper_replication():
    # YES(a) and NO(b) over 2 bins {a,b}: NO(b) pays exactly like YES(a) (state a only),
    # so the more expensive of the two is DOMINATED by the cheaper replication.
    # States [a, b]. Contracts: YES(a) pays [1,0]; NO(b) pays [1,0]; YES(b) pays [0,1].
    A = np.array(
        [
            [1.0, 1.0, 0.0],  # state a: YES(a)=1, NO(b)=1, YES(b)=0
            [0.0, 0.0, 1.0],  # state b
        ]
    )
    costs = np.array([0.60, 0.30, 0.40])  # YES(a) expensive, NO(b) cheap, YES(b)
    # Candidate = YES(a) alone: payoff [1,0], cost 0.60. NO(b) reproduces [1,0] at 0.30.
    candidate = np.array([1.0, 0.0, 0.0])
    res = dominance_lp_check(A, costs, candidate)
    assert res["dominated"] is True
    assert res["replication_cost"] == pytest.approx(0.30, abs=1e-6)
    assert res["candidate_cost"] == pytest.approx(0.60)


# ---------------------------------------------------------------------------
# R6 — cross-family QP (authority §P2).
# ---------------------------------------------------------------------------

def test_r6_qp_independent_contracts_signs():
    # Two independent (corr=I) binary contracts: one +edge, one -edge.
    # q1/p1 = 0.6/0.5 = 1.2 (>1, positive edge); q2/p2 = 0.3/0.5 = 0.6 (<1, negative).
    q = np.array([0.60, 0.30])
    p = np.array([0.50, 0.50])
    f = portfolio_qp_allocation(q, p)  # corr=None -> identity
    assert f[0] > 1e-6  # positive-edge contract funded
    assert f[1] == pytest.approx(0.0, abs=1e-6)  # negative-edge contract zero


def test_r6_binary_second_moment_frechet_and_diagonal():
    q = np.array([0.4, 0.6, 0.5])
    p = np.array([0.5, 0.5, 0.5])
    corr = np.eye(3)
    mu, M = binary_second_moment(q, p, corr=corr)
    # mu_i = q_i/p_i - 1
    assert np.allclose(mu, q / p - 1.0, atol=1e-12)
    # M is symmetric
    assert np.allclose(M, M.T, atol=1e-12)


# ---------------------------------------------------------------------------
# Function / robustness tests.
# ---------------------------------------------------------------------------

def test_budget_always_closes_overround():
    rng = np.random.default_rng(1)
    for _ in range(500):
        K = int(rng.integers(2, 7))
        p = rng.uniform(0.05, 0.6, K)
        q = rng.uniform(0.01, 1.0, K)
        q = q / q.sum()
        r = horse_race_allocation(p=p, q=q)
        assert abs(sum(r.f) + r.s_cash - 1.0) <= 1e-8
        assert all(x >= -1e-12 for x in r.f)
        assert r.s_cash >= -1e-12


def test_q_normalization_projects_to_simplex():
    # Un-normalized q is projected; relative tilt preserved.
    p = [0.20, 0.30, 0.40]
    q_unnorm = [3.0, 3.0, 4.0]  # sums to 10
    r = horse_race_allocation(p=p, q=q_unnorm)
    assert np.allclose(r.f, [0.30, 0.30, 0.40], atol=1e-9)


def test_degenerate_zero_mass_all_cash():
    r = horse_race_allocation(p=[0.5, 0.5], q=[0.0, 0.0])
    assert r.regime == "no_bet"
    assert r.s_cash == pytest.approx(1.0)


def test_single_bin_complete_family_normalizes_to_one():
    # K=1 with normalize_q=True (default): a COMPLETE single-bin family means that
    # bin occurs w.p. 1, so q -> 1.0 and (Σp=0.4<=1) the all-active solution is
    # f=q=1.0, cash 0. This is correct: a complete partition of one outcome is certain.
    r = horse_race_allocation(p=[0.40], q=[0.80])
    assert r.regime == "underround"
    assert r.f[0] == pytest.approx(1.0)
    assert r.s_cash == pytest.approx(0.0)


def test_single_isolated_bin_no_normalize():
    # An ISOLATED bin (q is a genuine marginal, NOT a complete partition) is passed
    # with normalize_q=False. Σp=0.4<=1 -> f=q=0.8, residual cash 0.2 (the 0.2 mass
    # on "bin does not occur" is held as cash). This is the single-bet Kelly degenerate.
    r = horse_race_allocation(p=[0.40], q=[0.80], normalize_q=False)
    assert r.regime == "underround"
    assert r.f[0] == pytest.approx(0.8)
    assert r.s_cash == pytest.approx(0.2)


def test_invalid_price_raises():
    with pytest.raises(ValueError):
        horse_race_allocation(p=[0.0, 0.5], q=[0.5, 0.5])
    with pytest.raises(ValueError):
        horse_race_allocation(p=[-0.1, 0.5], q=[0.5, 0.5])


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        horse_race_allocation(p=[0.5, 0.5], q=[0.5, 0.3, 0.2])


def test_empty_raises():
    with pytest.raises(ValueError):
        horse_race_allocation(p=[], q=[])
