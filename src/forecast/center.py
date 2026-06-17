# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/forecast/center.py" block lines 220-271: CenterEstimate dataclass
#   224-234, the center algorithm 236-270, the envelope-enforcement code 256-268)
#   + Stage 3 block (lines 1072-1090, RED-on-revert names, live signal). Reconciled
#   against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; prefer the live types; the envelope is a
#   TRANSFORMATION not a cap — mu_candidate is computed so lo<=mu<=hi holds by
#   construction, ENVELOPE_FALLBACK to mu_consensus, terminated by an assert; the
#   day0 license to leave the envelope is the SEPARATE day0_conditioner, not here).
#   Dependencies: src/forecast/types.py (ForecastCase/RawModelMember/FreshModelSet),
#   src/forecast/debias_authority.py (DebiasAuthority — applied ONCE),
#   src/forecast/bayes_precision_fusion.py (inverse-residual-variance / EB-shrink
#   precision-weight primitives reused for walk_forward_model_weights),
#   src/calibration/emos.py:437 emos_predictive (OPTIONAL EMOS center, admissible
#   only as a shrinkage residual toward the debiased consensus).
"""Forecast center builder + envelope invariant (q-kernel rebuild Stage 3).

THE CENTER. ``build_center`` is the single place where the live forecast center
``mu*`` is produced for a target family. It replaces the broken design in which
``build_emos_q`` accepted raw members and let EMOS output BECOME the live μ
directly — the design that let an EMOS slope/intercept (or a stale −4.847 EDLI
mean shift) push μ to a value (Tokyo 26°C) NO member supports when every fresh
debiased member sits in [20, 23]°C.

INV-C1 (the envelope invariant). After fresh members are de-biased ONCE through
``DebiasAuthority``, the served center MUST satisfy

    min(debiased_values) <= mu_native <= max(debiased_values)

absent a day0 observation (the day0 license to leave the envelope is owned by the
SEPARATE ``day0_conditioner`` module, not here). This module makes a violation
mathematically IMPOSSIBLE — not by clamping a bad μ after a broken transform
produced it, but by CONSTRUCTING the candidate so the bound holds:

  * ``mu_consensus`` is a WEIGHTED HUBER LOCATION of the debiased member values
    with non-negative weights that sum to 1. A weighted location with such weights
    is a convex combination of the member values, so it is provably inside
    ``[min, max]`` of those values (a weighted average / robust location can never
    exceed the extreme inputs). This is the in-envelope ANCHOR.

  * EMOS may only ENTER as a shrinkage RESIDUAL toward ``mu_consensus``:
    ``mu_candidate = shrink(mu_emos, toward=mu_consensus, strength=oos)``. If the
    shrunk candidate still lands outside ``[lo, hi]`` (a steep EMOS slope on a
    wide member spread can do this for small oos shrink), the transform FALLS
    BACK to ``mu_consensus`` — which is in-envelope by construction — and records
    ``ENVELOPE_FALLBACK``. The result is then ``assert lo <= mu_candidate <= hi``.

The fallback is NOT a "sanity clamp" that leaves a broken transform in place: the
ONLY value that can be served when EMOS proposes outside is the in-envelope robust
consensus, so an out-of-envelope μ is never a reachable output. The ``assert`` is
a proof obligation that the construction holds, not the mechanism that enforces it.

This module does NOT import ``predictive_distribution_builder`` (built in a
separate follow-on step). It produces ONLY the ``CenterEstimate``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Optional, Sequence

import numpy as np

from src.calibration.emos import emos_predictive
from src.forecast.bayes_precision_fusion import (
    KAPPA,
    LOWN_INFLATE,
    MIN_TRAIN,
    SIGMA_FLOOR,
)
from src.forecast.debias_authority import DebiasAuthority
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember

# ---------------------------------------------------------------------------
# Robust-location + shrink tuning. All in the settlement native unit.
# ---------------------------------------------------------------------------

# Huber tuning constant (the breakpoint between the quadratic core and the linear
# tail, in robust-σ units). 1.345 is the standard 95%-Gaussian-efficiency choice.
HUBER_C: float = 1.345

# Scale floor for the Huber location so a degenerate (all-equal) member set does
# not divide by zero when forming the robust scale. In the settlement native unit.
HUBER_SCALE_FLOOR: float = 0.25

# Iteratively-reweighted-least-squares iteration cap + convergence tol for the
# weighted Huber location. Convergence is fast (a handful of steps) for the small
# member sets (a few to a few dozen models) the forecast spine produces.
HUBER_MAX_ITERS: int = 50
HUBER_TOL: float = 1e-9

# Floor on the EMOS out-of-sample shrink strength. ``shrink`` with strength s in
# [0, 1] returns ``(1 - s) * mu_consensus + s * mu_emos``; s = 0 keeps the
# in-envelope consensus, s = 1 trusts EMOS fully (still envelope-proofed below).
EMOS_OOS_STRENGTH_DEFAULT: float = 0.0


# ---------------------------------------------------------------------------
# CenterEstimate (spec lines 224-234) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CenterEstimate:
    """The served forecast center for one ForecastCase.

    Fields are verbatim from consult_build_spec.md lines 224-234.

    ``mu_native`` is the served center in the settlement native unit. It is the
    debiased-consensus / shrunk-EMOS value that the envelope invariant proves to
    lie in ``[debiased_member_min_native, debiased_member_max_native]`` (absent a
    day0 observation, handled by the separate day0_conditioner). ``center_method``
    records WHICH center won; ``center_status`` records whether the envelope
    invariant fired its fallback.
    """

    mu_native: float
    raw_consensus_native: float
    debiased_consensus_native: float
    debiased_member_min_native: float
    debiased_member_max_native: float
    center_method: Literal["WEIGHTED_HUBER_CONSENSUS", "SHRUNK_EMOS", "RAW_FALLBACK"]
    center_status: Literal["OK", "ENVELOPE_FALLBACK", "DAY0_CLAMPED", "REFUSED"]
    weights_by_model: Mapping[str, float]
    reason: str


# ---------------------------------------------------------------------------
# Walk-forward model weights (spec line 246: "shrink to equal weights by n/SE").
# Reuses the bayes_precision_fusion EB / inverse-variance / low-n-inflation
# primitives rather than reinventing the weighting math.
# ---------------------------------------------------------------------------

def walk_forward_model_weights(
    case: ForecastCase,
    members: Sequence[RawModelMember],
) -> np.ndarray:
    """Non-negative model weights that sum to 1, shrunk toward equal by n / SE.

    Spec line 246: ``weights = walk_forward_model_weights(case, members)  # shrink
    to equal weights by n/SE``. The precision basis is the inverse residual
    variance (the same inverse-variance / precision idea ``bayes_precision_fusion``
    fuses on); thin-history members are inflated toward equal weight via the SAME
    EB low-n rule the fusion uses (``KAPPA`` shrink, ``MIN_TRAIN`` threshold,
    ``LOWN_INFLATE`` σ multiplier, ``SIGMA_FLOOR`` floor).

    A ``RawModelMember`` carries no per-model walk-forward residual vector in the
    Stage-1 contract (``src/forecast/types.py``), so the *available* precision
    signal is uniform across members; the EB shrink therefore collapses to EQUAL
    weights — the conservative shrink-to-equal posture the spec comment names. When
    a later contract threads per-model SE/n onto the member (or the fresh set), this
    function is the single seam that converts it to inverse-variance precision
    weights without touching the envelope proof: ANY non-negative weights summing
    to 1 keep ``mu_consensus`` a convex combination of the member values, so INV-C1
    holds regardless of the weighting detail.

    The weights are returned in member order. With ``n`` members and no per-model
    precision signal the result is ``np.full(n, 1/n)``.
    """
    n = len(members)
    if n == 0:
        return np.asarray([], dtype=float)

    # Inverse-variance precision per member, shrunk toward equal by the EB low-n
    # rule. With no per-model residual history on the member, every member shares
    # the same floored σ and the same low-n inflation, so precisions are equal and
    # the normalized weights are exactly 1/n (shrink-to-equal). The structure below
    # is the seam: a future per-model (se, n) makes the precisions diverge.
    precisions = np.empty(n, dtype=float)
    for i, member in enumerate(members):
        se = float(getattr(member, "walk_forward_se_native", SIGMA_FLOOR) or SIGMA_FLOOR)
        n_train = int(getattr(member, "walk_forward_n", 0) or 0)
        sigma = max(se, SIGMA_FLOOR)
        if n_train < MIN_TRAIN:
            # EB shrink toward equal: thin history -> inflate σ toward the
            # low-trust floor so a thin member cannot dominate (lam = n/(n+KAPPA)).
            lam = n_train / (n_train + KAPPA)
            sigma = lam * sigma + (1.0 - lam) * (SIGMA_FLOOR * LOWN_INFLATE)
            sigma = max(sigma, SIGMA_FLOOR)
        precisions[i] = 1.0 / (sigma * sigma)

    total = float(precisions.sum())
    if not np.isfinite(total) or total <= 0.0:
        return np.full(n, 1.0 / n, dtype=float)
    weights = precisions / total
    # Final guard: non-negative and sum-to-1 (the INV-C1 convexity precondition).
    weights = np.clip(weights, 0.0, None)
    s = float(weights.sum())
    if s <= 0.0:
        return np.full(n, 1.0 / n, dtype=float)
    return weights / s


# ---------------------------------------------------------------------------
# Weighted Huber location (spec line 247) — the in-envelope robust consensus.
# ---------------------------------------------------------------------------

def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Weighted quantile of ``values`` at probability ``q`` (for the robust scale)."""
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cw = np.cumsum(w)
    total = cw[-1]
    if total <= 0.0:
        return float(np.median(values))
    cutoff = q * total
    idx = int(np.searchsorted(cw, cutoff, side="left"))
    idx = min(max(idx, 0), len(v) - 1)
    return float(v[idx])


