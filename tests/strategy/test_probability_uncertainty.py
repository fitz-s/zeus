# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §4 + §5.5 + §5.6 + §12.B + §14.4 + Hidden issues #2/#3 + operator directive 2026-06-08
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=never
# Purpose: §12.B relationship-test antibody for ProbabilityUncertainty (q_lcb / edge_lcb separation).
"""Relationship tests (spec §12.B) for ``src.strategy.probability_uncertainty``.

These are written BEFORE the implementation (TDD / relationship-tests-first).
They encode the LOAD-BEARING cross-space invariants the spec forbids violating:

  1. §4 / Hidden #3 — ``q_lcb_no`` is the lower quantile of the PER-SAMPLE
     complement ``1 - q_yes_samples``. The lower tail of NO is the UPPER tail
     of YES, so ``q_lcb_no == 1 - q_ucb_yes`` and (for asymmetric samples)
     ``q_lcb_no != 1 - q_lcb_yes``. The point-complement intuition
     ``q_lcb_no = 1 - q_lcb_yes`` is WRONG and would cause NO overconfidence.

  2. §5.6 / Hidden #2 — ``edge_lcb`` is computed from JOINT
     (probability − executable cost) samples and is a SEPARATE function.
     ``q_lcb`` must NEVER be derived from ``edge_lcb``. Adding price/cost
     uncertainty widens ``edge_lcb`` but leaves ``q_lcb`` unchanged.

  3. §5.6 — penalties (calibration, boundary, representativeness, forecast
     volatility, multiple comparison) lower ``q_lcb`` only on the LOWER bound;
     the point ``q_point`` and the quantile of the raw samples are untouched.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.strategy.probability_uncertainty import (
    ProbabilityUncertainty,
    UncertaintyPenalties,
    edge_lcb,
    lower_quantile,
    no_side_samples,
    probability_uncertainty_from_samples,
    upper_quantile,
)


# ---------------------------------------------------------------------------
# Deterministic asymmetric bootstrap fixture. RIGHT-skewed YES samples so that
# the lower tail and the upper tail are NOT mirror images: this is exactly the
# regime where 1 - q_lcb_yes diverges from q_lcb_no.
# ---------------------------------------------------------------------------
def _asymmetric_yes_samples(seed: int = 7, n: int = 20000) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # Beta(2, 5): mean ~0.286, right-skewed, strictly inside (0, 1).
    return rng.beta(2.0, 5.0, size=n)


ALPHA = 0.05


# ===========================================================================
# §12.B.1 — test_q_lcb_no_not_one_minus_q_lcb_yes
# ===========================================================================
def test_q_lcb_no_not_one_minus_q_lcb_yes() -> None:
    """NO LCB uses complement SAMPLES, not the point complement of YES LCB.

    Central invariant (§4):
        q_lcb_no == 1 - q_ucb_yes        (lower tail of NO = upper tail of YES)
        q_lcb_no != 1 - q_lcb_yes        (the naive point complement is wrong)
    """
    q_yes = _asymmetric_yes_samples()

    yes = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)
    no = probability_uncertainty_from_samples(no_side_samples(q_yes), alpha=ALPHA)

    # The factory returns the frozen contract type (§14.4 field carrier).
    assert isinstance(yes, ProbabilityUncertainty)
    assert isinstance(no, ProbabilityUncertainty)

    # The load-bearing equality: NO lower bound is the complement of the YES
    # UPPER bound, NOT the complement of the YES lower bound.
    assert no.q_lcb == pytest.approx(1.0 - yes.q_ucb, abs=1e-12)

    # And it is provably NOT 1 - q_lcb_yes for asymmetric samples.
    assert abs(no.q_lcb - (1.0 - yes.q_lcb)) > 1e-3
    assert no.q_lcb != pytest.approx(1.0 - yes.q_lcb, abs=1e-6)

    # Structural cross-check on the raw quantile helpers themselves:
    # quantile(1 - x, alpha) == 1 - quantile(x, 1 - alpha).
    assert lower_quantile(no_side_samples(q_yes), ALPHA) == pytest.approx(
        1.0 - upper_quantile(q_yes, ALPHA), abs=1e-12
    )


# ===========================================================================
# §12.B.2 — test_edge_ci_lower_separate_from_q_lcb
# ===========================================================================
def test_edge_ci_lower_separate_from_q_lcb() -> None:
    """Adding price/cost uncertainty widens edge_lcb but leaves q_lcb unchanged.

    Hidden #2: edge_ci_lower must NOT masquerade as q_lcb. q_lcb depends only on
    the probability samples; edge_lcb depends on the JOINT (q - cost) samples.
    """
    rng = np.random.default_rng(11)
    q_yes = _asymmetric_yes_samples()

    # q_lcb is a pure function of the probability samples.
    q = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)

    # Case A: deterministic (zero-uncertainty) cost.
    cost_fixed = np.full_like(q_yes, 0.20)
    edge_lcb_fixed = edge_lcb(q_yes, cost_fixed, alpha=ALPHA)

    # Case B: SAME probability samples, SAME mean cost, but added cost noise.
    cost_noisy = 0.20 + rng.normal(0.0, 0.05, size=q_yes.shape)
    edge_lcb_noisy = edge_lcb(q_yes, cost_noisy, alpha=ALPHA)

    # q_lcb is identical across A and B — it never saw the cost samples.
    q_again = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)
    assert q_again.q_lcb == pytest.approx(q.q_lcb, abs=1e-12)

    # edge_lcb widens (becomes MORE negative / lower) when cost uncertainty is
    # added, because the lower tail of (q - cost) extends downward.
    assert edge_lcb_noisy < edge_lcb_fixed - 1e-4

    # And edge_lcb is NOT a probability lower bound: q_lcb + cost_point is not
    # the same object as edge_lcb + cost_point. The whole point of Hidden #2 is
    # that you cannot reconstruct q_lcb by adding price back to edge_lcb once
    # price uncertainty is present.
    reconstructed_q_lcb = edge_lcb_noisy + 0.20
    assert abs(reconstructed_q_lcb - q.q_lcb) > 1e-4


# ===========================================================================
# §12.B.3 — test_calibration_penalty_lowers_q_lcb
# ===========================================================================
def test_calibration_penalty_lowers_q_lcb() -> None:
    """Same q samples, worse calibration evidence → q_lcb decreases.

    The penalty is subtracted from the LOWER bound only; q_point and q_ucb are
    untouched (§5.6: 'only the LOWER bound; the POINT is untouched').
    """
    q_yes = _asymmetric_yes_samples()

    clean = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)
    penalized = probability_uncertainty_from_samples(
        q_yes,
        alpha=ALPHA,
        penalties=UncertaintyPenalties(calibration_penalty=0.05),
    )

    assert penalized.q_lcb < clean.q_lcb - 1e-9
    # The penalty magnitude flows straight through (no clipping in interior).
    assert penalized.q_lcb == pytest.approx(clean.q_lcb - 0.05, abs=1e-12)
    # Point and upper bound are NOT moved by a lower-bound penalty.
    assert penalized.q_point == pytest.approx(clean.q_point, abs=1e-12)
    assert penalized.q_ucb == pytest.approx(clean.q_ucb, abs=1e-12)
    # The recorded penalty is carried on the object for provenance.
    assert penalized.calibration_penalty == pytest.approx(0.05, abs=1e-12)


# ===========================================================================
# §12.B.4 — test_boundary_sensitivity_penalty
# ===========================================================================
def test_boundary_sensitivity_penalty() -> None:
    """A boundary-sensitivity penalty lowers q_lcb relative to a stable bin.

    Spec §12.B.4 / Hidden #2 motivation: members clustered near a bin boundary
    are more fragile; the boundary penalty lowers the LOWER confidence bound.
    """
    q_yes = _asymmetric_yes_samples()

    stable = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)
    near_boundary = probability_uncertainty_from_samples(
        q_yes,
        alpha=ALPHA,
        penalties=UncertaintyPenalties(boundary_penalty=0.03),
    )

    assert near_boundary.q_lcb < stable.q_lcb - 1e-9
    assert near_boundary.boundary_penalty == pytest.approx(0.03, abs=1e-12)
    # Point estimate is a stability anchor — unchanged by the boundary penalty.
    assert near_boundary.q_point == pytest.approx(stable.q_point, abs=1e-12)


# ===========================================================================
# Supporting structural invariants (defend the contract, not just the formula)
# ===========================================================================
def test_q_lcb_never_exceeds_q_point() -> None:
    """q_lcb ≤ q_point under the defined estimator (Hidden #2 detection rule).

    The lower quantile of the samples cannot exceed the point (mean) estimate
    by more than estimator noise, and penalties only push it lower. We assert
    the hard inequality on the constructed object.
    """
    q_yes = _asymmetric_yes_samples()
    pu = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)
    assert pu.q_lcb <= pu.q_point + 1e-9
    assert pu.q_ucb >= pu.q_point - 1e-9


def test_penalties_clip_q_lcb_to_zero_floor() -> None:
    """q_lcb is clipped to [0, 1]; a huge penalty floors at 0, never negative."""
    q_yes = _asymmetric_yes_samples()
    pu = probability_uncertainty_from_samples(
        q_yes,
        alpha=ALPHA,
        penalties=UncertaintyPenalties(calibration_penalty=10.0),
    )
    assert pu.q_lcb == 0.0


def test_no_side_samples_is_per_sample_complement() -> None:
    """no_side_samples(x) is exactly 1 - x, element-wise (not a separate model)."""
    q_yes = _asymmetric_yes_samples(seed=3, n=1000)
    no = no_side_samples(q_yes)
    assert np.allclose(no, 1.0 - q_yes)


def test_frozen_dataclass_is_immutable() -> None:
    """ProbabilityUncertainty is a frozen contract object."""
    q_yes = _asymmetric_yes_samples(n=500)
    pu = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)
    with pytest.raises((AttributeError, TypeError)):
        pu.q_lcb = 0.5  # type: ignore[misc]


def test_q_lcb_is_not_derived_from_edge_lcb_under_zero_cost() -> None:
    """Even with ZERO cost, q_lcb and edge_lcb come from different code paths.

    Hidden #2 antibody: with cost ≡ 0, edge_lcb == q_lcb numerically, but they
    must be produced by SEPARATE functions. We assert the numerical identity
    under zero cost (a sanity anchor) AND that edge_lcb diverges the moment any
    nonzero cost enters — proving edge_lcb is not just q_lcb wearing a hat.
    """
    q_yes = _asymmetric_yes_samples()
    zero_cost = np.zeros_like(q_yes)
    pu = probability_uncertainty_from_samples(q_yes, alpha=ALPHA)

    # With zero cost, edge_lcb reduces to the raw probability lower quantile,
    # which (with zero penalties) equals q_lcb.
    assert edge_lcb(q_yes, zero_cost, alpha=ALPHA) == pytest.approx(
        pu.q_lcb, abs=1e-12
    )

    # The instant any cost is subtracted, edge_lcb moves but q_lcb does not.
    some_cost = np.full_like(q_yes, 0.10)
    assert edge_lcb(q_yes, some_cost, alpha=ALPHA) < pu.q_lcb - 1e-3
