# Created: 2026-09-26
# Last audited: 2026-09-26
# Authority basis: design review REQ-20260925-223704 §3 and §10 behavioral
#   acceptance (simplex, NO complement, nested candidates, shrinkage).
"""Tests for src/calibration/market_anchored_family.py."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.special import betainc

from src.calibration import market_anchored_family as maf

K = 11


def _rows(n, conc, seed):
    return np.random.default_rng(seed).dirichlet(np.full(K, conc), size=n)


def _features(n, seed=0):
    rng = np.random.default_rng(seed)
    return maf.FamilyFeatures(
        metric=rng.choice(["high", "low"], n).astype(object),
        lane=rng.choice([maf.DAY0, maf.FORECAST], n).astype(object),
        hours_to_end=rng.uniform(0.0, 72.0, n),
        res_class=np.full(n, "C1:wmo_half_up", dtype=object),
        station=rng.choice(["A", "B", "C"], n).astype(object),
        era=np.full(n, "e", dtype=object),
    )


@pytest.mark.parametrize("w,a,b", [(0.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.3, 0.05, 20.0),
                                   (0.7, 20.0, 0.05), (0.5, 1.0, 3.0)])
def test_every_candidate_output_is_a_simplex(w, a, b):
    Q = _rows(400, 0.2, 1)
    P = _rows(400, 0.2, 2)
    P[0] = np.eye(K)[0]  # a degenerate family with a hard zero tail
    R = maf.family_masses(Q, P, w, a, b)
    assert np.all(R >= 0.0)
    assert np.allclose(R.sum(axis=1), 1.0, atol=1e-12)


def test_near_empty_middle_bin_is_never_negative():
    # the lower CDF and upper survival cross 1/2 inside a ~3e-17 bin, so its
    # difference of transformed tails rounds to -5.6e-17 before clipping
    P = np.array([[0.26107483617461746, 0.17142808755015337, 0.09843605465736145,
                   3.426482941542662e-17, 0.46906102161786767]])
    R = maf.family_masses(P, P, 0.0, 1.330030918127784, 1.66026796552523)
    assert np.all(R >= 0.0)
    assert R.sum() == pytest.approx(1.0, abs=1e-15)


def test_identity_transform_is_the_arithmetic_pool_and_market_null_is_p():
    Q, P = _rows(50, 0.5, 3), _rows(50, 0.5, 4)
    assert np.allclose(maf.family_masses(Q, P, 0.25, 1.0, 1.0), 0.25 * Q + 0.75 * P, atol=1e-15)
    assert np.allclose(maf.family_masses(Q, P, 0.0, 1.0, 1.0), P, atol=1e-15)


def test_tail_bin_mass_is_exact_where_naive_cdf_difference_cancels():
    P = np.full((1, K), 1e-13)
    P[0, 5] = 1.0 - 1e-13 * (K - 1)
    R = maf.family_masses(P, P, 0.0, 1.0, 2.0)
    # alpha=1, beta=2: the top bin's served mass is its survival I_s(2, 1) = s^2,
    # s = 1e-13 accumulated from the top; 1 - F(K-1) would round it to noise
    assert R[0, -1] > 0.0
    assert R[0, -1] == pytest.approx(1e-26, rel=1e-9, abs=0.0)


def test_beta_transform_matches_a_reference_cdf_difference_in_the_bulk():
    Q, P = _rows(200, 1.0, 5), _rows(200, 1.0, 6)
    R = maf.family_masses(Q, P, 0.4, 1.7, 0.6)
    H = np.cumsum(0.4 * Q + 0.6 * P, axis=1)
    naive = np.diff(np.hstack([np.zeros((200, 1)), betainc(1.7, 0.6, np.clip(H, 0, 1))]), axis=1)
    assert np.allclose(R, naive, atol=1e-8)


def test_no_leg_complements_prediction_and_label_together():
    r, y = maf.held_side([0.2, 0.2, 0.9], [1, 0, 1], ["YES", "NO", "NO"])
    assert r.tolist() == pytest.approx([0.2, 0.8, 0.1])
    assert y.tolist() == [1.0, 1.0, 0.0]
    # the held-side loss equals the YES-side loss: complementing only one side would not
    base = maf.binary_log_loss([0.2, 0.2, 0.9], [1, 0, 1])
    assert maf.binary_log_loss(r, y).tolist() == pytest.approx(base.tolist())


def test_invalid_family_masses_fail_closed():
    with pytest.raises(maf.FamilyMassError):
        maf.as_family_masses([[0.5, 0.4]])
    with pytest.raises(maf.FamilyMassError):
        maf.as_family_masses([[1.2, -0.2]])
    with pytest.raises(maf.FamilyMassError):
        maf.as_family_masses([[np.nan, 1.0]])


def test_prediction_set_miss_uses_the_smallest_top_mass_set_with_low_index_ties():
    R = np.array([[0.45, 0.45, 0.05, 0.05], [0.3, 0.3, 0.3, 0.1]])
    assert maf.prediction_set_miss(R, np.array([2, 3])).tolist() == [1.0, 1.0]
    assert maf.prediction_set_miss(R, np.array([1, 2])).tolist() == [0.0, 0.0]
    tied = np.array([[0.4, 0.2, 0.2, 0.2]] * 2)  # 0.6-mass set is {0, 1}: lower index wins the tie
    assert maf.prediction_set_miss(tied, np.array([1, 2]), mass=0.6).tolist() == [0.0, 1.0]


def test_scores_match_their_definitions():
    R = np.array([[0.1, 0.6, 0.3]])
    y = np.array([2])
    assert maf.categorical_log_loss(R, y)[0] == pytest.approx(-np.log(0.3))
    assert maf.ranked_probability_score(R, y)[0] == pytest.approx(0.1 ** 2 + 0.7 ** 2)
    assert maf.full_simplex_brier(R, y)[0] == pytest.approx(0.01 + 0.36 + 0.49)


def _synthetic(n, seed, q_power):
    rng = np.random.default_rng(seed)
    truth = rng.dirichlet(np.full(K, 0.8), size=n)
    y = np.array([rng.choice(K, p=t) for t in truth])
    P = 0.5 * truth + 0.5 * rng.dirichlet(np.ones(K), size=n)
    Q = truth ** q_power
    Q /= Q.sum(axis=1, keepdims=True)
    return truth, y, P, Q


def test_market_as_true_generator_fits_zero_weight():
    rng = np.random.default_rng(11)
    P = rng.dirichlet(np.full(K, 0.8), size=3000)
    y = np.array([rng.choice(K, p=p) for p in P])
    Q = rng.dirichlet(np.full(K, 0.3), size=3000)  # uninformative, overconfident
    s = np.ones(3000)
    assert maf.fit_arithmetic_weight(Q, P, y, s) < 0.01
    fit = maf.fit_family(maf.ARITHMETIC, Q, P, y, s, _features(3000))
    w, _, _ = fit.parameters(_features(3000))
    assert float(np.mean(w)) < 0.02


def test_useful_but_overconfident_q_fits_interior_shrinkage():
    truth, y, P, Q = _synthetic(4000, 12, 2.0)
    s = np.ones(4000)
    w_hat = maf.fit_arithmetic_weight(Q, P, y, s)
    assert 0.05 < w_hat < 0.95
    fit = maf.fit_family(maf.ARITHMETIC, Q, P, y, s, _features(4000))
    w, _, _ = fit.parameters(_features(4000))
    assert 0.05 < float(np.mean(w)) < 0.95
    null = maf.categorical_log_loss(P, y).mean()
    assert maf.categorical_log_loss(fit.predict(Q, P, _features(4000)), y).mean() < null
    assert maf.categorical_log_loss(Q, y).mean() > maf.categorical_log_loss(fit.predict(Q, P, _features(4000)), y).mean()


def test_loss_slope_at_market_decides_the_sign_of_the_weight():
    truth, y, P, Q = _synthetic(3000, 13, 2.0)
    assert float(np.mean(maf.loss_slope_at_market(Q, P, y))) < 0.0
    rng = np.random.default_rng(14)
    noise = rng.dirichlet(np.full(K, 0.2), size=3000)
    assert float(np.mean(maf.loss_slope_at_market(noise, P, y))) > 0.0


def test_price_only_recovers_a_known_beta_transform():
    rng = np.random.default_rng(15)
    n = 6000
    true_p = rng.dirichlet(np.full(K, 0.8), size=n)
    # observed market is the truth passed through the inverse transform
    target = maf.family_masses(true_p, true_p, 0.0, 1.0, 1.0)
    y = np.array([rng.choice(K, p=t) for t in target])
    P = maf.family_masses(true_p, true_p, 0.0, 0.7, 0.7)  # market too spread out
    feats = _features(n)
    fit = maf.fit_family(maf.PRICE_ONLY, P, P, y, np.ones(n), feats)
    _, a, b = fit.parameters(feats)
    assert float(np.mean(a)) > 1.0 and float(np.mean(b)) > 1.0
    assert (maf.categorical_log_loss(fit.predict(P, P, feats), y).mean()
            < maf.categorical_log_loss(P, y).mean())


def test_nested_candidates_restrict_their_parameters():
    truth, y, P, Q = _synthetic(800, 16, 1.5)
    feats = _features(800)
    null = maf.fit_family(maf.MARKET_NULL, Q, P, y, np.ones(800), feats)
    assert np.allclose(null.predict(Q, P, feats), P)
    arith = maf.fit_family(maf.ARITHMETIC, Q, P, y, np.ones(800), feats)
    _, a, b = arith.parameters(feats)
    assert np.all(a == 1.0) and np.all(b == 1.0)
    price = maf.fit_family(maf.PRICE_ONLY, Q, P, y, np.ones(800), feats)
    w, _, _ = price.parameters(feats)
    assert np.all(w == 0.0)
    assert set(maf.fit_family(maf.BLP, Q, P, y, np.ones(800), feats).theta) == {"w", "a", "b"}


def test_hierarchical_fit_is_invariant_to_row_duplication_under_normalized_weights():
    truth, y, P, Q = _synthetic(600, 17, 2.0)
    feats = _features(600)
    s = np.ones(600)
    base = maf.fit_family(maf.ARITHMETIC, Q, P, y, s, feats, eb_steps=0)
    dup = np.r_[np.arange(600), np.arange(600)]
    twice = maf.fit_family(maf.ARITHMETIC, Q[dup], P[dup], y[dup], s[dup] / 2.0, feats.take(dup),
                           eb_steps=0, design=base.design)
    assert np.allclose(base.theta["w"], twice.theta["w"], atol=1e-4)