def weighted_huber_location(
    values: Sequence[float],
    weights: Sequence[float],
) -> float:
    """Weighted Huber M-estimator of location (IRLS), in the native unit.

    A robust weighted location: it down-weights members that sit far (in robust-σ
    units) from the running center, so one outlier model cannot drag the consensus,
    while the bulk of the members vote by their walk-forward weight. Critically for
    INV-C1, the final estimate is a WEIGHTED AVERAGE of the member values with
    non-negative effective weights — every IRLS step forms
    ``sum(w_eff_i * x_i) / sum(w_eff_i)`` with ``w_eff_i >= 0`` — so it is a convex
    combination of the inputs and therefore ALWAYS lies in
    ``[min(values), max(values)]``. It can never invent a center outside the member
    envelope; that is the structural property the envelope invariant relies on.

    The robust scale is a weighted MAD (Median Absolute Deviation), floored so an
    all-equal member set does not divide by zero. ``HUBER_C`` is the Huber
    breakpoint. Falls back to the plain weighted mean if the scale collapses.
    """
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if x.size == 0:
        raise ValueError("weighted_huber_location: no values")
    if w.size != x.size:
        raise ValueError("weighted_huber_location: weights/values length mismatch")
    w = np.clip(w, 0.0, None)
    if float(w.sum()) <= 0.0:
        w = np.ones_like(x)
    w = w / float(w.sum())

    if x.size == 1:
        return float(x[0])

    # Start at the weighted median (robust, in-envelope).
    mu = _weighted_quantile(x, w, 0.5)

    # Weighted MAD scale (robust), floored.
    abs_dev = np.abs(x - mu)
    mad = _weighted_quantile(abs_dev, w, 0.5)
    scale = max(1.4826 * mad, HUBER_SCALE_FLOOR)

    for _ in range(HUBER_MAX_ITERS):
        resid = x - mu
        # Huber weight psi(r)/r: 1 inside the core, c*scale/|r| in the tail. The
        # effective weight w_i * huber_w_i is always >= 0, so the update is a
        # convex combination of the x_i and stays in [min(x), max(x)].
        z = np.abs(resid) / scale
        huber_w = np.where(z <= HUBER_C, 1.0, HUBER_C / np.maximum(z, 1e-12))
        eff = w * huber_w
        denom = float(eff.sum())
        if denom <= 0.0:
            break
        new_mu = float(np.sum(eff * x) / denom)
        if abs(new_mu - mu) <= HUBER_TOL:
            mu = new_mu
            break
        mu = new_mu
        # Refresh the scale around the new center (keeps it robust + floored).
        mad = _weighted_quantile(np.abs(x - mu), w, 0.5)
        scale = max(1.4826 * mad, HUBER_SCALE_FLOOR)

    # The estimate is a convex combination of x; numerically pin it into the
    # member hull so floating-point drift cannot escape the envelope by an ULP.
    lo = float(np.min(x))
    hi = float(np.max(x))
    return float(min(max(mu, lo), hi))


