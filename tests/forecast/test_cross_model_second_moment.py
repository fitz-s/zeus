# Created: 2026-06-30
# Authority basis: operator "improve until ideal" — the cross-model raw second-moment matrix
#   Ê[e_i·e_j] (date-aligned, walk-forward) that feeds covariance_min_variance_weights. RAW basis
#   (no demeaning), consistent with the 2026-06-18 diagonal law; the diagonal of this matrix is the
#   existing per-model raw second moment.
import math
from src.forecast.center import cross_model_second_moment


def test_diagonal_matches_per_model_raw_second_moment():
    # residuals keyed by (aligned) index; diagonal entry == mean of squared residuals
    res = {"A": {0: 1.0, 1: -1.0, 2: 2.0}, "B": {0: 0.0, 1: 0.0, 2: 0.0}}
    M = cross_model_second_moment(res, min_overlap=1)
    assert math.isclose(M[("A", "A")], (1 + 1 + 4) / 3, abs_tol=1e-9)
    assert math.isclose(M[("B", "B")], 0.0, abs_tol=1e-9)
    assert math.isclose(M[("A", "B")], 0.0, abs_tol=1e-9)


def test_positive_cross_term_for_co_erring_models():
    res = {"A": {0: 2.0, 1: 2.0}, "B": {0: 1.0, 1: 3.0}}
    M = cross_model_second_moment(res, min_overlap=1)
    assert math.isclose(M[("A", "B")], (2 * 1 + 2 * 3) / 2, abs_tol=1e-9)  # =4.0
    assert math.isclose(M[("A", "B")], M[("B", "A")], abs_tol=1e-9)


def test_aligns_on_common_dates_only():
    # only indices 1,2 are common
    res = {"A": {0: 5.0, 1: 1.0, 2: 1.0}, "B": {1: 2.0, 2: 2.0, 3: 9.0}}
    M = cross_model_second_moment(res, min_overlap=1)
    assert math.isclose(M[("A", "B")], (1 * 2 + 1 * 2) / 2, abs_tol=1e-9)  # index 0,3 dropped


def test_insufficient_overlap_absent():
    res = {"A": {0: 1.0}, "B": {5: 1.0}}  # no common date
    M = cross_model_second_moment(res, min_overlap=1)
    assert ("A", "B") not in M and ("B", "A") not in M
