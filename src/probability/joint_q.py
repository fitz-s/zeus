# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/probability/joint_q.py" block lines 505-544: the JointQ dataclass
#   509-521 with assert_valid; build_joint_q point integration 523-541 incl. the
#   NORMAL / DAY0_HIGH_MAX_NORMAL / DAY0_LOW_MIN_NORMAL family switch and the
#   q = q / q.sum() normalization; Stage 6 block lines 1127-1144) reconciled
#   against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; the point q is ONE contract: integrate
#   every bin over its settlement preimage, clip >= 0, then q = q/q.sum() so
#   Sigma q = 1 BY CONSTRUCTION; the settlement preimage MUST honor the city
#   rounding_rule — HK oracle_truncate vs WMO half_up — and use the SAME live
#   preimage/integrators the rest of the engine uses, byte-identical to settlement).
#   Live dependencies (all already built; imported, never re-implemented):
#     - src/forecast/predictive_distribution_builder.py  (PredictiveDistribution —
#                       the ONLY input to q: mu_native, sigma_native,
#                       distribution_family, day0.observed_extreme_native,
#                       identity_hash)
#     - src/probability/outcome_space.py                 (OutcomeSpace / OutcomeBin;
#                       resolution.rounding_rule is THE per-city settlement rule)
#     - src/calibration/emos.py::bin_probability_settlement  (the NORMAL
#                       settlement-preimage integrator that already accepts
#                       rounding_rule — threaded here; NEVER defaulted to WMO)
#     - src/forecast/day0_conditioner.py                 (probability_high_day0_bin
#                       / probability_low_day0_bin / day0_bin_preimage_native —
#                       REUSED for the DAY0 families and their settlement preimage)
#     - src/contracts/settlement_semantics.py::settlement_preimage_offsets
#                       (the single declarative preimage source, reached via the
#                       two integrators above; never re-derived here)
"""JointQ — ONE normalized joint distribution (Sigma q = 1) over the complete Omega.

This is Stage 6a of the q-kernel rebuild (consult_build_spec.md lines 505-544,
1127-1144). ``build_joint_q`` integrates the single ``PredictiveDistribution``
over EVERY bin of the complete ``OutcomeSpace`` (Omega) — including the
non-tradeable tail/shoulder bins — and returns ONE ``JointQ`` whose mass vector
sums to exactly 1 by construction.

THE ONE CONTRACT (operator law — make the bad output mathematically impossible,
NOT a downstream gate/cap that catches it):

  q is built in ONE place by ONE transform:

      probs[i] = integrate bin_i over its SETTLEMENT PREIMAGE under the family's
                 settlement-conditioned probability law
      q        = clip(probs, >= 0)            # masses are probabilities, never < 0
      q        = q / q.sum()                  # ONE normalization, Sigma q == 1

  Because the normalization is the LAST step of the single transform, a joint q
  that does not sum to 1 is unconstructable: ``assert_valid`` re-checks
  ``|q.sum() - 1| <= 1e-9`` but the equality is GUARANTEED by the
  ``q = q / q.sum()`` line, not enforced by a separate renormalization gate. The
  defect this replaces (drift ledger / spec line 543) is the old fused-q path that
  integrated per-bin masses at three independent sites and did NOT row-normalize
  before taking percentiles; here there is ONE site and ONE normalization.

THE SETTLEMENT-PREIMAGE / ROUNDING-RULE INVARIANT (spec [HIGH], drift ledger V3/V4):

  Every bin is integrated over its SETTLEMENT PREIMAGE, and that preimage is
  derived from ``omega.resolution.rounding_rule`` — the per-city settlement rule —
  threaded into the SAME live integrators the rest of the engine uses:

    * NORMAL family  -> ``bin_probability_settlement(mu, sigma, lo_label, hi_label,
                          rounding_rule=omega.resolution.rounding_rule)`` (emos.py).
                          The ``rounding_rule`` kwarg is passed EXPLICITLY; it is
                          NEVER allowed to fall back to the WMO default. For Hong
                          Kong (``oracle_truncate``) this integrates the asymmetric
                          ``[t, t+1)`` preimage; the old ``build_emos_q`` bug
                          silently used the symmetric WMO ``[t-0.5, t+0.5)`` and
                          shifted every HK bin's mass up by ~half a degree. That
                          bug is structurally impossible here because the only
                          integrator call threads the resolution's own rule.
    * DAY0 families  -> the bin's settlement-preimage bounds come from
                          ``day0_bin_preimage_native(lo_label, hi_label,
                          rounding_rule=omega.resolution.rounding_rule)``, the SAME
                          wrapper over ``settlement_preimage_offsets`` the day0
                          conditioner uses, then the settlement-conditioned bin mass
                          comes from ``probability_high_day0_bin`` /
                          ``probability_low_day0_bin``. So the HK truncation
                          preimage reaches the day0 lane too — the rule cannot be
                          dropped on either path.

  There is exactly ONE place that names a rounding rule (``omega.resolution``), so
  HK can never silently default to WMO: the wrong preimage is unconstructable.

DISTRIBUTION-FAMILY SWITCH (spec lines 531-537): the family is read off the
predictive distribution (``pd.distribution_family``), never re-decided here. A
``NORMAL`` pd integrates the bare predictive Normal; a ``DAY0_HIGH_MAX_NORMAL`` /
``DAY0_LOW_MIN_NORMAL`` pd integrates the settlement-conditioned law
``Y = max(obs_high, X)`` / ``Y = min(obs_low, X)`` using
``pd.day0.observed_extreme_native`` as the observed support bound — so a bin
entirely below the observed running high (resp. above the observed low) carries
q = 0 by the definition of the transform, not by a clamp on a bare-Normal value.

LIVE ELIGIBILITY: q is only meaningful for a live-eligible predictive distribution
(``sigma_native > 0``). ``build_joint_q`` refuses to integrate a width-less /
ineligible distribution — it raises ``JointQError`` rather than returning a
degenerate q — because the live-eligibility gate is the predictive-distribution σ
authority, not a fallback here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from typing import Literal, Mapping

import numpy as np
from scipy.stats import norm as _scipy_norm

from src.calibration.emos import bin_probability_settlement
from src.forecast.day0_conditioner import (
    day0_bin_preimage_native,
    probability_high_day0_bin,
    probability_low_day0_bin,
)
from src.forecast.predictive_distribution_builder import PredictiveDistribution
from src.probability.outcome_space import OutcomeSpace

# The q_source literal domain (spec line 515) — one per distribution family.
QSource = Literal[
    "SETTLEMENT_STATION_NORMAL_V1",
    "DAY0_HIGH_MAX_NORMAL_V1",
    "DAY0_LOW_MIN_NORMAL_V1",
]

# Map the predictive distribution_family onto the q_source receipt literal.
_FAMILY_TO_Q_SOURCE: Mapping[str, QSource] = {
    "NORMAL": "SETTLEMENT_STATION_NORMAL_V1",
    "DAY0_HIGH_MAX_NORMAL": "DAY0_HIGH_MAX_NORMAL_V1",
    "DAY0_LOW_MIN_NORMAL": "DAY0_LOW_MIN_NORMAL_V1",
}


class JointQError(ValueError):
    """Raised when a JointQ cannot be built as a valid normalized joint distribution.

    Fail-closed signal: the predictive distribution is ineligible (no width), the
    observed day0 extreme required by a DAY0 family is missing, or the complete
    partition carries no mass (a non-positive sum — an incomplete support, which
    can never happen for a valid MECE Omega with sigma > 0). In every case the
    joint q is refused rather than served degenerate.
    """


# ---------------------------------------------------------------------------
# JointQ (spec lines 509-521) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JointQ:
    """One normalized joint distribution over the complete Omega (spec 509-521).

    Field names are verbatim from consult_build_spec.md. ``q`` is the mass vector
    aligned 1:1 with ``omega.bins`` (``q[i]`` is the probability of ``omega.bins[i]``
    settling); ``q_by_bin_id`` is the same mass keyed by ``bin_id`` for the decision
    layer. ``q_sum`` is the recorded post-normalization sum (always 1 within
    tolerance). ``identity_hash`` lets a receipt prove which exact (predictive
    distribution, Omega) pair this q was integrated over.

    ``assert_valid`` re-checks the two structural invariants, but both hold BY
    CONSTRUCTION of ``build_joint_q`` (clip >= 0, then divide by the sum):
      * every mass is non-negative;
      * the masses sum to 1 within 1e-9.
    """

    omega: OutcomeSpace
    q: np.ndarray
    q_by_bin_id: Mapping[str, float]
    predictive_distribution_id: str
    q_source: QSource
    q_sum: float
    identity_hash: str

    def assert_valid(self) -> None:
        """Assert q >= 0 everywhere and Sigma q == 1 (within 1e-9) — spec 519-521.

        These are re-checks of invariants guaranteed by ``build_joint_q``'s single
        ``clip >= 0`` + ``q = q / q.sum()`` transform; they exist so a downstream
        consumer can cheaply re-prove the contract on a deserialized JointQ.
        """
        assert np.all(self.q >= 0), "JointQ.q has a negative mass"
        assert abs(float(self.q.sum()) - 1.0) <= 1e-9, (
            f"JointQ.q does not sum to 1 (sum={float(self.q.sum())!r})"
        )


# ---------------------------------------------------------------------------
# Identity hash (the receipt anchor; proves the exact pd + Omega q ran over).
# ---------------------------------------------------------------------------

def _identity_hash(
    omega: OutcomeSpace,
    pd: PredictiveDistribution,
    q_source: QSource,
    q: np.ndarray,
) -> str:
    """Deterministic identity hash over the predictive distribution, Omega, and q.

    Anchors a candidate receipt to the exact joint q: the predictive
    distribution's own ``identity_hash`` (which already covers mu/sigma/family/day0
    /sigma-basis), the Omega ``topology_hash`` + ``rounding_rule``, the ``q_source``,
    and the rounded mass vector. Stable across process runs.
    """
    h = hashlib.sha256()
    h.update(pd.identity_hash.encode("utf-8"))
    h.update(omega.topology_hash.encode("utf-8"))
    h.update(omega.resolution.rounding_rule.encode("utf-8"))
    h.update(q_source.encode("utf-8"))
    for b, mass in zip(omega.bins, q):
        h.update(f"{b.bin_id}={float(mass)!r}".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Honest q-SHAPE calibration — the FITTED settlement-frequency temper exponent.
#
# WHY (live order pathology 2026-06-21, docs/evidence/live_order_pathology/):
#   The served predictive σ IS the realized walk-forward settlement RMSE
#   (src/forecast/sigma_authority.py — the calibrated WIDTH). But a Normal with the
#   correct VARIANCE still mis-matches the SHAPE of the realized settlement
#   distribution: the realized frequency is THIN in the far / open-shoulder tail
#   relative to a Gaussian. For an already-wide cell (Milan high, μ*≈36°C,
#   σ≈2.0°C) the bare-Normal q over-states q(40°C)=2.78% and the >=42 open shoulder,
#   where realized settlement frequency is ~0.5%. The sub-cent edge rule then
#   mass-buys those impossible tail bins. Widening σ (k>1) or a uniform pedestal
#   (w>0) push MORE mass INTO the tail (the wrong direction); the honest correction
#   is a settlement-frequency SHAPE temper.
#
# THE TRANSFORM (operator law — make the bad output mathematically impossible, by
#   the GENERATOR, not a downstream cap/haircut/floor):
#
#       q_tempered[i] = q_normal[i] ** gamma          # then the SAME q/q.sum() renorm
#
#   A single exponent γ ("settlement-frequency temper") reshapes the WHOLE mass
#   vector before the one normalization. γ > 1 shrinks low-mass (far-tail / open
#   shoulder) bins MORE than high-mass (center / near-ring) bins, so the over-stated
#   tail mass is pulled toward center to match realized frequency; γ = 1.0 is the
#   IDENTITY (byte-identical to the un-tempered Normal). On the Milan case γ≈1.3
#   maps q(36)=22.5%, q(38)=11.9%, q(40)=1.76% (≈realized) and the open shoulder
#   ~0.1% while the near-center ring (d1-2) is PRESERVED — the only real edge
#   survives, the impossible tail gets an HONEST ≈0 q and fails the existing
#   edge_lcb>0 gate naturally with NO new filter (it is calibration, not a cap).
#
#   MONOTONE-CONSERVATIVE on the open shoulder (Paris >=26 relationship invariant):
#   γ ≥ 1 can only DECREASE a low-mass open-shoulder bin's RELATIVE mass after
#   renormalization — it can never INFLATE a catch-all — so the catch-all coherence
#   category the materializer guards is unconstructable here by construction.
#
# OPERATOR LAW ("没有一个人可以在没有数学支持下决定一个 hard coded value"): γ is FITTED by
#   maximum likelihood to realized settlement frequency (the SAME proper-scoring-rule
#   estimator the σ-scale artifact uses), never hand-set. It is read fail-soft from a
#   per-settlement-unit-family artifact; ABSENT / unfitted / malformed → γ = 1.0
#   (INERT, byte-identical). Mirrors src/data/replacement_forecast_materializer.py
#   ::_replacement_sigma_scale_lookup (the established fitted-artifact read pattern).
# ---------------------------------------------------------------------------

_Q_SHAPE_TEMPER_FIT_PATH = "state/q_shape_temper_fit.json"


def _q_shape_temper_lookup(unit: str) -> float:
    """FITTED settlement-frequency temper exponent γ for a settlement-unit family.

    Reads ``state/q_shape_temper_fit.json`` (written ONLY by the temper fitter — MLE
    over settled cells) and returns ``gamma`` for the given settlement unit family
    ('C' / 'F'). The temper q_adj(bin) = q_normal(bin) ** gamma (then the single
    q/q.sum() renorm) pulls the over-stated far/open-shoulder tail mass toward center
    to match realized settlement frequency.

    Returns ``gamma`` where:
      - artifact present AND family entry has ``fitted=True`` → ``gamma`` (clamped
        finite and ``>= 1.0``; a fit may only TEMPER the tail toward center, never
        FATTEN it — γ<1 would inflate the tail, the pathology this fixes).
      - artifact missing / malformed / family absent / family ``fitted=False``
        (REFUSED, e.g. a unit family with too few settled cells) → ``1.0`` IDENTITY
        (byte-identical to the un-tempered Normal). FAIL-SOFT: any error → ``1.0``.
        Never raises (a calibration-artifact fault must NEVER block a live decision).
    """
    try:
        path = _Q_SHAPE_TEMPER_FIT_PATH
        if not os.path.isabs(path):
            repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path = os.path.join(repo, _Q_SHAPE_TEMPER_FIT_PATH)
        if not os.path.exists(path):
            return 1.0
        with open(path, "r", encoding="utf-8") as fh:
            artifact = json.load(fh)
        fam = (artifact.get("families") or {}).get(str(unit).upper())
        if not isinstance(fam, dict) or not fam.get("fitted"):
            return 1.0
        gamma = float(fam.get("gamma", 1.0))
        # Clamp to the honest domain: a temper may only pull the tail IN (γ >= 1.0).
        # A non-finite / sub-1.0 value is inert (1.0) — the fix never FATTENS the tail.
        if not (math.isfinite(gamma) and gamma >= 1.0):
            return 1.0
        return gamma
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# build_joint_q — the ONE point-q integration (spec lines 523-541).
# ---------------------------------------------------------------------------

def build_joint_q(pd: PredictiveDistribution, omega: OutcomeSpace) -> JointQ:
    """Integrate the predictive distribution over Omega into ONE normalized q.

    Spec lines 523-541, implemented as ONE transform so a non-normalized joint q is
    unconstructable:

      1. For each bin, integrate over its SETTLEMENT PREIMAGE under the family's
         settlement-conditioned probability law. The preimage is derived from
         ``omega.resolution.rounding_rule`` (HK ``oracle_truncate`` vs WMO
         ``wmo_half_up``) threaded into the live integrators — NEVER defaulted.
      2. ``q = clip(probs, >= 0)`` — masses are probabilities, never negative.
      3. ``q = q / q.sum()`` — ONE normalization; Sigma q == 1 by construction.

    Refuses (raises ``JointQError``) a width-less / ineligible predictive
    distribution, a DAY0 family with no observed extreme, or a complete partition
    whose mass sums to a non-positive value (an impossible state for a valid MECE
    Omega with sigma > 0 — it would mean the support is not covered).
    """
    # --- live-eligibility gate is the pd sigma authority, not a fallback here ----
    sigma = float(pd.sigma_native)
    if not pd.live_eligible or sigma <= 0.0:
        raise JointQError(
            "PREDICTIVE_DISTRIBUTION_INELIGIBLE: cannot integrate q over a "
            f"width-less / ineligible distribution (live_eligible={pd.live_eligible}, "
            f"sigma_native={sigma!r}, reason={pd.ineligibility_reason!r})"
        )

    mu = float(pd.mu_native)
    family = pd.distribution_family
    rule = omega.resolution.rounding_rule

    q_source = _FAMILY_TO_Q_SOURCE.get(family)
    if q_source is None:  # pragma: no cover - defensive; family is a closed Literal
        raise JointQError(f"UNKNOWN_DISTRIBUTION_FAMILY: {family!r}")

    # The predictive Normal CDF folded with this pd's (mu, sigma):
    # normal_cdf(x) = Phi((x - mu) / sigma). Same scipy norm CDF the NORMAL
    # integrator (bin_probability_settlement) uses, so the two lanes integrate the
    # identical underlying Gaussian. scipy.norm.cdf already returns 0.0 / 1.0 at
    # -inf / +inf, so open shoulders need no special-casing.
    def _normal_cdf(x: float) -> float:
        return float(_scipy_norm.cdf((x - mu) / sigma))

    # The observed support bound for the DAY0 families (the running extreme).
    obs_extreme = pd.day0.observed_extreme_native
    if family in ("DAY0_HIGH_MAX_NORMAL", "DAY0_LOW_MIN_NORMAL") and obs_extreme is None:
        raise JointQError(
            f"DAY0_EXTREME_MISSING: family={family!r} requires "
            "pd.day0.observed_extreme_native but it is None"
        )

    probs: list[float] = []
    for b in omega.bins:
        if family == "NORMAL":
            # The bare predictive-Normal settlement integral over the bin preimage.
            # rounding_rule is threaded EXPLICITLY (the HK fix; never WMO default).
            p = bin_probability_settlement(
                mu,
                sigma,
                b.lower_native,
                b.upper_native,
                rounding_rule=rule,
            )
        elif family == "DAY0_HIGH_MAX_NORMAL":
            lo, hi = day0_bin_preimage_native(
                b.lower_native, b.upper_native, rounding_rule=rule
            )
            p = probability_high_day0_bin(
                float(obs_extreme), lo, hi, _normal_cdf
            )
        else:  # DAY0_LOW_MIN_NORMAL
            lo, hi = day0_bin_preimage_native(
                b.lower_native, b.upper_native, rounding_rule=rule
            )
            p = probability_low_day0_bin(
                float(obs_extreme), lo, hi, _normal_cdf
            )
        probs.append(p)

    # ONE transform: clip to non-negative, apply the FITTED settlement-frequency
    # SHAPE temper, then the SINGLE normalization. Sigma q == 1 by construction of
    # this division (not by a separate renorm gate).
    q = np.clip(np.asarray(probs, dtype=float), 0.0, None)
    # Honest q-SHAPE calibration (live order pathology 2026-06-21): q_adj = q ** gamma,
    # where gamma is the FITTED settlement-frequency temper for this settlement-unit
    # family (γ=1.0 IDENTITY when no fitted artifact — byte-identical to the bare
    # Normal). γ>1 shrinks the over-stated far/open-shoulder tail mass MORE than the
    # center/near-ring so the impossible tail gets an HONEST ≈0 q (matching realized
    # settlement frequency) while the near-center ring edge is preserved. It is part of
    # the ONE q transform (generator-level calibration), NOT a downstream cap/haircut,
    # so the temper propagates identically to every JointQBand parameter draw (each is a
    # build_joint_q call) — both the direction-law point q and the edge_lcb band samples
    # see the honest tail. The temper is monotone-conservative on open-shoulder bins
    # (γ≥1 can only reduce a low-mass bin's RELATIVE mass after renorm — never inflate a
    # catch-all), so the Paris >=26 catch-all coherence category stays unconstructable.
    _gamma = _q_shape_temper_lookup(omega.resolution.measurement_unit)
    if _gamma != 1.0:
        q = np.power(q, _gamma)
    total = float(q.sum())
    if not np.isfinite(total) or total <= 0.0:
        # A complete MECE partition over (-inf, +inf) with sigma > 0 always carries
        # positive total mass; a non-positive sum means the support is not covered
        # (an incomplete Omega) — fail closed rather than divide by zero into NaN.
        raise JointQError(
            f"DEGENERATE_JOINT_MASS: q.sum()={total!r} over {len(omega.bins)} bins; "
            "the partition carries no probability mass (incomplete support)"
        )
    q = q / total

    q_by_bin_id = {b.bin_id: float(m) for b, m in zip(omega.bins, q)}
    identity_hash = _identity_hash(omega, pd, q_source, q)

    return JointQ(
        omega=omega,
        q=q,
        q_by_bin_id=q_by_bin_id,
        predictive_distribution_id=pd.identity_hash,
        q_source=q_source,
        q_sum=float(q.sum()),
        identity_hash=identity_hash,
    )