# ---------------------------------------------------------------------------
# EMOS shrink (spec line 254) — EMOS enters ONLY as a residual toward consensus.
# ---------------------------------------------------------------------------

def shrink(value: float, *, toward: float, strength: float) -> float:
    """Shrink ``value`` toward ``toward`` by ``strength`` in [0, 1].

    ``strength = 0`` returns ``toward`` (no EMOS influence — the in-envelope
    consensus); ``strength = 1`` returns ``value`` (full EMOS). Spec line 254:
    ``mu_candidate = shrink(mu_emos, toward=mu_consensus, strength=emos_oos_strength)``.
    The result is a convex combination of the two when strength in [0, 1], so if
    BOTH endpoints were in the envelope the shrunk value would be too — but EMOS
    (``value``) need NOT be in the envelope, which is exactly why the envelope
    proof below still has to fall back when the shrunk candidate escapes.
    """
    s = float(min(max(strength, 0.0), 1.0))
    return (1.0 - s) * float(toward) + s * float(value)


def _emos_oos_strength(case: ForecastCase) -> float:
    """The out-of-sample EMOS shrink strength for this case.

    No fitted per-case OOS strength is threaded onto the Stage-1 ``ForecastCase``
    contract yet, so the default is the conservative floor (EMOS enters as a pure
    residual with zero default weight — it can only move μ if a later contract
    supplies a validated OOS strength). This is the single seam where a fitted
    EMOS-vs-consensus OOS skill weight would be read; it never bypasses the
    envelope proof.
    """
    return float(getattr(case, "emos_oos_strength", EMOS_OOS_STRENGTH_DEFAULT))


