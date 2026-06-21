# Created: 2026-06-14
# Last audited: 2026-06-21
# Authority basis: docs/rebuild/consult_build_spec.md
#   + Option C raw-precision representativeness center warming (consult
#   REQ-20260621-033315; forecast-gap-is-data-precision). The shared
#   raw_precision_center / raw_second_moment_weights helper threads per-model
#   grid-representativeness variance into the RAW diagonal precision denominator
#   that produces the SERVED traded center _mu_diagonal (Σ w_m·z_m). Form A:
#   denom_m = max(base_m2, floor_m2) + repr_m2_native (floor the residual FIRST,
#   THEN add the INDEPENDENT representativeness observation-variance, per
#   representativeness_variance.py rule 5: Sigma_source = Sigma_resid + sigma_repr²).
#   Repr enters the MEAN weights ONLY — never predictive_sigma_c / Kelly width.
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
# Walk-forward model weights — RAW DIAGONAL PRECISION (2026-06-18 FINAL no-shadow
# execution flow §2). Basis = inverse RAW SECOND MOMENT 1/max(Ê[(x−Y)²], floor²),
# NOT the inverse demeaned variance. Reads RawModelMember.walk_forward_raw_m2_native.
# Reuses ONLY the SIGMA_FLOOR / KAPPA / MIN_TRAIN / LOWN_INFLATE scalars from
# bayes_precision_fusion — never shrink_cov / diag_cov / np.var / np.std (those
# discard bias² and are the WRONG basis under the RAW no-de-bias law).
# ---------------------------------------------------------------------------

def walk_forward_model_weights(
    case: ForecastCase,
    members: Sequence[RawModelMember],
) -> np.ndarray:
    """Non-negative model weights that sum to 1 — RAW diagonal precision basis.

    RAW DIAGONAL PRECISION (2026-06-18 FINAL no-shadow execution flow §2; consult
    resolution ledger BLOCKER 3). Under the no-de-bias RAW law the optimal diagonal
    weighting is the inverse RAW SECOND MOMENT, NOT the inverse demeaned variance:

        ``w_m ∝ 1 / max(Ê[(x_m − Y)²], SIGMA_FLOOR²)``

    where ``Ê[(x_m − Y)²]`` (``member.walk_forward_raw_m2_native``) is the
    strictly-prior, date-aligned mean of the SQUARED raw residual (forecast minus
    settlement) over this model's walk-forward history. The raw second moment
    INCLUDES the bias² term ``E[r]²`` — which is exactly what a RAW (uncorrected)
    center must price, because under RAW the per-model location bias is part of the
    realized error. This is settlement-confirmed to beat ``1/demeaned-var`` (the
    ``np.cov``/``np.var`` basis that discards bias²): ``diag_raw2mom`` 1.676 bin-NLL
    vs the EB-cost table. DO NOT route this through ``shrink_cov`` / ``diag_cov`` /
    ``np.var`` / ``np.std`` / any demeaning — those estimate Σ (demeaned) and are
    the WRONG basis under RAW.

    SHRINK-TO-EQUAL AT LOW n: a member whose ``walk_forward_n < MIN_TRAIN`` cannot
    be trusted to dominate on a thin raw second moment, so its precision is shrunk
    toward the equal-weight precision (the pooled-equal floor) by the EB low-n rule
    ``lam = n/(n+KAPPA)``: ``m2_eff = lam·raw_m2 + (1−lam)·(SIGMA_FLOOR·LOWN_INFLATE)²``.
    A member with NO history (``raw_m2 is None`` / non-finite / n == 0) gets the
    conservative equal-precision floor ``(SIGMA_FLOOR·LOWN_INFLATE)²`` — so the
    absent-history member set collapses to EQUAL 1/n (never EB, never demeaned var).

    The weights are non-negative and sum to 1, so ``mu_consensus`` stays a convex
    combination of the member values and INV-C1 holds regardless of the weighting
    detail. Returned in member order. With ``n`` members and NO per-model precision
    signal the result is exactly ``np.full(n, 1/n)``.
    """
    n = len(members)
    if n == 0:
        return np.asarray([], dtype=float)

    # UNIT CONSISTENCY (critic HIGH 2026-06-18): ``walk_forward_raw_m2_native`` is in
    # the member NATIVE unit² — the spine producer scales degC²->°F² by ``(9/5)²`` for
    # F-cities. ``SIGMA_FLOOR`` / ``LOWN_INFLATE`` are defined in degC, so the floor and
    # the low-n shrink TARGET must be scaled to the same native² unit; otherwise an F-city
    # thin-history member is blended/floored against a 3.24× too-small target and over-
    # weighted (intent-inverted at low n). C-cities: ``_u == 1.0`` (unchanged).
    _u = (9.0 / 5.0) ** 2 if case.unit == "F" else 1.0
    floor_m2 = (SIGMA_FLOOR * SIGMA_FLOOR) * _u
    equal_m2 = ((SIGMA_FLOOR * LOWN_INFLATE) ** 2) * _u  # pooled-equal low-trust floor, native²

    precisions = np.empty(n, dtype=float)
    have_any_signal = False
    for i, member in enumerate(members):
        raw_m2 = getattr(member, "walk_forward_raw_m2_native", None)
        n_train = int(getattr(member, "walk_forward_n", 0) or 0)
        # Option C: grid-representativeness variance in the member's native unit²
        # (already serving-unit-scaled by the producer). 0.0 / non-finite ⇒ no penalty.
        try:
            repr_native = float(getattr(member, "representativeness_m2_native", 0.0) or 0.0)
        except (TypeError, ValueError):
            repr_native = 0.0
        if not np.isfinite(repr_native) or repr_native <= 0.0:
            repr_native = 0.0
        try:
            raw_m2_f = float(raw_m2) if raw_m2 is not None else None
        except (TypeError, ValueError):
            raw_m2_f = None
        if raw_m2_f is None or not np.isfinite(raw_m2_f) or raw_m2_f <= 0.0 or n_train <= 0:
            # No usable raw second-moment signal for this member -> equal-precision
            # floor (it cannot dominate; the set shrinks toward equal 1/n).
            m2_eff = equal_m2
            if repr_native > 0.0:
                # Cold-start (Option C §4): a positive repr IS a precision signal even
                # with no raw history — equal prior + repr (geometry breaks the tie).
                have_any_signal = True
        else:
            have_any_signal = True
            if n_train < MIN_TRAIN:
                # EB shrink toward the equal-precision floor by lam = n/(n+KAPPA):
                # a thin raw second moment cannot dominate a deep one.
                lam = n_train / (n_train + KAPPA)
                m2_eff = lam * raw_m2_f + (1.0 - lam) * equal_m2
            else:
                m2_eff = raw_m2_f
        # Precision = 1 / (max(E[r^2], SIGMA_FLOOR^2) + sigma_repr²). The floor is on
        # the raw second moment itself; the INDEPENDENT representativeness variance is
        # added AFTER the floor (Form A — rule 5: Sigma_source = Sigma_resid + repr).
        precisions[i] = 1.0 / (max(m2_eff, floor_m2) + repr_native)

    # No member carried any precision signal at all -> exact equal weights (the
    # absent-history posture; the spine's dormant seam before history is threaded).
    if not have_any_signal:
        return np.full(n, 1.0 / n, dtype=float)

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
# Shared RAW second-moment weight helper (single source of truth for ENTRY
# and EXIT center alignment — METHOD UNIFY 2026-06-18).
# ---------------------------------------------------------------------------

