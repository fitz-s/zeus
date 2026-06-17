# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/probability/joint_q_band.py" block lines 546-588:
#   PredictiveParameterDraw 550-555, JointQBand dataclass 557-570 with
#   assert_valid asserting every sample ROW sums to 1 (atol 1e-9), and the
#   per-draw algorithm 572-587 — draw mu_k/sigma_k from the parameter posterior,
#   integrate ALL bins, q_k = q_k / q_k.sum() per draw (renormalize the ROW to the
#   simplex BEFORE any marginal quantile), then q_lcb = quantile(samples, alpha,
#   axis=0); Stage 6 block lines 1127-1144 — RED-on-revert test names + live signal)
#   reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; this is the EXACT fix for the live defect
#   _build_fused_q_bounds at replacement_forecast_materializer.py:1425-1426, which
#   takes np.percentile(probs, 5, axis=0) over (draws x bins) RAW per-bin mass with
#   NO per-row simplex renormalization).
#   Live dependencies (ALL already built; imported, never re-implemented):
#     - src/probability/joint_q.py::build_joint_q  (the SAME bin integrator + the
#                       SAME q = q/q.sum() row normalization the point q uses; this
#                       module just VARIES the (mu, sigma) per draw and reuses it as
#                       integrate_all_bins -> a row already on the simplex)
#     - src/probability/joint_q.py::JointQ / JointQError
#     - src/probability/outcome_space.py::OutcomeSpace  (the complete Omega)
#     - src/forecast/predictive_distribution_builder.py::PredictiveDistribution
#                       (mu_native for the mu-draw mean; sigma_native for the
#                       sigma-draw center; sigma_components for the draw dispersions;
#                       distribution_family / day0 carried unchanged through replace())
#     - src/forecast/sigma_authority.py::SigmaComponents
#                       (center_parameter_se_native is THE mu-draw SE — the live home
#                       of the spec's "center_parameter_se"; realized_floor_native is
#                       the sigma-draw floor so no draw goes sub-realized)
"""JointQBand — the coherent q_lcb (per-draw parameter sampling, row-simplex first).

This is Stage 6b of the q-kernel rebuild (consult_build_spec.md lines 546-588,
1127-1144). It produces a COHERENT lower/upper credible band on the joint q by
propagating the PARAMETER POSTERIOR through the q integrator and reading marginal
quantiles ONLY AFTER each draw has been renormalized to the probability simplex.

THE DEFECT THIS REPLACES (drift ledger / spec lines 587, 1135;
replacement_forecast_materializer.py:1425-1426 ``_build_fused_q_bounds``):

  The live fused-q-bound builder forms ``probs`` of shape (n_draws x n_bins) — the
  RAW per-bin integrated mass for each parameter draw — and takes
  ``q_lcb = np.percentile(probs, 5, axis=0)`` PER BIN, with NO per-row
  renormalization. Because each draw's raw row does not sum to 1, the per-bin 5th
  percentile is taken over masses that are not coherent probabilities. A narrow,
  high-belief MODAL bin (one in which most draws place a tight spike) sees its
  low-quantile mass driven toward ~0 by the handful of draws whose spike landed in a
  NEIGHBORING bin — an artifact of count / center granularity, NOT a real downside.
  The modal q_lcb COLLAPSES to ~0 and the winning ring bin is sold as if worthless.

THE CORRECTED TRANSFORMATION (operator law — make the bad output mathematically
impossible; NO floor/cap/clamp bolted onto the collapsed value — the GENERATOR is
the fix):

  For each draw k:
    1. ``mu_k    = draw_mu(pd)``     — drawn from the center-parameter posterior
                                       ``N(pd.mu_native, center_parameter_se)``.
    2. ``sigma_k = draw_sigma(pd)``  — drawn from the width posterior, floored
                                       positive AND at the realized floor.
    3. ``q_k = integrate_all_bins(pd_k, omega)`` — integrate EVERY bin of the
       COMPLETE Omega under the SAME settlement-conditioned integrator the point q
       uses (``build_joint_q``), which ALSO performs ``q = q / q.sum()``. So each
       ``q_k`` is ALREADY a point on the probability simplex (``sum(q_k) == 1``):
       the per-row renormalization happens INSIDE the generator, before this module
       ever stacks the rows.
    4. ``samples[k, :] = q_k`` — a coherent-probability row.

  ``q_lcb = np.quantile(samples, alpha, axis=0)`` and
  ``q_ucb = np.quantile(samples, 1 - alpha, axis=0)`` are then marginal quantiles of
  COHERENT joint distributions. A modal bin's q_lcb is the alpha-quantile of its
  mass ACROSS draws that each already integrate to 1 — so a tight modal spike that
  most draws agree on keeps a high q_lcb; it can no longer be hollowed out by a few
  draws whose spike shifted one bin over, because every row was renormalized first.

  ``assert_valid`` asserts every SAMPLE ROW sums to 1 within 1e-9 (spec line 570).
  This is a re-check of an invariant guaranteed by ``build_joint_q``'s normalization
  inside ``integrate_all_bins`` — it is NOT a renormalization gate that fixes a bad
  row. There is no path that stacks an un-normalized row: the only way a row enters
  ``samples`` is as ``build_joint_q(pd_k, omega).q``, which is on the simplex by
  construction.

basis = "PARAMETER_POSTERIOR_SIMPLEX_V1".

DETERMINISM: the draws are seeded from the predictive distribution's
``identity_hash`` so the band is reproducible for a fixed (pd, omega, alpha,
n_draws) — a receipt carries the ``sample_hash`` proving which exact draw matrix
produced the band.

DRIFT RESOLVED (recorded per operator law; see the implementation report):

  The spec algorithm writes ``mu_k = draw_mu(pd.center, pd.sigma_components)``, but
  the LIVE ``CenterEstimate`` (src/forecast/center.py) carries NO
  ``center_parameter_se`` field — the center-parameter standard error lives on
  ``SigmaComponents.center_parameter_se_native`` (src/forecast/sigma_authority.py).
  Resolution (toward the live type, per the drift ledger "prefer Actual-live"
  directive): the mu-draw SE is read from ``pd.sigma_components.center_parameter_se_native``
  and the mu-draw MEAN is the SERVED ``pd.mu_native`` (which is the day0-corrected
  center ``day0.center_after_native`` when day0 is active, exactly the center q is
  integrated around) — NOT ``pd.center.mu_native`` (the pre-day0 center). This keeps
  the parameter draw self-consistent with the served point distribution.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from src.forecast.predictive_distribution_builder import PredictiveDistribution
from src.probability.joint_q import JointQ, JointQError, build_joint_q
from src.probability.outcome_space import OutcomeSpace

# Default number of parameter-posterior draws and the default lower/upper tail
# probability. ``alpha`` is the band's lower tail (q_lcb = alpha-quantile); the
# upper credible bound is the (1 - alpha)-quantile. 0.05 matches the live
# _build_fused_q_bounds 5th/95th-percentile band this module replaces.
DEFAULT_N_DRAWS: int = 4000
DEFAULT_ALPHA: float = 0.05

# A small positive floor for the drawn sigma so the per-draw build_joint_q never
# hits its width-less / ineligible gate (sigma must be > 0). The draw is ALSO
# floored at the realized sigma floor below (the same sub-realized-sigma invariant
# the sigma authority enforces), so this constant is only the last-ditch positivity
# guard for a degenerate (zero realized floor, zero served sigma) input — which
# cannot occur for a live-eligible distribution.
_SIGMA_POSITIVE_FLOOR: float = 1e-6


class JointQBandError(ValueError):
    """Raised when a JointQBand cannot be built as a valid simplex draw matrix.

    Fail-closed signal: the predictive distribution is ineligible (no width — the
    point q itself is unconstructable), or a degenerate draw count / alpha was
    requested. The band is refused rather than served from an incoherent draw
    matrix.
    """


# ---------------------------------------------------------------------------
# PredictiveParameterDraw (spec lines 550-555) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PredictiveParameterDraw:
    """One draw from the predictive PARAMETER posterior (spec lines 550-555).

    Field names are verbatim from consult_build_spec.md. Each draw is the
    (mu, sigma) the joint q is integrated under for one Monte-Carlo row, plus the
    debias / center-error context that produced it — retained so a receipt can
    reconstruct the draw matrix and the band's row-sum statistics.

    * ``mu_native`` — the drawn center in the settlement native unit
      (``N(pd.mu_native, center_parameter_se_native)``).
    * ``sigma_native`` — the drawn predictive width, floored positive and at the
      realized floor (the sub-realized-sigma invariant holds per draw).
    * ``debias_shift_native`` — the aggregate de-bias shift carried on the draw
      (constant across draws; the de-bias is applied ONCE upstream).
    * ``center_error_native`` — the center-parameter standard error the mu draw was
      sampled with (== ``sigma_components.center_parameter_se_native``).
    """

    mu_native: float
    sigma_native: float
    debias_shift_native: float
    center_error_native: float


# ---------------------------------------------------------------------------
# JointQBand (spec lines 557-570) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JointQBand:
    """A coherent credible band on the joint q (spec lines 557-570).

    Field names are verbatim from consult_build_spec.md.

    * ``joint_q`` — the POINT joint q (the served distribution the band brackets).
    * ``samples`` — the (n_draws, n_bins) draw matrix. EVERY ROW is a coherent joint
      distribution on the probability simplex (``sum(row) == 1`` within 1e-9),
      because each row is ``build_joint_q(pd_k, omega).q`` — integrated AND
      normalized inside the generator.
    * ``q_lcb`` / ``q_ucb`` — the per-bin lower / upper credible bounds: the
      ``alpha`` / ``1 - alpha`` marginal quantiles of ``samples`` along axis 0. These
      are quantiles of COHERENT distributions, so a modal bin's lower bound is NOT
      hollowed out by neighbor-shifted draws (the collapse the spec replaces).
    * ``alpha`` — the lower tail probability (q_lcb quantile).
    * ``basis`` — ``"PARAMETER_POSTERIOR_SIMPLEX_V1"`` (the generator contract).
    * ``sample_hash`` — a deterministic hash over the draw matrix proving which
      exact samples produced the band.

    ``assert_valid`` (spec line 570) re-checks that every sample row is a
    non-negative point on the simplex — a re-proof of the invariant
    ``build_joint_q`` guarantees, never a renormalization gate.
    """

    joint_q: JointQ
    samples: np.ndarray  # shape (n_draws, n_bins)
    q_lcb: np.ndarray
    q_ucb: np.ndarray
    alpha: float
    basis: Literal["PARAMETER_POSTERIOR_SIMPLEX_V1"]
    sample_hash: str

    def assert_valid(self) -> None:
        """Assert the draw matrix is 2-D, non-negative, and every ROW sums to 1.

        Spec lines 568-570 verbatim: ``samples.ndim == 2``; ``samples >= 0``
        everywhere; ``samples.sum(axis=1)`` is all-ones within ``atol=1e-9``. The
        row-sum-one property is GUARANTEED by the ``q = q / q.sum()`` inside
        ``build_joint_q`` (each row IS a point joint q), so this is a cheap re-proof
        a deserialized band can run — not the mechanism that enforces the simplex.
        """
        assert self.samples.ndim == 2, "JointQBand.samples must be 2-D (n_draws, n_bins)"
        assert np.all(self.samples >= 0), "JointQBand.samples has a negative mass"
        assert np.allclose(self.samples.sum(axis=1), 1.0, atol=1e-9), (
            "JointQBand.samples has a row that does not sum to 1 — a draw was not "
            "renormalized to the simplex (the defect this module replaces)"
        )


# ---------------------------------------------------------------------------
# Parameter-posterior draws (spec line 577-578: draw_mu / draw_sigma).
#
# The mu draw is the center-parameter posterior N(mu*, center_parameter_se). The
# sigma draw is the width posterior: a positive draw centered at the served sigma
# with a dispersion tied to the sigma-component decomposition, floored at the
# realized floor so a draw can never go sub-realized (the sigma-authority invariant
# holds per draw, by construction of the draw — not by a post-hoc clamp on the band).
# ---------------------------------------------------------------------------

def _center_parameter_se(pd: PredictiveDistribution) -> float:
    """The center-parameter standard error for the mu draw (native unit).

    DRIFT RESOLUTION: the spec writes ``draw_mu(pd.center, pd.sigma_components)`` but
    the live ``CenterEstimate`` has NO ``center_parameter_se`` field — the
    center-parameter SE lives on ``SigmaComponents.center_parameter_se_native``. This
    reads that live field. Falls back to 0.0 (a degenerate point mu draw) only if it
    is absent / non-finite / non-positive, in which case every mu_k == pd.mu_native
    and the band's width is carried entirely by the sigma draw.
    """
    se = float(getattr(pd.sigma_components, "center_parameter_se_native", 0.0) or 0.0)
    return se if np.isfinite(se) and se > 0.0 else 0.0


def _sigma_draw_dispersion(pd: PredictiveDistribution) -> float:
    """The dispersion (sd) of the sigma draw (native unit).

    The width itself is uncertain. Its posterior dispersion is informed by the
    sigma-component decomposition: the model-dispersion candidate is the part of the
    served width that is an ESTIMATE (as opposed to the realized floor, which is a
    measured error), so its magnitude is a conservative scale for how much sigma
    could move draw-to-draw. We use a quarter of the model-dispersion component
    (capped at a quarter of the served sigma) so the sigma draw is a genuine but
    modest perturbation around the served width — never a wild multiplier that would
    swamp the mu draw. The draw is floored below so the dispersion only ever WIDENS
    the band, never narrows the served sigma.
    """
    model_disp = float(
        getattr(pd.sigma_components, "model_dispersion_native", 0.0) or 0.0
    )
    served = float(pd.sigma_native)
    scale = 0.25 * abs(model_disp)
    cap = 0.25 * abs(served)
    disp = min(scale, cap) if cap > 0.0 else scale
    return disp if np.isfinite(disp) and disp > 0.0 else 0.0


def _realized_floor(pd: PredictiveDistribution) -> float:
    """The realized sigma floor the sigma draw is floored at (native unit).

    Reads ``SigmaComponents.realized_floor_native`` — the realized walk-forward
    settlement error of the cell. Every drawn sigma is at least this floor, so the
    sub-realized-sigma invariant (sigma authority guarantee 1) holds for EVERY draw,
    by construction of the draw. Falls back to 0.0 when absent (the positivity floor
    below still keeps the draw eligible).
    """
    floor = float(getattr(pd.sigma_components, "realized_floor_native", 0.0) or 0.0)
    return floor if np.isfinite(floor) and floor > 0.0 else 0.0


def draw_mu(pd: PredictiveDistribution, rng: np.random.Generator) -> float:
    """Draw mu_k from the center-parameter posterior ``N(mu*, center_parameter_se)``.

    The mean is the SERVED ``pd.mu_native`` (the day0-corrected center q integrates
    around); the sd is the live ``center_parameter_se_native``. When the SE is 0 the
    draw collapses to the point center (a degenerate but valid draw).
    """
    se = _center_parameter_se(pd)
    mu = float(pd.mu_native)
    if se <= 0.0:
        return mu
    return float(rng.normal(loc=mu, scale=se))


def draw_sigma(pd: PredictiveDistribution, rng: np.random.Generator) -> float:
    """Draw sigma_k from the width posterior, floored positive AND at the realized floor.

    Centered at the served ``pd.sigma_native`` with the sigma-component dispersion.
    The draw is floored at ``max(realized_floor, positive_floor)`` so (a) the per-draw
    ``build_joint_q`` never hits its width-less gate and (b) no draw is sub-realized —
    the sigma-authority invariant holds per draw by construction of the floor inside
    this draw, NOT by a clamp on the assembled band.
    """
    served = float(pd.sigma_native)
    disp = _sigma_draw_dispersion(pd)
    floor = max(_realized_floor(pd), _SIGMA_POSITIVE_FLOOR)
    if disp <= 0.0:
        return max(served, floor)
    drawn = float(rng.normal(loc=served, scale=disp))
    return max(drawn, floor)


# ---------------------------------------------------------------------------
# integrate_all_bins (spec line 580) — REUSE the point-q integrator + its
# row-simplex normalization. The per-draw row is ALREADY on the simplex.
# ---------------------------------------------------------------------------

def integrate_all_bins(pd_k: PredictiveDistribution, omega: OutcomeSpace) -> np.ndarray:
    """Integrate EVERY bin of Omega under draw ``pd_k`` and return the simplex row.

    Spec line 580: ``q_k = integrate_all_bins(pd_k, omega)``. This REUSES the SAME
    integrator the point q uses — ``build_joint_q`` — so the band integrates the
    identical settlement-conditioned bin transform (and the identical rounding-rule
    threading) the point q does; only the (mu, sigma) vary per draw. ``build_joint_q``
    performs ``q = q / q.sum()`` as the last step of its single transform, so the
    returned row is ALREADY normalized to the probability simplex — spec line 581's
    ``q_k = q_k / q_k.sum()`` is therefore satisfied INSIDE this call (re-dividing an
    already-unit row by its unit sum is the identity). The per-row renormalization is
    the generator's job, not a step bolted on after stacking.
    """
    return build_joint_q(pd_k, omega).q


# ---------------------------------------------------------------------------
# build_joint_q_band — the coherent band (spec lines 572-585).
# ---------------------------------------------------------------------------

def _sample_hash(samples: np.ndarray, alpha: float, basis: str, pd_id: str) -> str:
    """Deterministic hash over the draw matrix (the receipt's band anchor)."""
    h = hashlib.sha256()
    h.update(pd_id.encode("utf-8"))
    h.update(f"alpha={alpha!r}".encode("utf-8"))
    h.update(basis.encode("utf-8"))
    h.update(f"shape={samples.shape!r}".encode("utf-8"))
    # Hash the row-major bytes of the (rounded) matrix so the hash is stable across
    # process runs but sensitive to any change in the draws.
    h.update(np.ascontiguousarray(np.round(samples, 12)).tobytes())
    return h.hexdigest()


def _seed_from_identity(pd: PredictiveDistribution) -> int:
    """A stable 64-bit seed derived from the predictive distribution identity hash.

    Makes the draw matrix (and therefore the band + sample_hash) reproducible for a
    fixed (pd, omega, alpha, n_draws) so a receipt is verifiable.
    """
    digest = hashlib.sha256(pd.identity_hash.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def build_joint_q_band(
    pd: PredictiveDistribution,
    omega: OutcomeSpace,
    *,
    n_draws: int = DEFAULT_N_DRAWS,
    alpha: float = DEFAULT_ALPHA,
) -> JointQBand:
    """Build the coherent JointQBand (spec lines 572-585).

    Algorithm (verbatim from the spec, with the live integrator reused as
    ``integrate_all_bins``):

        for k in range(n_draws):
            mu_k    = draw_mu(pd)                       # center-parameter posterior
            sigma_k = draw_sigma(pd)                    # width posterior (floored)
            pd_k    = replace(pd, mu_native=mu_k, sigma_native=sigma_k)
            q_k     = integrate_all_bins(pd_k, omega)   # build_joint_q -> already
            q_k     = q_k / q_k.sum()                   #   on the simplex (identity)
            samples[k, :] = q_k

        q_lcb = np.quantile(samples, alpha, axis=0)
        q_ucb = np.quantile(samples, 1 - alpha, axis=0)

    Because each ``q_k`` is renormalized INSIDE ``build_joint_q`` before it is
    stacked, the marginal quantiles are taken over COHERENT joint distributions — the
    corrected transformation. The modal-bin collapse of the live
    ``_build_fused_q_bounds`` (raw per-bin percentiles over un-normalized rows) is
    therefore unconstructable: there is no un-normalized row in ``samples``.

    Refuses (raises ``JointQBandError``) an ineligible predictive distribution (no
    width — the point q is itself unconstructable) and a degenerate (n_draws < 1 or
    alpha outside (0, 0.5)) request.
    """
    if n_draws < 1:
        raise JointQBandError(f"DEGENERATE_N_DRAWS: n_draws={n_draws} (need >= 1)")
    if not (0.0 < alpha < 0.5):
        raise JointQBandError(
            f"DEGENERATE_ALPHA: alpha={alpha!r} (need 0 < alpha < 0.5)"
        )

    # The POINT joint q (the served distribution the band brackets). If the pd is
    # ineligible / width-less, build_joint_q raises JointQError — re-raise as a band
    # error so the band fails closed exactly where the point q does.
    try:
        point_q = build_joint_q(pd, omega)
    except JointQError as exc:
        raise JointQBandError(f"POINT_Q_UNCONSTRUCTABLE: {exc}") from exc

    n_bins = len(omega.bins)
    rng = np.random.default_rng(_seed_from_identity(pd))

    samples = np.empty((n_draws, n_bins), dtype=float)
    for k in range(n_draws):
        mu_k = draw_mu(pd, rng)
        sigma_k = draw_sigma(pd, rng)
        # Carry mu_k / sigma_k onto a per-draw predictive distribution; every other
        # field (distribution_family, day0, live_eligible, ...) is carried UNCHANGED,
        # so integrate_all_bins applies the identical settlement-conditioned transform
        # the point q uses — only the (mu, sigma) vary.
        pd_k = replace(pd, mu_native=float(mu_k), sigma_native=float(sigma_k))
        # integrate_all_bins == build_joint_q(pd_k, omega).q, which already did
        # q = q / q.sum(); the row is on the simplex BEFORE it is stacked.
        q_k = integrate_all_bins(pd_k, omega)
        samples[k, :] = q_k

    # Marginal quantiles of COHERENT joint distributions (each row sums to 1).
    q_lcb = np.quantile(samples, alpha, axis=0)
    q_ucb = np.quantile(samples, 1.0 - alpha, axis=0)

    basis: Literal["PARAMETER_POSTERIOR_SIMPLEX_V1"] = "PARAMETER_POSTERIOR_SIMPLEX_V1"
    sample_hash = _sample_hash(samples, alpha, basis, pd.identity_hash)

    band = JointQBand(
        joint_q=point_q,
        samples=samples,
        q_lcb=q_lcb,
        q_ucb=q_ucb,
        alpha=alpha,
        basis=basis,
        sample_hash=sample_hash,
    )
    band.assert_valid()
    return band
