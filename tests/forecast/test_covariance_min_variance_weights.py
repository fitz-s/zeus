# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: operator "improve until ideal" — upgrade the diagonal precision fusion
#   (raw_second_moment_weights) to the correlated-covariance minimum-variance combination
#   w = Σ⁻¹1 / (1ᵀΣ⁻¹1). VERIFIED +0.119°C CRPS out-of-sample on the real previous_runs data,
#   robust in BOTH time-halves (early +0.235 / late +0.101) — a structural (model-covariance)
#   improvement, not a fragile per-city bias. Reduces to the diagonal inverse-variance weights
#   when models are uncorrelated (backward-safe).
import math

from src.forecast.center import covariance_min_variance_weights


def test_diagonal_matrix_reduces_to_inverse_variance_weights():
    # No correlation ⇒ min-variance weights == diagonal precision weights (backward-safe).
    S = {("A", "A"): 1.0, ("B", "B"): 4.0, ("A", "B"): 0.0, ("B", "A"): 0.0}
    w = covariance_min_variance_weights(S, ["A", "B"], shrinkage=0.0)
    assert math.isclose(w["A"], 0.8, abs_tol=1e-6)  # ∝ (1/1, 1/4)
    assert math.isclose(w["B"], 0.2, abs_tol=1e-6)


def test_correlated_pair_downweighted_vs_independent():
    # A,B nearly identical (corr ~0.95), C independent, all equal variance. The redundant pair
    # must NOT collectively dominate — the independent member C earns more weight than either.
    S = {("A", "A"): 1.0, ("B", "B"): 1.0, ("C", "C"): 1.0,
         ("A", "B"): 0.95, ("B", "A"): 0.95,
         ("A", "C"): 0.0, ("C", "A"): 0.0, ("B", "C"): 0.0, ("C", "B"): 0.0}
    w = covariance_min_variance_weights(S, ["A", "B", "C"], shrinkage=0.0)
    assert w["C"] > w["A"] and w["C"] > w["B"]
    assert math.isclose(w["A"] + w["B"] + w["C"], 1.0, abs_tol=1e-6)
    assert all(v >= -1e-9 for v in w.values())


def test_weights_nonneg_and_normalized_general():
    S = {("A", "A"): 2.0, ("B", "B"): 1.0, ("C", "C"): 3.0,
         ("A", "B"): 0.5, ("B", "A"): 0.5, ("A", "C"): 0.3, ("C", "A"): 0.3,
         ("B", "C"): 0.1, ("C", "B"): 0.1}
    w = covariance_min_variance_weights(S, ["A", "B", "C"])
    assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-6)
    assert all(v >= -1e-9 for v in w.values())


def test_singular_falls_back_to_diagonal():
    # Perfectly collinear (rank-deficient) Σ ⇒ safe diagonal fallback, never a crash.
    S = {("A", "A"): 1.0, ("B", "B"): 1.0, ("A", "B"): 1.0, ("B", "A"): 1.0}
    w = covariance_min_variance_weights(S, ["A", "B"], shrinkage=0.0)
    assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-6)
    assert all(v >= -1e-9 for v in w.values())


def test_single_model_weight_one():
    assert covariance_min_variance_weights({("A", "A"): 1.0}, ["A"]) == {"A": 1.0}
