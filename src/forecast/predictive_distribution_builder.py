# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/forecast/predictive_distribution_builder.py" block lines 344-365:
#   the PredictiveDistribution dataclass with EXACT field names; the
#   [BLOCKER] forecast-authority-split fix lines 23, 28 — enforce mu* in
#   [min,max] debiased members unless a day0 observed extreme licenses leaving;
#   the sigma-missing fallback lines 426-430; the Stage 3 block lines 1072-1090).
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; this is wired into the reactor at
#   integration/Wave 5, NOT now; prefer the live dependency types).
#   Dependencies (ALL now built — imported and assembled here; this is the only
#   live builder):
#     - src/forecast/types.py            (ForecastCase / RawModelMember / FreshModelSet)
#     - src/forecast/debias_authority.py (DebiasAuthority — applied ONCE)
#     - src/forecast/center.py           (build_center -> CenterEstimate; envelope-enforced)
#     - src/forecast/day0_conditioner.py (condition_day0 -> Day0Conditioning; may license
#                                         leaving the envelope)
#     - src/forecast/sigma_authority.py  (build_sigma -> SigmaDecision; realized-floored,
#                                         live_eligible=False when sigma authority missing)
#     - src/probability/event_resolution.py + src/probability/outcome_space.py
#                                        (the Omega/resolution this PD feeds)
"""PredictiveDistributionBuilder — the ONE live predictive-distribution authority.

This is Stage 3/PD of the q-kernel rebuild. It is the SINGLE live builder that
assembles the four already-built forecast-spine authorities into ONE
``PredictiveDistribution`` — the only input to q (spec line 365):

    DebiasAuthority.apply  (ONCE)                 -> debiased member values
        -> CenterEstimate  (envelope-enforced)    -> mu* in [min,max] debiased
        -> Day0Conditioning (may license leaving) -> support-corrected center
        -> SigmaComponents (realized-floored)     -> served sigma (or ineligible)

It replaces the [BLOCKER] forecast-authority split (spec lines 23, 28): the live
reactor had an EMOS lane (``build_emos_q`` from the event-snapshot path) AND a
fallback/day0 lane (``_maybe_apply_edli_bias_correction``) that could produce
DIFFERENT mu*/sigma/q semantics for the same family. There is now ONE assembly,
so every live path returns the SAME receipt contract.

THE HEADLINE INVARIANT (spec lines 23, 28, 1089) — implemented as the assembly
ORDER, not as a downstream gate/cap:

    mu* cannot select Tokyo 26 when the fresh debiased members are 20-23.

This holds by construction because the only two things that set ``mu_native`` are:

  1. ``CenterEstimate.mu_native`` from ``build_center``, which is PROVEN to lie in
     ``[debiased_member_min_native, debiased_member_max_native]`` (the center
     module constructs it as a convex combination of the debiased members and
     falls back to the in-envelope consensus if EMOS proposes outside). So absent
     day0, ``mu_native`` is the envelope-enforced center — 26 is unreachable when
     the members are 20-23.

  2. ``Day0Conditioning.center_after_native`` — but ONLY when an observed running
     extreme on the relevant side was actually resolved (``day0.active``). For a
     HIGH market this is ``max(center_before, observed_high)``; the center can
     leave the envelope UPWARD to ``observed_high`` ONLY because ``observed_high``
     was physically measured today. With NO day0 observation (``day0.active`` is
     False), ``center_after_native == center_before_native`` (the conditioner's
     fail-closed identity), so the envelope-enforced center is served unchanged.

There is therefore no path by which 26 reaches ``mu_native`` when the members are
20-23 and no observed extreme licenses it: the center authority forbids it and the
day0 authority only opens the envelope toward a value that was actually observed.
The ``distribution_family`` records which regime served the center
(``NORMAL`` / ``DAY0_HIGH_MAX_NORMAL`` / ``DAY0_LOW_MIN_NORMAL``) so the q
integrator picks the matching settlement-conditioned bin transform.

LIVE ELIGIBILITY (spec lines 426-430): the predictive σ authority is the gate. If
``build_sigma`` returns ``live_eligible=False`` (no fusion capture AND no realized
floor -> ``PREDICTIVE_SIGMA_AUTHORITY_MISSING``), this builder returns a
PredictiveDistribution with ``live_eligible=False`` and the σ authority's
``ineligibility_reason`` — it does NOT silently serve a width-less member-vote q.
A REFUSED center (no fresh members) is likewise ``live_eligible=False``. Either
way the ineligible distribution STILL carries the full receipt contract (an
``identity_hash`` is always present), so a no-trade receipt is reconstructable.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np

from src.forecast.center import CenterEstimate, build_center
from src.forecast.day0_conditioner import (
    Day0Conditioning,
    Day0ObservationState,
    condition_day0,
)
from src.forecast.debias_authority import AppliedDebias, DebiasAuthority
from src.forecast.sigma_authority import SigmaComponents, SigmaDecision, build_sigma
from src.forecast.types import ForecastCase, FreshModelSet

DistributionFamily = Literal[
    "NORMAL", "DAY0_HIGH_MAX_NORMAL", "DAY0_LOW_MIN_NORMAL"
]


# ---------------------------------------------------------------------------
# PredictiveDistribution (spec lines 348-365) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PredictiveDistribution:
    """The ONE predictive distribution that feeds q (spec lines 348-365).

    Field names are verbatim from consult_build_spec.md. This object is the only
    input to q — every q / band / decision consumer reads ``mu_native``,
    ``sigma_native``, and ``distribution_family`` from HERE, never recomputing a
    center or width.

    Receipt contract: EVERY live path (eligible or not) returns this object with a
    populated ``identity_hash`` plus the full provenance sub-objects (``center``,
    ``debias``, ``day0``, ``sigma_components``), so a candidate receipt can
    reconstruct the forecast from source inputs (spec Stage-0 live signal). When
    ``live_eligible`` is False, ``ineligibility_reason`` carries the closing reason
    (the σ authority's ``PREDICTIVE_SIGMA_AUTHORITY_MISSING`` or the REFUSED-center
    reason); the distribution is otherwise structurally complete.

    * ``mu_native`` — the served center in the settlement native unit. It is the
      envelope-enforced ``CenterEstimate.mu_native`` UNLESS an active day0 support
      transform licenses leaving the envelope toward an OBSERVED extreme, in which
      case it is ``Day0Conditioning.center_after_native``.
    * ``sigma_native`` — the served predictive width (``SigmaDecision.sigma_native``),
      realized-floored; 0.0 only on an ineligible distribution.
    * ``debiased_members_native`` — the de-biased member values (settlement unit)
      the center / envelope were computed over.
    * ``member_min_native`` / ``member_max_native`` — the debiased member envelope
      bounds (== ``center.debiased_member_min/max_native``).
    """

    case: ForecastCase
    mu_native: float
    sigma_native: float
    debiased_members_native: tuple[float, ...]
    member_min_native: float
    member_max_native: float
    center: CenterEstimate
    debias: AppliedDebias
    day0: Day0Conditioning
    sigma_components: SigmaComponents
    distribution_family: DistributionFamily
    live_eligible: bool
    ineligibility_reason: Optional[str]
    identity_hash: str


# ---------------------------------------------------------------------------
# Identity hash (the receipt-contract anchor; present on EVERY live path).
# ---------------------------------------------------------------------------

def _identity_hash(
    case: ForecastCase,
    mu_native: float,
    sigma_native: float,
    debiased_members_native: tuple[float, ...],
    distribution_family: str,
    center: CenterEstimate,
    debias: AppliedDebias,
    day0: Day0Conditioning,
    sigma_components: SigmaComponents,
    live_eligible: bool,
    ineligibility_reason: Optional[str],
) -> str:
    """Deterministic identity hash over the full predictive-distribution content.

    Stable across process runs so a candidate receipt can prove which exact
    predictive distribution (which center, which de-bias, which day0 transform,
    which σ basis) q was integrated over. Computed for EVERY path — eligible and
    ineligible — so the receipt contract is identical regardless of the outcome.
    """
    h = hashlib.sha256()
    h.update(str(case.family_id).encode("utf-8"))
    h.update(str(case.station_id).encode("utf-8"))
    h.update(str(case.metric).encode("utf-8"))
    h.update(str(case.target_local_date).encode("utf-8"))
    h.update(case.resolution.semantics_version.encode("utf-8"))
    h.update(case.resolution.rounding_rule.encode("utf-8"))
    h.update(f"mu={mu_native!r}".encode("utf-8"))
    h.update(f"sigma={sigma_native!r}".encode("utf-8"))
    h.update(
        ("members=" + ",".join(repr(float(v)) for v in debiased_members_native)).encode(
            "utf-8"
        )
    )
    h.update(f"family={distribution_family}".encode("utf-8"))
    h.update(f"center_status={center.center_status}".encode("utf-8"))
    h.update(f"center_method={center.center_method}".encode("utf-8"))
    h.update(f"debias_status={debias.activation_status}".encode("utf-8"))
    h.update(f"debias_shift={debias.aggregate_shift_native!r}".encode("utf-8"))
    h.update(f"day0_status={day0.status}".encode("utf-8"))
    h.update(f"day0_active={day0.active}".encode("utf-8"))
    h.update(
        f"day0_extreme={day0.observed_extreme_native!r}".encode("utf-8")
    )
    h.update(f"sigma_artifact={sigma_components.artifact_id}".encode("utf-8"))
    h.update(
        f"sigma_after_floor={sigma_components.sigma_after_floor_native!r}".encode(
            "utf-8"
        )
    )
    h.update(f"live_eligible={live_eligible}".encode("utf-8"))
    h.update(f"ineligibility_reason={ineligibility_reason!r}".encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Distribution-family selection (spec line 360 domain).
# ---------------------------------------------------------------------------

def _distribution_family(
    metric: str,
    day0: Day0Conditioning,
) -> DistributionFamily:
    """Pick the settlement-conditioned distribution family.

    A bare predictive Normal is ``NORMAL``. When an active day0 support transform
    is in force, the family is the side-specific settlement-conditioned Normal so
    the q integrator applies ``Y = max(obs_high, X)`` (HIGH) or
    ``Y = min(obs_low, X)`` (LOW) — the ``probability_high_day0_bin`` /
    ``probability_low_day0_bin`` transforms in ``day0_conditioner``. An inactive
    day0 conditioning (``NO_DAY0`` / ``OBS_SOURCE_MISSING_REFUSED``) keeps the
    bare ``NORMAL`` family.
    """
    if not day0.active:
        return "NORMAL"
    if metric == "high":
        return "DAY0_HIGH_MAX_NORMAL"
    return "DAY0_LOW_MIN_NORMAL"


def _empty_sigma_components() -> SigmaComponents:
    """A zeroed SigmaComponents for a REFUSED-center distribution (no σ computed)."""
    return SigmaComponents(
        raw_member_spread_native=0.0,
        model_dispersion_native=0.0,
        center_parameter_se_native=0.0,
        station_representativeness_sigma_native=0.0,
        day0_remaining_process_sigma_native=0.0,
        realized_floor_native=0.0,
        sigma_before_floor_native=0.0,
        sigma_after_floor_native=0.0,
        artifact_id="none",
    )


# ---------------------------------------------------------------------------
# The builder (the single assembly; spec line 365 "This object is the only input
# to q").
# ---------------------------------------------------------------------------

class PredictiveDistributionBuilder:
    """The ONE live builder that assembles the predictive distribution.

    Holds the single ``DebiasAuthority`` (the same one the center builder applies
    once). ``build`` is the only public method; it returns a fully-populated
    ``PredictiveDistribution`` for EVERY input — there is no path that returns
    ``None`` or raises for a live family (a REFUSED center or a missing σ authority
    is expressed as ``live_eligible=False`` WITH the full receipt, never a silent
    drop).
    """

    def __init__(self, debias_authority: DebiasAuthority) -> None:
        self._debias_authority = debias_authority

    def build(
        self,
        case: ForecastCase,
        models: FreshModelSet,
        obs: Optional[Day0ObservationState] = None,
        *,
        use_emos: bool = True,
        fused_center_sd_native: Optional[float] = None,
        sigma_resid_native: Optional[float] = None,
        has_fusion_capture: bool = True,
    ) -> PredictiveDistribution:
        """Assemble the ONE predictive distribution for a family (spec lines 344-365).

        Assembly order (each authority is the single owner of its decision):

          1. ``build_center(case, models, debias_authority)`` — applies
             ``DebiasAuthority.apply`` ONCE internally and returns the
             envelope-enforced ``CenterEstimate``. Its ``debiased_member_min/max``
             ARE the debiased member envelope; ``mu_native`` is proven inside it.
          2. ``condition_day0(metric, obs, center_before=center.mu_native)`` — the
             ONLY place the envelope may be left, and only toward an OBSERVED
             extreme. Inactive when ``obs`` is None / ``obs.observed`` is False, so
             the envelope-enforced center is served unchanged.
          3. ``build_sigma(case, models, ...)`` — the realized-floored σ authority.
             When it is not live-eligible, the whole distribution is not
             live-eligible (no width-less q is served).

        The served ``mu_native`` is ``day0.center_after_native`` (which equals
        ``center.mu_native`` when day0 is inactive). The result is therefore in
        ``[member_min, member_max]`` whenever day0 is inactive, and may exceed it
        ONLY via an active day0 observed-extreme license — the exact behavior the
        spec [BLOCKER] (lines 23, 28) requires.
        """
        # --- (1) center: applies DebiasAuthority ONCE, envelope-enforced ---------
        center = build_center(case, models, self._debias_authority, use_emos=use_emos)

        # Re-apply the SAME single de-bias to recover the debiased member vector and
        # its telemetry record. ``DebiasAuthority.apply`` is deterministic and pure
        # (no I/O, no mutation), so this is the same shift the center used — there is
        # still exactly ONE de-bias decision, recovered here for the receipt and the
        # debiased-member tuple. (The center module does not return the vector.)
        debiased_values, applied = self._debias_authority.apply(case, models)
        debiased_members_native = tuple(float(v) for v in np.asarray(debiased_values, dtype=float))

        member_min_native = float(center.debiased_member_min_native)
        member_max_native = float(center.debiased_member_max_native)

        # --- REFUSED center (no fresh members): not live-eligible ----------------
        if center.center_status == "REFUSED":
            day0_inactive = condition_day0(
                metric=case.metric,
                obs=obs if obs is not None else _no_obs(case),
                center_before_native=float(center.mu_native)
                if math.isfinite(center.mu_native)
                else 0.0,
            )
            sigma_components = _empty_sigma_components()
            family: DistributionFamily = "NORMAL"
            reason = f"CENTER_REFUSED: {center.reason}"
            identity_hash = _identity_hash(
                case,
                float(center.mu_native),
                0.0,
                debiased_members_native,
                family,
                center,
                applied,
                day0_inactive,
                sigma_components,
                False,
                reason,
            )
            return PredictiveDistribution(
                case=case,
                mu_native=float(center.mu_native),
                sigma_native=0.0,
                debiased_members_native=debiased_members_native,
                member_min_native=member_min_native,
                member_max_native=member_max_native,
                center=center,
                debias=applied,
                day0=day0_inactive,
                sigma_components=sigma_components,
                distribution_family=family,
                live_eligible=False,
                ineligibility_reason=reason,
                identity_hash=identity_hash,
            )

        # --- (2) day0 conditioning: the ONLY envelope-leaving license ------------
        day0 = condition_day0(
            metric=case.metric,
            obs=obs if obs is not None else _no_obs(case),
            center_before_native=float(center.mu_native),
        )

        # The served center is the support-corrected center. When day0 is inactive,
        # condition_day0 returns center_after_native == center_before_native, so
        # mu_native stays the envelope-enforced center (26 unreachable for 20-23
        # members). When day0 is active, mu_native may leave the envelope ONLY
        # toward the OBSERVED extreme (max(center, observed_high) for HIGH).
        mu_native = float(day0.center_after_native)

        # --- (3) sigma authority: the live-eligibility gate ----------------------
        sigma_decision: SigmaDecision = build_sigma(
            case,
            models,
            fused_center_sd_native=fused_center_sd_native,
            sigma_resid_native=sigma_resid_native,
            has_fusion_capture=has_fusion_capture,
        )
        sigma_components = sigma_decision.components

        family = _distribution_family(case.metric, day0)

        live_eligible = bool(sigma_decision.live_eligible)
        ineligibility_reason = sigma_decision.ineligibility_reason
        sigma_native = float(sigma_decision.sigma_native)

        # --- RAW PROVENANCE FAIL-CLOSED (single-serving-rule flow §7) ------------
        # Under the operator RAW no-de-bias law the served center MUST be RAW (zero
        # de-bias shift) — the spine injects _NoOpDebiasAuthority so debias_applied is
        # false and aggregate_shift_native == 0. If a live-eligible distribution ever
        # carries a NON-ZERO de-bias shift (a regression that re-wired a real
        # DebiasAuthority onto the spine, or a wrong center_method), REJECT it as
        # ineligible rather than serve a forbidden de-biased μ. This is the structural
        # antibody that makes a forbidden forward de-bias unconstructable on the live
        # decision path (it does not move μ — it refuses to serve one that was moved).
        # The day0-active path is exempt: day0 is the SEPARATE observed-extreme license,
        # not a de-bias, and its shift lives in day0.center_after_native, not debias.
        _debias_shift = float(getattr(applied, "aggregate_shift_native", 0.0) or 0.0)
        if live_eligible and abs(_debias_shift) > 1e-9:
            live_eligible = False
            ineligibility_reason = (
                f"RAW_LAW_VIOLATION_DEBIAS_SHIFT_NONZERO: debias_applied with "
                f"aggregate_shift_native={_debias_shift:+.6f} on a live-eligible "
                f"distribution (RAW law forbids a forward de-bias on the served center)"
            )
        _valid_center_methods = {
            "WEIGHTED_HUBER_CONSENSUS", "SHRUNK_EMOS", "RAW_FALLBACK",
        }
        if live_eligible and center.center_method not in _valid_center_methods:
            live_eligible = False
            ineligibility_reason = (
                f"RAW_LAW_VIOLATION_CENTER_METHOD: center_method="
                f"{center.center_method!r} is not a recognized RAW center method"
            )

        identity_hash = _identity_hash(
            case,
            mu_native,
            sigma_native,
            debiased_members_native,
            family,
            center,
            applied,
            day0,
            sigma_components,
            live_eligible,
            ineligibility_reason,
        )

        return PredictiveDistribution(
            case=case,
            mu_native=mu_native,
            sigma_native=sigma_native,
            debiased_members_native=debiased_members_native,
            member_min_native=member_min_native,
            member_max_native=member_max_native,
            center=center,
            debias=applied,
            day0=day0,
            sigma_components=sigma_components,
            distribution_family=family,
            live_eligible=live_eligible,
            ineligibility_reason=ineligibility_reason,
            identity_hash=identity_hash,
        )


def _no_obs(case: ForecastCase) -> Day0ObservationState:
    """An inactive (fail-closed) day0 observation state for a case with no obs.

    ``observed=False`` so ``condition_day0`` returns the ``NO_DAY0`` identity
    transform (center unchanged, envelope not left). Carries the case station id so
    a receipt can show which station the (absent) observation would have come from.
    """
    return Day0ObservationState(
        observed=False,
        station_id=case.station_id,
        source="none",
        samples_count=0,
        latest_observed_at_utc=None,
        observed_high_native=None,
        observed_low_native=None,
        observed_extreme_native=None,
        raw_observation_hash=None,
    )


def build_predictive_distribution(
    case: ForecastCase,
    models: FreshModelSet,
    debias_authority: DebiasAuthority,
    obs: Optional[Day0ObservationState] = None,
    *,
    use_emos: bool = True,
    fused_center_sd_native: Optional[float] = None,
    sigma_resid_native: Optional[float] = None,
    has_fusion_capture: bool = True,
) -> PredictiveDistribution:
    """Module-level convenience wrapper around ``PredictiveDistributionBuilder.build``.

    Constructs a one-shot builder with the supplied ``debias_authority`` and builds
    the predictive distribution. The reactor (at integration/Wave 5) holds a
    long-lived builder; this wrapper is for the test seam and ad-hoc callers.
    """
    return PredictiveDistributionBuilder(debias_authority).build(
        case,
        models,
        obs,
        use_emos=use_emos,
        fused_center_sd_native=fused_center_sd_native,
        sigma_resid_native=sigma_resid_native,
        has_fusion_capture=has_fusion_capture,
    )