def raw_second_moment_weights(
    raw_m2_and_n: "dict[str, tuple[float | None, int]]",
    *,
    unit: str = "C",
    repr_m2_by_model: "dict[str, float] | None" = None,
) -> "dict[str, float]":
    """Non-negative weights summing to 1 — RAW diagonal precision basis.

    Shared helper consumed by both the spine ENTRY (``walk_forward_model_weights``
    via ``RawModelMember``) and the materializer EXIT path (``forecast_posteriors``
    center — METHOD UNIFY 2026-06-18).  Using ONE function guarantees the entry and
    exit centers are computed identically from the same weights formula so the
    two-center split (#135) cannot re-open through weights drift.

    ``raw_m2_and_n`` maps model → (raw_m2_native, n_train).  ``raw_m2_native`` is
    Ê[(x−Y)²] in the model's NATIVE unit² (degC² for C-cities; degC² for
    walk-forward residuals even when the materializer serves F — the caller is
    responsible for supplying degC² from ``train_residuals`` which are stored in
    degC).  ``unit`` is the SERVING unit: "F" scales the floor / shrink target by
    (9/5)² (the same fix operator landed in f06d2176bc).

    ``repr_m2_by_model`` (Option C, 2026-06-21): optional per-model grid-
    representativeness variance, **in the SAME unit² basis as ``raw_m2_native``**
    (the CALLER supplies it pre-converted, exactly as it supplies ``raw_m2_native``).
    When supplied, each model's denominator becomes

        ``denom_m = max(base_m2, floor_m2) + repr_m2``

    i.e. the residual second moment is floored FIRST, then the INDEPENDENT
    representativeness observation-variance is ADDED (Form A — per
    ``representativeness_variance.py`` rule 5: ``Sigma_source = Sigma_resid +
    sigma_repr²``).  Adding AFTER the floor is what makes a small-but-real repr
    penalty on a sub-floor-residual member still bite (it would be swallowed if
    added before the floor).  The helper does NOT unit-scale repr: just as
    ``raw_m2_native`` is the caller's responsibility (the EXIT seam supplies degC²,
    the ENTRY seam supplies native²·(9/5)²), the caller supplies repr in the matching
    basis so the add stays unit-consistent regardless of which seam calls it.

    A model absent from ``repr_m2_by_model`` (or with a non-positive / non-finite
    value) contributes repr = 0.0 — byte-identical to the pre-Option-C helper for
    that model.  ``repr_m2_by_model=None`` is byte-identical to the old signature.

    COLD-START (Option C §4): "no signal" is now ``no raw m2 AND no positive finite
    repr`` ⇒ exact equal 1/n.  A member with NO raw m2 but a POSITIVE repr is a real
    precision signal: its base becomes the equal-precision prior ``equal_m2`` and the
    repr is added, so a geometry-derived penalty can break equal weights even on a
    no-history (thin/cold-start) cell.

    Returns {model: weight}.  Falls back to equal 1/n when no model has a usable
    precision signal (no history, all thin/non-finite, AND no positive repr).
    """
    models = list(raw_m2_and_n.keys())
    n = len(models)
    if n == 0:
        return {}
    _u = (9.0 / 5.0) ** 2 if unit == "F" else 1.0
    floor_m2 = (SIGMA_FLOOR * SIGMA_FLOOR) * _u
    equal_m2 = ((SIGMA_FLOOR * LOWN_INFLATE) ** 2) * _u
    _repr = repr_m2_by_model or {}

    def _repr_native(model: str) -> float:
        # Repr is supplied in the SAME basis as raw_m2 (caller's responsibility — no
        # scaling here). Absent / non-positive / non-finite repr is 0.0 (byte-identical
        # for that model).
        try:
            r = float(_repr.get(model, 0.0))
        except (TypeError, ValueError):
            return 0.0
        if not np.isfinite(r) or r <= 0.0:
            return 0.0
        return r

    precisions: dict[str, float] = {}
    have_any_signal = False
    for model in models:
        raw_m2, n_train = raw_m2_and_n[model]
        repr_native = _repr_native(model)
        try:
            raw_m2_f = float(raw_m2) if raw_m2 is not None else None
        except (TypeError, ValueError):
            raw_m2_f = None
        if raw_m2_f is None or not np.isfinite(raw_m2_f) or raw_m2_f <= 0.0 or n_train <= 0:
            # No usable raw second-moment history for this member.
            m2_eff = equal_m2
            if repr_native > 0.0:
                # Cold-start (Option C §4): a positive repr IS a precision signal even
                # with no raw history — equal prior + repr (geometry breaks the tie).
                have_any_signal = True
        else:
            have_any_signal = True
            if n_train < MIN_TRAIN:
                lam = n_train / (n_train + KAPPA)
                m2_eff = lam * raw_m2_f + (1.0 - lam) * equal_m2
            else:
                m2_eff = raw_m2_f
        # Form A: floor the residual second moment FIRST, then ADD the independent
        # representativeness observation-variance. The floor is the minimum residual
        # error; repr is a SEPARATE noise channel (rule 5), so it must not be swallowed
        # by the residual floor.
        denom = max(m2_eff, floor_m2) + repr_native
        precisions[model] = 1.0 / denom
    if not have_any_signal:
        eq = 1.0 / n
        return {m: eq for m in models}
    total = sum(precisions.values())
    if not np.isfinite(total) or total <= 0.0:
        eq = 1.0 / n
        return {m: eq for m in models}
    w = {m: max(0.0, v / total) for m, v in precisions.items()}
    s = sum(w.values())
    if s <= 0.0:
        eq = 1.0 / n
        return {m: eq for m in models}
    return {m: v / s for m, v in w.items()}