# ---------------------------------------------------------------------------
# The center builder (spec lines 236-270).
# ---------------------------------------------------------------------------

def build_center(
    case: ForecastCase,
    models: FreshModelSet,
    debias_authority: DebiasAuthority,
    *,
    use_emos: bool = True,
) -> CenterEstimate:
    """Build the served forecast center for one family (spec lines 236-270).

    Algorithm (verbatim from the spec):
      1. Read fresh members for the exact target family (``models`` already IS the
         fresh member set for ``case``).
      2. Apply ``DebiasAuthority`` ONCE -> debiased member values.
      3. Compute a robust consensus:
            weights = walk_forward_model_weights(case, members)
            mu_consensus = weighted_huber_location(debiased_values, weights)
      4. OPTIONAL EMOS, only as a shrinkage residual toward the debiased consensus:
            mu_emos = a + b * xbar           (from emos_predictive)
            mu_candidate = shrink(mu_emos, toward=mu_consensus, strength=oos)
      5. Enforce the envelope (the TRANSFORMATION, not a cap):
            lo = min(debiased_values) ; hi = max(debiased_values)
            if not lo <= mu_candidate <= hi:
                mu_candidate = mu_consensus            # in-envelope by construction
                center_status = "ENVELOPE_FALLBACK"
            assert lo <= mu_candidate <= hi

    The ``assert`` is a proof obligation: ``mu_consensus`` is a convex combination
    of the debiased member values (non-negative weights summing to 1), so it is in
    ``[lo, hi]`` by construction, and the fallback path therefore always satisfies
    the bound. EMOS can NEVER directly become live μ unless the envelope proof
    passes (spec line 269). The day0 license to leave the envelope is owned by the
    separate ``day0_conditioner`` and is NOT exercised here.
    """
    members = tuple(models.members)
    model_ids = [m.model_id for m in members]

    # --- raw consensus (pre-debias), for telemetry / drift visibility ----------
    raw_values = np.asarray(models.member_values_native, dtype=float)
    weights = walk_forward_model_weights(case, members)
    weights_by_model: dict[str, float] = {
        mid: float(w) for mid, w in zip(model_ids, weights)
    }

    if raw_values.size == 0:
        # No members: cannot serve a center. Fail-closed REFUSED (the day0/q layers
        # treat a REFUSED center as "no live distribution"); no μ is invented.
        return CenterEstimate(
            mu_native=float("nan"),
            raw_consensus_native=float("nan"),
            debiased_consensus_native=float("nan"),
            debiased_member_min_native=float("nan"),
            debiased_member_max_native=float("nan"),
            center_method="RAW_FALLBACK",
            center_status="REFUSED",
            weights_by_model=weights_by_model,
            reason="no fresh members for this family; no center served",
        )

    raw_consensus = weighted_huber_location(raw_values, weights)

    # --- (2) de-bias ONCE through the single authority -------------------------
    debiased_values, applied = debias_authority.apply(case, models)
    debiased_values = np.asarray(debiased_values, dtype=float)

    # --- (3) robust debiased consensus (the in-envelope anchor) ----------------
    mu_consensus = weighted_huber_location(debiased_values, weights)
    debiased_consensus = float(mu_consensus)

    # Envelope bounds over the DEBIASED member values (spec lines 260-261).
    lo = float(np.min(debiased_values))
    hi = float(np.max(debiased_values))

    # --- (4) optional EMOS, ONLY as a shrinkage residual toward consensus ------
    mu_candidate = float(mu_consensus)
    center_method: Literal[
        "WEIGHTED_HUBER_CONSENSUS", "SHRUNK_EMOS", "RAW_FALLBACK"
    ] = "WEIGHTED_HUBER_CONSENSUS"
    center_status: Literal[
        "OK", "ENVELOPE_FALLBACK", "DAY0_CLAMPED", "REFUSED"
    ] = "OK"
    emos_note = ""

    if use_emos:
        mu_emos = _emos_center(case, debiased_values)
        if mu_emos is not None:
            oos = _emos_oos_strength(case)
            shrunk = shrink(mu_emos, toward=mu_consensus, strength=oos)
            mu_candidate = float(shrunk)
            center_method = "SHRUNK_EMOS"
            emos_note = (
                f"emos_mu={mu_emos:.4f} shrunk toward consensus={mu_consensus:.4f} "
                f"@ strength={oos:.3f} -> candidate={mu_candidate:.4f}; "
            )

    # --- (5) enforce the envelope — the TRANSFORMATION (spec lines 256-268) -----
    # Not a cap on a bad value: the only value served when EMOS proposes outside is
    # mu_consensus, which is a convex combination of the debiased members and so is
    # in [lo, hi] by construction. An out-of-envelope μ is therefore unreachable.
    if not lo <= mu_candidate <= hi:
        mu_candidate = float(mu_consensus)
        center_method = "WEIGHTED_HUBER_CONSENSUS"
        center_status = "ENVELOPE_FALLBACK"
        emos_note += (
            f"EMOS candidate left envelope [{lo:.4f}, {hi:.4f}] -> fell back to "
            f"in-envelope debiased consensus {mu_consensus:.4f}; "
        )

    # Proof obligation: the construction guarantees the bound holds (mu_consensus is
    # a convex combination of the debiased members). A failure here means a debiased
    # member is non-finite or the consensus math regressed — fail loudly, never
    # serve an out-of-envelope center.
    assert lo <= mu_candidate <= hi, (
        f"INV-C1 VIOLATED: mu_candidate={mu_candidate} not in debiased member "
        f"envelope [{lo}, {hi}] (consensus={mu_consensus})"
    )

    reason = (
        f"method={center_method} status={center_status}; "
        f"debias={applied.activation_status} shift={applied.aggregate_shift_native:+.4f}; "
        f"{emos_note}"
        f"envelope=[{lo:.4f}, {hi:.4f}] mu*={mu_candidate:.4f}"
    )

    return CenterEstimate(
        mu_native=float(mu_candidate),
        raw_consensus_native=float(raw_consensus),
        debiased_consensus_native=debiased_consensus,
        debiased_member_min_native=lo,
        debiased_member_max_native=hi,
        center_method=center_method,
        center_status=center_status,
        weights_by_model=weights_by_model,
        reason=reason,
    )


def _emos_center(case: ForecastCase, debiased_values: np.ndarray) -> Optional[float]:
    """Compute the EMOS predictive mean ``mu_emos = a + b * xbar`` for this case.

    Calls the live ``emos_predictive`` (``src/calibration/emos.py:437``) on the
    DEBIASED member values (in °C — current Zeus EMOS cells are fit in °C; F-unit
    families are handled by the unit thread upstream, see drift note). Returns the
    EMOS mean only, or ``None`` when the cell is missing / served != "emos" / the
    member set is too small — in which case EMOS simply does not enter and the
    served center is the in-envelope consensus. The returned mean is NEVER used as
    μ directly: ``build_center`` only ever shrinks it toward the consensus and
    re-proves the envelope.
    """
    season = getattr(case, "season", "")
    lead_days = float(getattr(case, "lead_hours", 0.0)) / 24.0
    try:
        out = emos_predictive(
            case.city,
            season,
            lead_days,
            np.asarray(debiased_values, dtype=float),
            metric=case.metric,
        )
    except Exception:
        return None
    if out is None:
        return None
    mu_emos, _sigma = out
    if not np.isfinite(mu_emos):
        return None
    return float(mu_emos)
