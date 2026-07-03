# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: "bin selection.md" §4 + §5.5 + §5.6 + §11 (Phase 2) + §14.4 + Hidden issues #2/#3 + operator directive 2026-06-08
"""Probability-space uncertainty contract (spec Phase 2, §14.4).

LOAD-BEARING SAFETY OBJECT. This module separates two confidence-lower-bound
spaces the spec forbids conflating (Hidden issues #2 and #3):

  * ``q_lcb``   — robust LOWER bound on the side's settlement PROBABILITY,
                  computed from probability bootstrap samples ALONE, minus
                  forecast-quality penalties. Used to SIZE Kelly.
  * ``edge_lcb``— robust LOWER bound on the realized EDGE (probability minus
                  executable cost), computed from the JOINT
                  ``(q − cost)`` samples. Used as a separate acceptance gate.

Runtime boundary: this module provides pure probability-bound contracts and
helpers. Importing it has no side effects; callers decide whether the resulting
bounds are admissible for a live decision.

Three invariants this module structurally enforces (so the wrong code is hard
to write, per the project's "make the category impossible" methodology):

1. NATIVE-NO IS NOT AN INDEPENDENT FORECAST (§4, Hidden #3).
   The native NO probability is the PER-SAMPLE YES complement
   ``q_no_samples = 1 - q_yes_samples`` — see :func:`no_side_samples`. The
   correct NO lower bound is therefore the lower quantile of that complement,
   which equals ``1 - q_ucb_yes`` (lower tail of NO = upper tail of YES),
   NOT ``1 - q_lcb_yes``. There is deliberately NO separate "NO model" here;
   the function name and docstring make the complement structure explicit so
   no one rebuilds an independent NO forecast.

2. q_lcb IS NEVER DERIVED FROM edge_lcb (§5.6, Hidden #2).
   :func:`edge_lcb` is a separate function taking cost samples. The
   :class:`ProbabilityUncertainty` constructor only ever consumes probability
   samples + penalties. Price/cost uncertainty widens ``edge_lcb`` but leaves
   ``q_lcb`` untouched. ``edge_lcb + cost`` is NOT a valid probability lower
   bound because price and probability uncertainty interact nonlinearly.

3. PENALTIES LOWER ONLY THE LOWER BOUND (§5.6).
   ``q_point`` and ``q_ucb`` are forecast-quality-penalty-free; only ``q_lcb``
   subtracts the penalty sum, then clips to ``[0, 1]``.

Quantile convention
-------------------
``lower_quantile(s, alpha) = percentile(s, 100*alpha)`` and
``upper_quantile(s, alpha) = percentile(s, 100*(1-alpha))`` using NumPy's
default linear interpolation, which is exactly order-reversal symmetric:
``lower_quantile(1 - s, alpha) == 1 - upper_quantile(s, alpha)``. That exact
algebraic identity is what makes invariant (1) hold to machine precision.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

# Default tail mass for the robust lower/upper bounds. 0.05 → 5th / 95th
# percentile, matching the existing edge-bootstrap convention in
# ``market_analysis._bootstrap_bin`` (``np.percentile(edges, 5/95)``).
DEFAULT_ALPHA: float = 0.05


# ---------------------------------------------------------------------------
# Quantile helpers (the exact-symmetry primitives invariant (1) relies on).
# ---------------------------------------------------------------------------
def _as_clean_samples(samples) -> np.ndarray:
    """Coerce to a 1-D float array and reject empty / non-finite input.

    Fail-closed: a degenerate sample vector cannot silently produce a bogus
    bound. Callers in the no-trade-gate path (§13: ``q_lcb unavailable or
    invalid``) translate this into a no-trade.
    """
    arr = np.asarray(samples, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("probability samples are empty")
    if not np.all(np.isfinite(arr)):
        raise ValueError("probability samples contain non-finite values")
    return arr


def lower_quantile(samples, alpha: float = DEFAULT_ALPHA) -> float:
    """Lower α-quantile of ``samples`` (e.g. the 5th percentile for α=0.05)."""
    _validate_alpha(alpha)
    arr = _as_clean_samples(samples)
    return float(np.percentile(arr, 100.0 * alpha))


def upper_quantile(samples, alpha: float = DEFAULT_ALPHA) -> float:
    """Upper (1−α)-quantile of ``samples`` (e.g. the 95th percentile for α=0.05)."""
    _validate_alpha(alpha)
    arr = _as_clean_samples(samples)
    return float(np.percentile(arr, 100.0 * (1.0 - alpha)))


def _validate_alpha(alpha: float) -> None:
    if not (0.0 < alpha < 0.5):
        raise ValueError(f"alpha must be in (0, 0.5), got {alpha}")


# ---------------------------------------------------------------------------
# Native NO = per-sample YES complement (NOT an independent forecast). §4.
# ---------------------------------------------------------------------------
def no_side_samples(q_yes_samples) -> np.ndarray:
    """Return the native-NO probability samples as the YES per-sample complement.

    §4 / Hidden #3: NO is NOT an independent forecast. For a complete binary
    bin event, ``q_no = 1 - q_yes`` HOLDS AT THE SAMPLE LEVEL::

        q_no_samples = 1 - q_yes_samples

    The robust NO lower bound is then ``lower_quantile(q_no_samples, alpha)``,
    which equals ``1 - upper_quantile(q_yes_samples, alpha)`` — the lower tail
    of NO is the UPPER tail of YES. This is emphatically NOT
    ``1 - lower_quantile(q_yes_samples, alpha)``.

    This function is the ONLY blessed way to obtain NO probability samples from
    YES samples; do not build a separate NO bootstrap/posterior.
    """
    arr = _as_clean_samples(q_yes_samples)
    if np.any(arr < 0.0) or np.any(arr > 1.0):
        raise ValueError("q_yes samples must lie in [0, 1] to take the complement")
    return 1.0 - arr


# ---------------------------------------------------------------------------
# Penalty bundle (§5.6). Each penalty lowers ONLY the q_lcb lower bound.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UncertaintyPenalties:
    """Forecast-quality penalties subtracted from ``q_lcb`` (§5.6 δ terms).

    All penalties are non-negative probability-space deductions. They lower the
    robust lower bound only; the point estimate and upper bound never see them.

    * ``calibration_penalty``         — δ_cal: weak / immature calibration.
    * ``boundary_penalty``            — δ_boundary: members clustered near a bin
                                        edge (boundary sensitivity, §12.B.4).
    * ``representativeness_penalty``  — δ_rep: grid-vs-station / source mismatch.
    * ``forecast_volatility_penalty`` — δ_forecastVol: forecast instability.
    * ``multiple_comparison_penalty`` — δ_multi: family-wise multiple-testing.
    """

    calibration_penalty: float = 0.0
    boundary_penalty: float = 0.0
    representativeness_penalty: float = 0.0
    forecast_volatility_penalty: float = 0.0
    multiple_comparison_penalty: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "calibration_penalty",
            "boundary_penalty",
            "representativeness_penalty",
            "forecast_volatility_penalty",
            "multiple_comparison_penalty",
        ):
            v = float(getattr(self, name))
            if not np.isfinite(v):
                raise ValueError(f"{name} must be finite, got {v}")
            if v < 0.0:
                raise ValueError(f"{name} must be >= 0 (penalties only lower q_lcb), got {v}")

    def total(self) -> float:
        """Sum of all penalty deductions (the Σ-penalties term in §5.6)."""
        return (
            float(self.calibration_penalty)
            + float(self.boundary_penalty)
            + float(self.representativeness_penalty)
            + float(self.forecast_volatility_penalty)
            + float(self.multiple_comparison_penalty)
        )


# ---------------------------------------------------------------------------
# The frozen contract object (§11 Phase 2 / §14.4 field list).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProbabilityUncertainty:
    """Robust probability-space uncertainty for ONE native side (§11 Phase 2).

    Fields are exactly the Phase-2 list from spec §11:
        q_point, q_samples_hash, q_lcb, q_ucb, calibration_penalty,
        boundary_penalty, representativeness_penalty,
        forecast_volatility_penalty, multiple_comparison_penalty.

    Construct via :func:`probability_uncertainty_from_samples`, which is the
    only blessed path — it guarantees ``q_lcb`` is derived from probability
    samples alone (never from ``edge_lcb``) and that penalties lower only the
    lower bound.

    Invariants enforced at construction:
        * ``0 <= q_lcb <= 1`` (clipped),
        * ``q_lcb <= q_point`` (lower bound cannot exceed the point — Hidden #2
          detection rule),
        * ``q_ucb >= q_point``.
    """

    q_point: float
    q_samples_hash: str
    q_lcb: float
    q_ucb: float
    calibration_penalty: float = 0.0
    boundary_penalty: float = 0.0
    representativeness_penalty: float = 0.0
    forecast_volatility_penalty: float = 0.0
    multiple_comparison_penalty: float = 0.0
    alpha: float = DEFAULT_ALPHA
    # Non-field metadata for downstream provenance; excluded from eq/hash key.
    n_samples: int = field(default=0, compare=False)

    def __post_init__(self) -> None:
        for name in ("q_point", "q_lcb", "q_ucb"):
            v = float(getattr(self, name))
            if not np.isfinite(v):
                raise ValueError(f"{name} must be finite, got {v}")
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be in [0, 1], got {v}")
        # Hidden #2 detection rule: a lower bound that exceeds the point estimate
        # is the signature of edge-CI masquerading as a probability bound. Allow
        # a tiny estimator tolerance, then hard-fail.
        if self.q_lcb > self.q_point + 1e-9:
            raise ValueError(
                f"q_lcb ({self.q_lcb}) > q_point ({self.q_point}): a probability "
                "lower bound cannot exceed the point estimate. This is the "
                "signature of edge_lcb masquerading as q_lcb (Hidden #2)."
            )
        if self.q_ucb < self.q_point - 1e-9:
            raise ValueError(
                f"q_ucb ({self.q_ucb}) < q_point ({self.q_point}): upper bound "
                "cannot be below the point estimate."
            )

    @property
    def penalty_total(self) -> float:
        return (
            float(self.calibration_penalty)
            + float(self.boundary_penalty)
            + float(self.representativeness_penalty)
            + float(self.forecast_volatility_penalty)
            + float(self.multiple_comparison_penalty)
        )


def _hash_samples(arr: np.ndarray) -> str:
    """Stable content hash of the sample vector (for cache keys / provenance)."""
    return hashlib.sha256(np.ascontiguousarray(arr, dtype=np.float64).tobytes()).hexdigest()[:16]


def probability_uncertainty_from_samples(
    q_side_samples,
    *,
    alpha: float = DEFAULT_ALPHA,
    penalties: UncertaintyPenalties | None = None,
) -> ProbabilityUncertainty:
    """Build a :class:`ProbabilityUncertainty` from ONE side's probability samples.

    Spec §5.6 / §14.4::

        q_lcb = clip_[0,1]( lower_quantile(q_side_samples, alpha) - Σ penalties )
        q_ucb =            upper_quantile(q_side_samples, 1 - alpha)
        q_point = mean(q_side_samples)

    ``q_side_samples`` are the samples for the SIDE being evaluated:
        * YES side: pass the YES bootstrap samples directly.
        * NO side:  pass ``no_side_samples(q_yes_samples)`` — the per-sample
          complement. This makes ``q_lcb_no == 1 - q_ucb_yes`` automatically
          (the central invariant), never ``1 - q_lcb_yes``.

    q_lcb is a pure function of the probability samples and penalties ONLY. It
    never consumes cost/price samples — that is :func:`edge_lcb`'s job
    (Hidden #2). Do not derive q_lcb from edge_lcb.

    """
    _validate_alpha(alpha)
    arr = _as_clean_samples(q_side_samples)
    if np.any(arr < 0.0) or np.any(arr > 1.0):
        raise ValueError("probability samples must lie in [0, 1]")

    pen = penalties or UncertaintyPenalties()

    q_point = float(np.mean(arr))
    raw_lcb = float(np.percentile(arr, 100.0 * alpha))
    q_ucb = float(np.percentile(arr, 100.0 * (1.0 - alpha)))

    # Penalties lower ONLY the lower bound, then clip to [0, 1] (§5.6).
    q_lcb = float(np.clip(raw_lcb - pen.total(), 0.0, 1.0))
    # Guard the q_lcb <= q_point invariant against estimator noise where the
    # zero-penalty lower quantile can drift a hair above the sample mean
    # (rare with continuous samples, possible with tied/degenerate vectors).
    q_lcb = min(q_lcb, q_point)
    # Symmetric guard for q_point <= q_ucb: with sparse tail bins (>95% of
    # bootstrap samples exactly 0.0, a few small positives) the 95th
    # percentile is 0.0 while the mean is positive -- q_ucb < q_point by
    # construction, which raised in __post_init__ and collapsed the WHOLE
    # family proof (live regret storm LIVE_INFERENCE_INPUTS_MISSING:q_ucb,
    # 2026-06-12). The mean is the tightest honest upper bound available
    # when the upper quantile degenerates below it.
    q_ucb = max(q_ucb, q_point)

    return ProbabilityUncertainty(
        q_point=q_point,
        q_samples_hash=_hash_samples(arr),
        q_lcb=q_lcb,
        q_ucb=q_ucb,
        calibration_penalty=float(pen.calibration_penalty),
        boundary_penalty=float(pen.boundary_penalty),
        representativeness_penalty=float(pen.representativeness_penalty),
        forecast_volatility_penalty=float(pen.forecast_volatility_penalty),
        multiple_comparison_penalty=float(pen.multiple_comparison_penalty),
        alpha=float(alpha),
        n_samples=int(arr.size),
    )


# ---------------------------------------------------------------------------
# Edge lower bound — SEPARATE function over JOINT (q - cost) samples (§5.6).
# ---------------------------------------------------------------------------
def edge_lcb(
    q_side_samples,
    executable_cost_samples,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> float:
    """Robust lower bound on the EDGE = probability − executable cost (§5.6).

    ::

        edge_lcb(S) = lower_quantile( q_side_samples − executable_cost_samples, alpha )

    This is the JOINT distribution of (probability − cost). It is a SEPARATE
    authority from ``q_lcb`` (Hidden #2): adding price/cost uncertainty widens
    this bound, while ``q_lcb`` is unchanged. ``edge_lcb + cost_point`` is NOT a
    valid probability lower bound because the two uncertainty sources interact
    nonlinearly.

    ``executable_cost_samples`` must be paired sample-for-sample with
    ``q_side_samples`` (same length). Cost is in the SAME side's native
    probability-units cost space (fee/depth-adjusted executable cost).
    """
    _validate_alpha(alpha)
    q_arr = _as_clean_samples(q_side_samples)
    c_arr = _as_clean_samples(executable_cost_samples)
    if q_arr.shape != c_arr.shape:
        raise ValueError(
            f"q_side_samples ({q_arr.shape}) and executable_cost_samples "
            f"({c_arr.shape}) must be paired sample-for-sample (equal length)"
        )
    edge_samples = q_arr - c_arr
    return float(np.percentile(edge_samples, 100.0 * alpha))