def raw_precision_center(
    raw_m2_and_n: "dict[str, tuple[float | None, int]]",
    z_by_model: "dict[str, float]",
    *,
    unit: str = "C",
    repr_m2_by_model: "dict[str, float] | None" = None,
) -> "tuple[dict[str, float], float]":
    """Shared RAW-precision center: returns (weights_by_model, mu_diagonal).

    THE single served-center functional (METHOD UNIFY 2026-06-18 + Option C). Both
    the materializer EXIT seam and the spine ENTRY path call THIS so the weights AND
    the arithmetic center ``_mu_diagonal = Σ_m w_m · z_m`` are computed identically
    from the same inputs — a shared center FUNCTIONAL, not only a shared weight
    formula (closing the #135 two-center split at the center level, not just weights).

    ``raw_m2_and_n`` / ``unit`` / ``repr_m2_by_model`` are as in
    ``raw_second_moment_weights``. ``z_by_model`` maps model → RAW member value z_m
    in the SERVING unit. The center is the convex combination Σ w_m·z_m, so it stays
    inside the member envelope [min z, max z] (non-negative weights summing to 1) —
    no-debias, no invented value.

    Returns ``({} , nan)`` when there are no models with a z value.
    """
    weights = raw_second_moment_weights(
        raw_m2_and_n, unit=unit, repr_m2_by_model=repr_m2_by_model
    )
    if weights and z_by_model:
        mu = float(sum(weights.get(m, 0.0) * z for m, z in z_by_model.items()))
        return weights, mu
    if z_by_model:
        # No precision signal at all → equal-weight RAW mean (pure RAW, never invented).
        mu = float(sum(z_by_model.values()) / len(z_by_model))
        return weights, mu
    return weights, float("nan")


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
