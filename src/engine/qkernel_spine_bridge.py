# Created: 2026-06-14
# Last audited: 2026-06-17
# Authority basis: operator single-truth law + residual_legacy_sources.md (GATE-0
#   bias-maze strip: _spine_debias_authority unconditional identity, settlement-residual
#   seam removed) + docs/rebuild/consult_build_spec.md (Wave 5 reactor wiring) +
#   docs/rebuild/impl_w4_family_decision_engine.md (the engine contract this bridge
#   drives) + docs/rebuild/arm_replay_report.md (the spine validated BEFORE this
#   integration) + docs/rebuild/impl_w5b_integration.md (this integration's report).
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md.
"""qkernel_spine_bridge — the Wave-5B cutover bridge from the live reactor to the
rebuilt q-kernel spine.

This module is the ONLY place the reactor's forecast-family decision is routed
through ``src/decision/family_decision_engine.FamilyDecisionEngine.decide()``.
When the ``qkernel_spine_enabled`` flag is ON, forecast families call
:func:`decide_family_via_spine` here. When it is OFF or unreadable, forecast
families no-trade with ``QKERNEL_SPINE_REQUIRED`` at the reactor seam; they do
not fall back to the old scalar selector.

WHAT IT DOES (the cutover contract):

  1. ``qkernel_spine_enabled()`` — reads the single boolean live-authority flag
     from ``settings["feature_flags"]["qkernel_spine_enabled"]``. A config read
     fault returns False; the reactor converts that to a typed no-trade, never to
     a live fallback selector.

  2. ``decide_family_via_spine(...)`` — builds the spine inputs from the
     reactor-native data already in scope at the ``_generate_candidate_proofs`` /
     ``_selected_candidate_proof`` orchestration seam (the family, the per-candidate
     ``_CandidateProof`` tuple already materialized for the submission pipeline, the
     threaded ``_edli_spine_*`` predictive center/dispersion/members, the existing
     family exposure), calls ``FamilyDecisionEngine.decide()`` (the rebuilt spine:
     predictive_distribution -> joint_q -> joint_q_band -> family_book ->
     market_coherence -> negrisk_routes -> payoff_vector -> filter[direction,
     coherence, edge_lcb>0 & delta_u>0] -> argmax optimal_delta_u), and maps the
     resulting ``FamilyDecision.selected`` back onto the matching ``_CandidateProof``
     so the reactor's submission pipeline (RiskGuard, freshness, MECE fail-closed,
     venue submission, receipts, the Stage-0 decision_receipt_spine) wraps it
     unchanged.

THE SUBMISSION PIPELINE IS NOT TOUCHED. This bridge replaces the DECISION
COMPUTATION (which q, which candidate, what size), not the submission machinery. The
reactor still owns RiskGuard, freshness/staleness gates, MECE fail-closed, venue
submission, receipt persistence. The honest pre-existing gates (direction law,
capital-efficiency q_lcb>price = the engine's ``edge_lcb>0``, real fee+tick,
settlement truth) STAY: the spine's own ``decide()`` filter chain (direction law +
coherence + edge_lcb>0 & optimal_delta_u>0) IS the capital-efficiency law, and the
selected proof still flows through the reactor's downstream submit-time re-proofs.

DRIFT RESOLVED (recorded per operator law — see impl_w5b_integration.md §"Input mapping"):

  * The belief authority runs at the seam. The spine assembles the ONE predictive
    distribution over the members threaded under ``_edli_spine_debiased_members_native``
    by the Stage-0 producer, and it preserves the reactor-served predictive σ carried
    on the same payload. POST-FIX REALITY (spine-source rewire 2026-06-16): those
    members are the RAW MULTI-MODEL member envelope sourced from ``raw_model_forecasts``
    (~7-13 decorrelated NWP providers, latest cycle per model) — the SAME source the
    ARM/settlement-EV replay validates (``fresh_members_at_cycle``). SINGLE TRUTH:
    there is NO settlement-residual de-bias layer — ``_spine_debias_authority`` is
    unconditionally ``_NoOpDebiasAuthority`` (ZERO shift, ``raw == debiased``), so the
    center this ships IS the raw precise multi-model fused center, untouched. The
    qkernel must not rebuild a different σ from the payload; entry, monitor, and
    qkernel score the same served distribution. If the producer stashed no members at
    all (the threaded inputs are absent — e.g. <3 fresh models on the causal cycle),
    the bridge returns a TYPED no-trade (``SPINE_INPUTS_UNAVAILABLE``) rather than
    fabricating a center.

  * The spine's ``family_book`` step consumes ``ExecutableMarketSnapshot`` per
    sibling keyed by bin_id. The reactor's per-family decision seam holds the
    executable snapshot ROWS (DB row dicts) and the per-candidate ``_CandidateProof``
    objects, not reconstructed ``ExecutableMarketSnapshot`` objects. Resolution: the
    bridge does NOT rebuild the family book from raw rows. The reactor's selection
    geometry (the ΔU ranker over ``NativeSideCandidate`` sizing objects + the
    ``FamilyPayoffMatrix`` over the family bins + the ``PortfolioExposureVector``) is
    the SAME ``utility_ranker`` geometry the spine's payoff layer maximizes over, and
    the per-candidate executable cost curves are already materialized on the proofs.
    The bridge therefore drives ``decide()`` with the reactor-native sizing
    candidates and exposure (the real spine types) and lets the spine own the
    direction-law + coherence + edge + argmax-ΔU selection over them. The market book
    used for coherence is assembled from the per-candidate proofs' executable prices
    (the de-frictioned market q the coherence module needs), recorded in the report
    as the resolved family-book input.

EVERY PATH RETURNS A TYPED OUTCOME. A trade selects a proof; a no-trade carries a
typed ``no_trade_reason`` (the spine's own vocabulary or ``SPINE_INPUTS_UNAVAILABLE``
when a required input genuinely cannot be reconstructed). The bridge never raises a
bare exception into the reactor's hot path — a wiring fault is caught and returned as
a typed ``SPINE_WIRING_FAULT`` no-trade so the reactor emits a deterministic receipt.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from src.decision.family_decision_engine import (
    FamilyDecision,
    FamilyDecisionEngine,
    FamilyDecisionError,
)
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.debias_authority import AppliedDebias
from src.forecast.forecast_case_factory import forecast_case_metadata
from src.forecast.predictive_distribution_builder import (
    PredictiveDistribution,
    PredictiveDistributionBuilder,
    _identity_hash,
)
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import (
    ResolutionError,
    event_resolution_for_city,
)
from src.probability.joint_q import JointQ
from src.probability.joint_q_band import JointQBand
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    OutcomeSpaceError,
    compute_topology_hash,
)
from src.strategy import utility_ranker

# ---------------------------------------------------------------------------
# Typed no-trade / fault reasons unique to the bridge (the spine owns the rest).
# ---------------------------------------------------------------------------
NO_TRADE_SPINE_INPUTS_UNAVAILABLE = "SPINE_INPUTS_UNAVAILABLE"
NO_TRADE_SPINE_WIRING_FAULT = "SPINE_WIRING_FAULT"
NO_TRADE_SPINE_NO_SELECTION = "SPINE_NO_SELECTION"
# Route identity (consult_review_pr409.md §5 BLOCKER "integration-route identity"):
# the unchanged submit path executes ONE native leg, so the bridge may only carry a
# DIRECT native route (DIRECT_YES / DIRECT_NO) back to a single _CandidateProof. A
# synthetic / arb / conversion route is multi-leg and the submit path cannot execute
# it — the minimum-safe realization restricts the engine to direct routes and REFUSES
# (never silently single-leg-maps) any non-direct selection as this typed no-trade.
NO_TRADE_ROUTE_NOT_DIRECTLY_EXECUTABLE = "NO_TRADE_ROUTE_NOT_DIRECTLY_EXECUTABLE"
# v1 lead-bucket restriction (consult_review_pr409_round2.md §3): only the 24h lead
# bucket has its own settlement-EV replay, so live qkernel is restricted to it. A case
# outside the replayed bucket is a typed no-trade until that bucket is EV-replayed.
NO_TRADE_QKERNEL_LEAD_BUCKET_NOT_REPLAYED = "QKERNEL_LEAD_BUCKET_NOT_REPLAYED"
_LEGACY_ROUNDED_MU_DIRECTION_REJECTION_PREFIX = "DIRECTION_LAW_BIN_FORECAST_MISMATCH"

# The route_id prefixes a DIRECT native route carries (negrisk_routes._direct_*_route:
# route_id = f"{route_type}:{bin_id}@{shares}"). These are the ONLY route types one
# native _CandidateProof can execute via the unchanged single-leg submit path.
_DIRECT_ROUTE_ID_PREFIXES = ("DIRECT_YES:", "DIRECT_NO:")

# The joint-q band draw count the engine uses for the coherent ΔU band. The engine's
# own default is 4000 (the validated production width). ``None`` means "use the engine
# default"; a test may set this to a smaller value to keep the smoke fast. This is the
# ONLY tunable that affects the band width; it never changes the selection LOGIC, only
# the Monte-Carlo resolution of the robust edge lower bound.
SPINE_BAND_DRAWS: Optional[int] = None

# The qkernel's per-candidate band tail must share the live family-error budget.
# If this stays hard-coded at 5% while the live submit gate runs BH/FDR at a
# different q, the spine can discard point-positive candidates before FDR sees
# them. Keep the fallback conservative for config faults, but use edge.fdr_alpha
# on the live path.
_DEFAULT_SPINE_BAND_ALPHA: float = 0.05

# SINGLE TRUTH (legacy bias-maze strip 2026-06-17): the spine center IS the raw precise
# multi-model fused center from ``raw_model_forecasts`` (~7-13 decorrelated NWP providers,
# latest cycle per model). There is NO settlement-residual de-bias layer and NO bias flag.
# ``_spine_debias_authority`` is UNCONDITIONALLY the identity ``_NoOpDebiasAuthority``:
# ``build_center`` / ``build_sigma`` run on the RAW fused members with ZERO shift, so
# ``raw == debiased``. (The removed seam read ``ZEUS_SPINE_SETTLE_RESID_DEBIAS`` and the
# deleted ``settlement_residual_debias`` provider; both gone under the single-truth law.)


def _spine_debias_authority(case: ForecastCase):  # noqa: ARG001 — identity contract
    """The identity de-bias authority the live spine builder uses (single truth).

    ALWAYS ``_NoOpDebiasAuthority`` — zero shift, ``raw == debiased``. The spine ships
    the raw precise multi-model fused center untouched; there is no statistical de-bias
    correction layer (operator single-truth law).
    """
    return _NoOpDebiasAuthority()


# ===========================================================================
# (1) The single cutover flag accessor.
# ===========================================================================

def qkernel_spine_enabled() -> bool:
    """The single forecast qkernel live-authority flag.

    Read from ``settings["feature_flags"]["qkernel_spine_enabled"]`` using the SAME
    accessor the other reactor feature flags use (e.g. the replacement-authority
    flag reads ``settings["feature_flags"][...]``). A config read fault returns
    False; forecast families then emit ``QKERNEL_SPINE_REQUIRED`` at the reactor
    seam. Disabling the flag is not a license to route live forecast money through
    a fallback selector.
    """
    try:
        from src.config import settings

        return bool(settings["feature_flags"].get("qkernel_spine_enabled", False))
    except Exception:  # noqa: BLE001 — reactor turns False into QKERNEL_SPINE_REQUIRED.
        return False


def _qkernel_spine_band_alpha() -> float:
    """Tail probability used for qkernel edge/DeltaU bands.

    The selected proof's false-edge p-value is later consumed by the existing
    family BH/FDR gate. Using the same configured q here prevents a stricter
    hidden pre-FDR filter from making the family FDR surface unreachable.
    """

    try:
        from src.config import settings

        value = float(settings["edge"]["fdr_alpha"])
    except (KeyError, TypeError, ValueError):  # fail-safe to the historical conservative tail
        return _DEFAULT_SPINE_BAND_ALPHA
    if not (0.0 < value < 0.5):
        return _DEFAULT_SPINE_BAND_ALPHA
    return value


# ===========================================================================
# The bridge result — a selected proof OR a typed no-trade, plus the FamilyDecision.
# ===========================================================================

@dataclass(frozen=True)
class SpineDecisionResult:
    """The outcome of routing one family's decision through the rebuilt spine.

    * ``selected_proof`` — the reactor ``_CandidateProof`` the spine selected (its
      q/q_lcb/trade_score overlaid with the spine's economics), or ``None`` for a
      no-trade. This is the SAME object type the reactor's submission pipeline
      already consumes, so RiskGuard / freshness / venue_command / receipts wrap it
      unchanged.
    * ``no_trade_reason`` — ``None`` when a trade was selected; otherwise a typed
      reason (the spine's own ``no_trade_reason`` or a bridge fault reason).
    * ``decision`` — the full ``FamilyDecision`` (``None`` only when the spine could
      not be driven at all — ``SPINE_INPUTS_UNAVAILABLE`` / ``SPINE_WIRING_FAULT``).
      Carries the spine receipt_hash for the decision receipt.
    * ``decided_by_spine`` — always True when this object is produced (the bridge is
      ONLY reached when the flag is ON); the reactor uses it to assert the decision
      authority on the receipt.
    """

    selected_proof: Optional[Any]
    no_trade_reason: Optional[str]
    decision: Optional[FamilyDecision]
    decided_by_spine: bool = True


@dataclass(frozen=True)
class _OverlayResult:
    proof: Any | None
    reason: str | None = None


# ===========================================================================
# (2) The reactor -> spine input mapping (built from data in scope at the seam).
# ===========================================================================

def _coerce_target_date(value: Any) -> date:
    """Parse a reactor family ``target_date`` (a YYYY-MM-DD string) into a date."""
    if isinstance(value, date):
        return value
    text = str(value)
    return date.fromisoformat(text[:10])


def _bin_unit(family: Any) -> str:
    """The measurement unit carried on the family's bins ('C' or 'F')."""
    for candidate in getattr(family, "candidates", ()) or ():
        unit = getattr(getattr(candidate, "bin", None), "unit", None)
        if unit in ("C", "F"):
            return unit
    # Fail-closed default: the resolution carries the real unit; bins are validated
    # against it downstream, so a wrong guess raises rather than silently miscomputes.
    return "C"


def _city_resolver(family: Any):
    """Resolve the runtime City object for the family (for the EventResolution).

    Uses the live ``runtime_cities_by_name`` registry the reactor already imports.
    Returns the City object or ``None`` (the caller turns ``None`` into a typed
    no-trade — never fabricates a settlement station).
    """
    try:
        from src.config import runtime_cities_by_name

        cities = runtime_cities_by_name()
        return cities.get(str(getattr(family, "city", "")))
    except Exception:  # noqa: BLE001
        return None


def _candidate_bin_id_for(candidate: Any) -> str:
    """The stable bin_id for one reactor ``MarketTopologyCandidate``.

    This MUST be byte-identical to the reactor's ``_candidate_bin_id(proof)`` (which
    hashes the proof's candidate condition_id + bin geometry), because that same hash
    keys the sizing candidates and the route set the spine sizes/selects over. The
    Omega bin_id, the sizing-candidate (bin_id, side) key, the family-book market key,
    and the route key are then ALL the same id, so the spine's selected
    ``candidate_id`` (``SIDE:bin_id:route_id``) maps back to the reactor proof. The
    reactor uses ``stable_hash`` over exactly these fields — replicated here so the
    Omega built from ``family.candidates`` lines up with the proofs.
    """
    from src.decision_kernel.canonicalization import stable_hash

    bin_obj = getattr(candidate, "bin", None)
    return stable_hash(
        {
            "condition_id": str(getattr(candidate, "condition_id", "") or ""),
            "bin_low": getattr(bin_obj, "low", None),
            "bin_high": getattr(bin_obj, "high", None),
            "bin_unit": getattr(bin_obj, "unit", None),
            "bin_label": getattr(bin_obj, "label", None),
        }
    )


def build_forecast_case(
    family: Any,
    *,
    source_cycle_time_utc: datetime,
) -> ForecastCase:
    """Build the spine ``ForecastCase`` from the reactor family + forecast source cycle.

    Resolves the versioned ``EventResolution`` via the live
    ``event_resolution_for_city`` (the SAME per-city settlement identity the q layer
    threads). Raises ``ResolutionError`` (fail-closed) if the city cannot be resolved
    to a settlement station — the caller turns that into a typed no-trade.

    The case ``issue_time_utc`` / ``source_cycle_time_utc`` are the FORECAST SOURCE
    CYCLE that produced the served members (NOT decision_time), and season / lead /
    regime are derived by the SINGLE ``forecast_case_metadata`` factory the ARM replay
    also uses, so the settlement sigma-floor cell identity is the replay-validated one
    (consult_review_pr409_round2.md §3). ``season = emos_season(target)`` (the floor
    table's own key — never blank); ``regime_key = "default"`` (the replay's);
    ``lead_hours`` is the real lead from the source cycle to the target finalization.
    """
    city = _city_resolver(family)
    if city is None:
        raise ResolutionError(
            f"CITY_UNRESOLVED: {getattr(family, 'city', None)!r} not in runtime registry"
        )
    metric = str(getattr(family, "metric", "")).lower()
    if metric not in ("high", "low"):
        raise ResolutionError(f"METRIC_INVALID: {metric!r}")
    target_local_date = _coerce_target_date(getattr(family, "target_date", None))
    resolution = event_resolution_for_city(city, target_local_date, metric)  # type: ignore[arg-type]

    cycle = (
        source_cycle_time_utc
        if source_cycle_time_utc.tzinfo
        else source_cycle_time_utc.replace(tzinfo=timezone.utc)
    )
    meta = forecast_case_metadata(
        target_local_date=target_local_date,
        source_cycle_time_utc=cycle,
        finalization_local_time=resolution.finalization_local_time,
        settlement_timezone=resolution.settlement_timezone,
    )
    return ForecastCase(
        city=resolution.city,
        city_id=str(getattr(city, "name", resolution.city)),
        station_id=resolution.station_id,
        settlement_source_type=resolution.settlement_source_type,
        target_local_date=target_local_date,
        metric=metric,  # type: ignore[arg-type]
        issue_time_utc=cycle,
        lead_hours=meta.lead_hours,
        season=meta.season,
        regime_key=meta.regime_key,
        unit=resolution.measurement_unit,
        resolution=resolution,
        family_id=str(getattr(family, "family_id", "")),
        source_cycle_time_utc=cycle,
    )


def build_outcome_space(family: Any, case: ForecastCase) -> OutcomeSpace:
    """Build the complete MECE ``OutcomeSpace`` (Omega) from the reactor family bins.

    The reactor family's bins are ALREADY a validated MECE partition (the
    candidate-binding layer built them via ``validate_bin_topology``). This maps each
    reactor ``Bin`` to an ``OutcomeBin`` carrying the family resolution's rounding
    rule, then validates the assembled Omega (fail-closed on any incompleteness).
    """
    resolution = case.resolution
    bins: list[OutcomeBin] = []
    for candidate in getattr(family, "candidates", ()) or ():
        bin_obj = getattr(candidate, "bin", None)
        if bin_obj is None:
            continue
        bin_id = _candidate_bin_id_for(candidate)
        bins.append(
            OutcomeBin(
                bin_id=bin_id,
                condition_id=str(getattr(candidate, "condition_id", "") or ""),
                label=str(getattr(bin_obj, "label", "") or bin_id),
                lower_native=getattr(bin_obj, "low", None),
                upper_native=getattr(bin_obj, "high", None),
                yes_token_id=str(getattr(candidate, "yes_token_id", "") or "") or None,
                no_token_id=str(getattr(candidate, "no_token_id", "") or "") or None,
                executable=True,
                rounding_rule=resolution.rounding_rule,
            )
        )
    bins_tuple = tuple(bins)
    omega = OutcomeSpace(
        family_id=case.family_id,
        resolution=resolution,
        bins=bins_tuple,
        topology_hash=compute_topology_hash(case.family_id, resolution, bins_tuple),
    )
    omega.validate()  # fail-closed: incomplete/overlapping family raises here
    return omega


def _served_joint_belief_from_proofs(
    *,
    omega: OutcomeSpace,
    proofs: Sequence[Any],
    candidate_bin_id,
    alpha: float,
) -> tuple[JointQ, JointQBand, dict[tuple[str, str], float], None] | tuple[None, None, None, str]:
    """Rehydrate the reactor-served posterior q for the qkernel selector.

    The bridge must not let qkernel rebuild a second point probability from the
    raw member envelope while admission/execution use the already-served
    replacement posterior.  This helper builds a ``JointQ`` directly from the
    selected family's proof probabilities and a candidate-side q_lcb map from
    those same proofs, so route economics and submit certificates consume one
    live belief surface.
    """

    yes_q_by_bin: dict[str, float] = {}
    payoff_lcb_by_side: dict[tuple[str, str], float] = {}

    for proof in proofs:
        side = _proof_side(proof)
        if side not in {"YES", "NO"}:
            continue
        try:
            bin_id = str(candidate_bin_id(proof))
            q_point = float(getattr(proof, "q_posterior"))
            q_lcb = float(getattr(proof, "q_lcb_5pct"))
        except (TypeError, ValueError):
            return None, None, None, "SERVED_BELIEF_PROOF_Q_UNPARSEABLE"
        if not (
            bin_id
            and math.isfinite(q_point)
            and math.isfinite(q_lcb)
            and 0.0 <= q_lcb <= q_point <= 1.0
        ):
            return None, None, None, "SERVED_BELIEF_PROOF_Q_INVALID"
        if side == "YES":
            yes_q_by_bin[bin_id] = q_point
            payoff_lcb_by_side[(bin_id, "YES")] = q_lcb
        else:
            yes_q_by_bin.setdefault(bin_id, float(1.0 - q_point))
            payoff_lcb_by_side[(bin_id, "NO")] = q_lcb

    q_values: list[float] = []
    for b in omega.bins:
        if b.bin_id not in yes_q_by_bin:
            return None, None, None, f"SERVED_BELIEF_Q_MISSING:{b.bin_id}"
        q = float(yes_q_by_bin[b.bin_id])
        if not (math.isfinite(q) and 0.0 <= q <= 1.0):
            return None, None, None, f"SERVED_BELIEF_Q_INVALID:{b.bin_id}"
        q_values.append(q)

    q_arr = np.asarray(q_values, dtype=float)
    total = float(q_arr.sum())
    if not (math.isfinite(total) and abs(total - 1.0) <= 1e-6):
        return None, None, None, f"SERVED_BELIEF_Q_NOT_SIMPLEX:sum={total:.12f}"
    if abs(total - 1.0) > 1e-12:
        q_arr = q_arr / total

    # Every executable route the proof-native route builder can enumerate must
    # have its own side lower bound.  Missing side-q_lcb would make the selector
    # fall back to a synthetic band bound and recreate the split-belief defect.
    for proof in proofs:
        side = _proof_side(proof)
        if side not in {"YES", "NO"}:
            continue
        try:
            key = (str(candidate_bin_id(proof)), side)
        except Exception:  # noqa: BLE001
            return None, None, None, "SERVED_BELIEF_LCB_KEY_UNRESOLVABLE"
        if key not in payoff_lcb_by_side:
            return None, None, None, f"SERVED_BELIEF_LCB_MISSING:{key[0]}:{key[1]}"

    h = hashlib.sha256()
    h.update(b"REACTOR_SERVED_POSTERIOR_JOINT_Q_V1")
    h.update(omega.topology_hash.encode("utf-8"))
    h.update(omega.resolution.rounding_rule.encode("utf-8"))
    for b, q in zip(omega.bins, q_arr):
        h.update(f"|{b.bin_id}={float(q):.12f}".encode("utf-8"))
    identity_hash = h.hexdigest()
    q_by_bin_id = {b.bin_id: float(q) for b, q in zip(omega.bins, q_arr)}
    joint_q = JointQ(
        omega=omega,
        q=q_arr,
        q_by_bin_id=q_by_bin_id,
        predictive_distribution_id="reactor_served_posterior",
        q_source="REACTOR_SERVED_POSTERIOR_V1",  # type: ignore[arg-type]
        q_sum=float(q_arr.sum()),
        identity_hash=identity_hash,
    )
    joint_q.assert_valid()

    # Candidate economics receive the side-specific served q_lcb through
    # ``guarded_payoff_q_lcb``.  The band still has to be a valid simplex draw
    # matrix for coherence/receipt/sizing plumbing; a deterministic one-row band
    # is honest here because this bridge is not inventing a new uncertainty
    # surface.  Any q_lcb authority comes from ``payoff_lcb_by_side`` above.
    samples = np.asarray([q_arr], dtype=float)
    bh = hashlib.sha256()
    bh.update(b"REACTOR_SERVED_POSTERIOR_DETERMINISTIC_BAND_V1")
    bh.update(identity_hash.encode("utf-8"))
    bh.update(f"alpha={float(alpha):.12f}".encode("utf-8"))
    band = JointQBand(
        joint_q=joint_q,
        samples=samples,
        q_lcb=q_arr.copy(),
        q_ucb=q_arr.copy(),
        alpha=float(alpha),
        basis="PARAMETER_POSTERIOR_SIMPLEX_V1",
        sample_hash=bh.hexdigest(),
    )
    band.assert_valid()
    return joint_q, band, payoff_lcb_by_side, None


def _served_predictive_inputs(payload: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Lift the reactor's served predictive center/dispersion/members from the payload.

    The Stage-0 producer stashed ``_edli_spine_mu_native`` / ``_edli_spine_sigma_native``
    / ``_edli_spine_debiased_members_native`` (and the raw members / q vector) on the
    THREADED payload at the single point where they were all in scope. Source-corrected
    2026-06-16: the live producer sources the member envelope from ``raw_model_forecasts``
    (the multi-model deterministic fusion, NOT ``ensemble_snapshots``). These members are
    the RAW multi-model envelope — NOT chain-of-record-debiased and NOT lifted from an
    "ARM-validated" run; the ARM replay reads the SAME ``raw_model_forecasts`` source, but
    the center/width here are recomputed live by ``build_center`` / ``build_sigma`` over
    that envelope (the q the spine integrates is built over the SAME N(mu*, sigma) the
    bridge constructs). Returns ``None`` when the served predictive inputs are genuinely
    absent (the caller emits a typed no-trade rather than fabricating a center).
    """
    mu = payload.get("_edli_spine_mu_native")
    sigma = payload.get("_edli_spine_sigma_native")
    debiased = payload.get("_edli_spine_debiased_members_native")
    if mu is None or sigma is None:
        return None
    try:
        mu_f = float(mu)
        sigma_f = float(sigma)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(mu_f) and np.isfinite(sigma_f) and sigma_f > 0.0):
        return None
    members = None
    if debiased is not None:
        try:
            arr = np.asarray(debiased, dtype=float).ravel()
            if arr.size and np.isfinite(arr).all():
                members = tuple(float(x) for x in arr.tolist())
        except (TypeError, ValueError):
            members = None
    raw = payload.get("_edli_spine_raw_members_native")
    raw_members = None
    if raw is not None:
        try:
            rarr = np.asarray(raw, dtype=float).ravel()
            if rarr.size and np.isfinite(rarr).all():
                raw_members = tuple(float(x) for x in rarr.tolist())
        except (TypeError, ValueError):
            raw_members = None
    # Belief requires fresh members: the VALIDATED build_center runs on the member
    # envelope, NOT on the served mu. If NEITHER a debiased nor a raw member array was
    # threaded, the seam has no fresh consensus to lock the center to — return None so
    # the caller emits a typed SPINE_INPUTS_UNAVAILABLE no-trade rather than letting
    # build_fresh_model_set synthesize a 1-point envelope from the legacy served mu
    # (which would put the legacy mu back on the live path). The Stage-0 producer threads
    # members alongside mu, so this is unreachable on the live lane; it closes the one
    # latent legacy-mu seam.
    if members is None and raw_members is None:
        return None
    # The FORECAST SOURCE CYCLE that produced these members (the Stage-0 producer
    # stashes it under _edli_spine_source_cycle_time_utc). The ForecastCase issue /
    # source_cycle / lead MUST derive from this cycle, NOT decision_time, so the live
    # σ-floor lead bucket matches the replay-validated cell (round-2 §3). FAIL CLOSED
    # (return None ⇒ typed SPINE_INPUTS_UNAVAILABLE) when absent — never silently fall
    # back to decision_time, which would mis-bucket the lead and serve the wrong floor.
    source_cycle = _parse_source_cycle_time(payload.get("_edli_spine_source_cycle_time_utc"))
    if source_cycle is None:
        return None
    # RAW PRECISION (single-serving-rule §1-§2): the per-member raw second moment Ê[(x−Y)²]
    # + walk-forward n, threaded by the Stage-0 producer in the SAME index order as the
    # member arrays. Lifted here so build_fresh_model_set can attach it to each
    # RawModelMember and the spine's walk_forward_model_weights forms the RAW diagonal
    # 1/E[r²] weight. Absent (None) ⇒ equal-weight (the dormant-seam behavior, unchanged).
    raw_m2_by_index = _coerce_optional_float_list(payload.get("_edli_spine_raw_m2_by_index"))
    n_by_index = _coerce_int_list(payload.get("_edli_spine_n_by_index"))
    # Option C (2026-06-21): grid-representativeness variance sigma_repr² (native²),
    # threaded index-aligned by the producer (the model name is lost downstream). Lifted
    # so build_fresh_model_set can attach it to RawModelMember.representativeness_m2_native
    # and walk_forward_model_weights adds it AFTER the residual floor (Form A). Absent ⇒
    # all-zero ⇒ byte-identical (no geometry penalty).
    repr_m2_by_index = _coerce_optional_float_list(payload.get("_edli_spine_repr_m2_by_index"))
    return {
        "mu_native": mu_f,
        "sigma_native": sigma_f,
        "debiased_members_native": members,
        "raw_members_native": raw_members,
        "source_cycle_time_utc": source_cycle,
        "raw_m2_by_index": raw_m2_by_index,
        "n_by_index": n_by_index,
        "repr_m2_by_index": repr_m2_by_index,
    }


def _coerce_optional_float_list(value: Any) -> Optional[tuple[Optional[float], ...]]:
    """Coerce a threaded list of (float|None) raw second moments. None on any fault."""
    if value is None:
        return None
    try:
        out: list[Optional[float]] = []
        for v in value:
            if v is None:
                out.append(None)
                continue
            fv = float(v)
            out.append(fv if np.isfinite(fv) and fv > 0.0 else None)
        return tuple(out)
    except (TypeError, ValueError):
        return None


def _coerce_int_list(value: Any) -> Optional[tuple[int, ...]]:
    """Coerce a threaded list of walk-forward counts. None on any fault."""
    if value is None:
        return None
    try:
        return tuple(int(v) for v in value)
    except (TypeError, ValueError):
        return None


def _parse_source_cycle_time(value: Any) -> Optional[datetime]:
    """Parse the threaded forecast source-cycle timestamp into a tz-aware UTC datetime.

    The producer stashes a string (the snapshot ``source_cycle_time`` / ``issue_time``).
    Returns ``None`` (⇒ the caller fails closed to SPINE_INPUTS_UNAVAILABLE) when the
    value is absent or unparseable — the source cycle is REQUIRED for the lead bucket.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _spine_inputs_missing_reason(payload: Mapping[str, Any]) -> str:
    """Sub-type WHY ``_served_predictive_inputs`` failed, so a live SPINE_INPUTS_UNAVAILABLE
    names the exact gap (the Stage-0 producer threads these onto the payload; a missing key
    means the producer did not run for this family, mutated a different payload object, or
    that branch computed no value). Non-gating telemetry — never alters a decision."""
    mu = payload.get("_edli_spine_mu_native")
    sigma = payload.get("_edli_spine_sigma_native")
    if mu is None or sigma is None:
        return "MU_SIGMA_NOT_STASHED"
    try:
        if not (np.isfinite(float(mu)) and np.isfinite(float(sigma)) and float(sigma) > 0.0):
            return "MU_SIGMA_NONFINITE"
    except (TypeError, ValueError):
        return "MU_SIGMA_UNPARSEABLE"
    if payload.get("_edli_spine_debiased_members_native") is None and (
        payload.get("_edli_spine_raw_members_native") is None
    ):
        return "MEMBERS_NOT_STASHED"
    if _parse_source_cycle_time(payload.get("_edli_spine_source_cycle_time_utc")) is None:
        return "SOURCE_CYCLE_NOT_STASHED"
    return "UNKNOWN"


def build_fresh_model_set(
    case: ForecastCase, served: Mapping[str, Any]
) -> FreshModelSet:
    """Build a ``FreshModelSet`` from the served RAW MULTI-MODEL member envelope.

    POST-FIX REALITY (spine-source rewire 2026-06-16): the values are the producer's
    ``debiased_members_native``, which since the source rewire is the RAW multi-model
    envelope from ``raw_model_forecasts`` (~7-13 decorrelated NWP providers, latest cycle
    per model, °C→native) — the SAME member set the ARM/settlement-EV replay validates
    (``fresh_members_at_cycle``). SINGLE TRUTH: there is no de-bias layer, so
    ``raw == debiased`` (the producer stashes the raw envelope into BOTH keys). The
    ``build_center`` (envelope-lock) and ``build_sigma`` (realized-floor) authorities run
    on these RAW members with ZERO shift (``_NoOpDebiasAuthority``). Falls back to the raw
    array, then to ``mu_native``, only if no member array was threaded.
    """
    values = served.get("debiased_members_native") or served.get("raw_members_native") or ()
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        arr = np.asarray([float(served["mu_native"])], dtype=float)
    # RAW PRECISION (single-serving-rule §1-§2): the per-member raw second moment Ê[(x−Y)²]
    # + walk-forward n, lifted by _served_predictive_inputs in the SAME index order as
    # the member array. Attached to each RawModelMember so build_center's
    # walk_forward_model_weights forms the RAW diagonal 1/E[r²] weight. When absent /
    # length-mismatched, every member carries raw_m2=None ⇒ equal-weight (unchanged).
    _raw_m2 = served.get("raw_m2_by_index")
    _n_by = served.get("n_by_index")
    _have_precision = (
        isinstance(_raw_m2, (tuple, list))
        and len(_raw_m2) == int(arr.size)
        and isinstance(_n_by, (tuple, list))
        and len(_n_by) == int(arr.size)
    )
    # Option C: index-aligned grid-representativeness variance (native²). Independent of
    # _have_precision — a member can carry repr even with no raw m2 history (cold-start).
    _repr_by = served.get("repr_m2_by_index")
    _have_repr = isinstance(_repr_by, (tuple, list)) and len(_repr_by) == int(arr.size)

    def _member_m2(i: int) -> float | None:
        if not _have_precision:
            return None
        return _raw_m2[i]

    def _member_n(i: int) -> int:
        if not _have_precision:
            return 0
        try:
            return int(_n_by[i])
        except (TypeError, ValueError):
            return 0

    def _member_repr(i: int) -> float:
        if not _have_repr:
            return 0.0
        try:
            r = float(_repr_by[i])
        except (TypeError, ValueError):
            return 0.0
        return r if (r == r and r > 0.0) else 0.0  # NaN-safe; non-positive → 0.0

    members = tuple(
        RawModelMember(
            model_id=f"reactor_served_{i}",
            product_id="reactor_served",
            source_run_id="reactor_served",
            source_cycle_time_utc=case.source_cycle_time_utc,
            available_at_utc=case.issue_time_utc,
            value_native=float(v),
            station_mapping_id=case.station_id,
            raw_forecast_artifact_id="reactor_served",
            data_version="reactor_served",
            walk_forward_raw_m2_native=_member_m2(i),
            walk_forward_n=_member_n(i),
            representativeness_m2_native=_member_repr(i),
        )
        for i, v in enumerate(arr.tolist())
    )
    h = hashlib.sha256()
    h.update(case.family_id.encode("utf-8"))
    for v in arr.tolist():
        h.update(f"|{float(v)!r}".encode("utf-8"))
    return FreshModelSet(
        case=case,
        members=members,
        member_values_native=arr,
        min_native=float(np.min(arr)),
        max_native=float(np.max(arr)),
        model_set_hash=h.hexdigest(),
    )


class _NoOpDebiasAuthority:
    """De-bias is the IDENTITY at this live seam — no shift is ever applied (single truth).

    The members threaded here are the RAW precise multi-model envelope from
    ``raw_model_forecasts`` (NOT a chain-of-record-debiased set). Under the operator
    single-truth law there is NO settlement-residual de-bias layer, so this authority
    applies ZERO shift and ``raw == debiased``. The ``build_center`` (envelope-lock) and
    ``build_sigma`` (realized-floor) authorities run on these RAW members untouched — the
    raw precise multi-model fused center IS what the spine ships.
    """

    def apply(self, case: ForecastCase, models: FreshModelSet):
        vals = np.asarray(models.member_values_native, dtype=float)
        n = int(vals.size)
        applied = AppliedDebias(
            artifact_ids=(),
            per_member_shift_native=tuple(0.0 for _ in range(n)),
            aggregate_shift_native=0.0,
            trailing_residual_mean_native=0.0,
            trailing_residual_std_native=0.0,
            activation_status="NO_ARTIFACT",
            reason="reactor_chain_of_record_debias_upstream_no_seam_reshift",
        )
        return vals, applied


class _ReactorServedFreshModelReader:
    """A ``FreshModelReader`` that serves the reactor's pre-built ``FreshModelSet``."""

    def __init__(self, models: FreshModelSet) -> None:
        self._models = models

    def read(self, case: ForecastCase) -> FreshModelSet:  # noqa: ARG002
        return self._models


class _ReactorServedPredictiveBuilder:
    """Build the spine predictive distribution while preserving reactor-served σ.

    The bridge payload already carries the live materializer's served predictive
    width. The generic builder is still the owner of center assembly, day0 support,
    raw-law validation, and the receipt shape, but this seam must not recompute a
    second sigma and feed q a different width than the live entry/monitor belief.
    """

    def __init__(self, debias_authority, *, served_sigma_native: float) -> None:
        self._delegate = PredictiveDistributionBuilder(debias_authority)
        self._served_sigma_native = float(served_sigma_native)

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
        base = self._delegate.build(
            case,
            models,
            obs,
            use_emos=use_emos,
            fused_center_sd_native=fused_center_sd_native,
            sigma_resid_native=sigma_resid_native,
            has_fusion_capture=has_fusion_capture,
        )
        return self._with_served_sigma(base)

    def _with_served_sigma(
        self, base: PredictiveDistribution
    ) -> PredictiveDistribution:
        sigma = self._served_sigma_native
        if not (np.isfinite(sigma) and sigma > 0.0):
            return replace(
                base,
                live_eligible=False,
                ineligibility_reason="REACTOR_SERVED_SIGMA_INVALID",
            )

        # Keep genuine non-sigma refusals closed. A missing local sigma authority is
        # exactly what the reactor-served sigma resolves; center/raw-law refusals are not.
        reason = str(base.ineligibility_reason or "")
        sigma_only_refusal = reason.startswith("PREDICTIVE_SIGMA_AUTHORITY_MISSING")
        if not base.live_eligible and not sigma_only_refusal:
            return base

        components = replace(
            base.sigma_components,
            sigma_before_floor_native=sigma,
            sigma_after_floor_native=sigma,
            artifact_id="reactor_served_predictive_sigma_payload",
        )
        identity_hash = _identity_hash(
            base.case,
            base.mu_native,
            sigma,
            base.debiased_members_native,
            base.distribution_family,
            base.center,
            base.debias,
            base.day0,
            components,
            True,
            None,
        )
        return replace(
            base,
            sigma_native=sigma,
            sigma_components=components,
            live_eligible=True,
            ineligibility_reason=None,
            identity_hash=identity_hash,
        )


def _payload_float(payload: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(parsed):
            return parsed
    return None


def _payload_datetime_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class _PayloadDay0Reader:
    """Read a live Day0 observed extreme from the same event payload the reactor prices.

    Day0 is not a separate order type here. It is the same family decision with one
    extra fact: a live-authorized observed running extreme that conditions the
    predictive identity. The selected probabilities still come from the reactor's
    served Day0 q/q_lcb proof surface passed into ``served_joint_q``.
    """

    def __init__(self, payload: Mapping[str, Any], family: Any, *, enabled: bool) -> None:
        self._state: Optional[Day0ObservationState] = None
        self._block_reason: Optional[str] = None
        if not enabled:
            return
        try:
            from src.events.day0_authority import assert_live_day0_payload_authority

            assert_live_day0_payload_authority(payload)
        except Exception as exc:  # noqa: BLE001 - typed into a no-trade reason by caller.
            self._block_reason = f"DAY0_OBSERVATION_AUTHORITY_REQUIRED:{exc}"
            return

        metric = str(payload.get("metric") or payload.get("temperature_metric") or getattr(family, "metric", "") or "")
        if metric == "high":
            observed_extreme = _payload_float(payload, "high_so_far", "raw_value", "rounded_value")
            observed_high = observed_extreme
            observed_low = _payload_float(payload, "low_so_far")
        elif metric == "low":
            observed_extreme = _payload_float(payload, "low_so_far", "raw_value", "rounded_value")
            observed_high = _payload_float(payload, "high_so_far")
            observed_low = observed_extreme
        else:
            self._block_reason = f"DAY0_OBSERVATION_METRIC_UNSUPPORTED:{metric or 'missing'}"
            return
        if observed_extreme is None:
            self._block_reason = "DAY0_OBSERVED_EXTREME_MISSING"
            return

        samples = payload.get("samples_count", payload.get("sample_count", 1))
        try:
            samples_count = max(1, int(samples))
        except (TypeError, ValueError):
            samples_count = 1
        observation_id = str(
            payload.get("observation_context_id")
            or payload.get("payload_hash")
            or payload.get("event_id")
            or ""
        )
        raw_hash = hashlib.sha256(
            (
                f"{payload.get('city') or getattr(family, 'city', '')}|"
                f"{payload.get('target_date') or getattr(family, 'target_date', '')}|"
                f"{metric}|{observed_extreme!r}|{payload.get('observation_time') or ''}|"
                f"{observation_id}"
            ).encode("utf-8")
        ).hexdigest()
        self._state = Day0ObservationState(
            observed=True,
            station_id=str(payload.get("station_id") or ""),
            source=str(payload.get("settlement_source") or payload.get("source") or ""),
            samples_count=samples_count,
            latest_observed_at_utc=_payload_datetime_utc(
                payload.get("observation_time") or payload.get("observation_available_at")
            ),
            observed_high_native=observed_high,
            observed_low_native=observed_low,
            observed_extreme_native=observed_extreme,
            raw_observation_hash=raw_hash,
        )

    @property
    def block_reason(self) -> Optional[str]:
        return self._block_reason

    def read(self, case: ForecastCase) -> Optional[Day0ObservationState]:  # noqa: ARG002
        return self._state


# ---------------------------------------------------------------------------
# Sizing candidates + payoff matrix + exposure (the real spine types, built from
# the reactor-native proofs — the SAME utility_ranker geometry the payoff layer uses).
# ---------------------------------------------------------------------------

def _sizing_candidates_from_proofs(
    *,
    family_key: str,
    proofs: Sequence[Any],
    native_side_candidate_from_proof,
    candidate_bin_id,
) -> dict[tuple[str, str], Any]:
    """Materialize the spine ``sizing_candidates`` map keyed by (bin_id, side).

    Reuses the reactor's ONE materialization path
    (``_native_side_candidate_from_proof``) so the sizing candidate the spine sizes
    against is byte-identical to the legacy ranker's candidate. The key is
    (bin_id, side) where side is YES/NO (the spine's route side) — derived from the
    proof's direction (buy_yes -> YES, buy_no -> NO).
    """
    out: dict[tuple[str, str], Any] = {}
    for (bin_id, side), proof in _canonical_proofs_by_bin_side(
        proofs,
        candidate_bin_id,
        require_probability_unit_price=True,
    ).items():
        try:
            candidate = native_side_candidate_from_proof(family_key=family_key, proof=proof)
        except Exception:  # noqa: BLE001 — a non-materializable proof is simply absent
            continue
        out[(bin_id, side)] = candidate
    return out


def _family_min_order_shares(proofs: Sequence[Any]) -> Decimal:
    """The family's venue min order size (probability-unit shares) for route pricing.

    Reads ``min_order_size`` off the proofs' rows (the same venue floor the leaf
    ``executable_cost`` walker asserts). The route surface MUST be priced at a feasible
    size; pricing at the engine default of 1 share would mark routes non-executable on
    a book whose min order is larger. Defaults to ``Decimal("5")`` when no row carries a
    parseable min order size.
    """
    best: Optional[Decimal] = None
    for proof in proofs:
        row = getattr(proof, "row", None)
        if not isinstance(row, Mapping):
            continue
        try:
            mo = Decimal(str(row.get("min_order_size") or "5"))
        except (TypeError, ValueError):
            continue
        if mo > 0 and (best is None or mo < best):
            best = mo
    return best if best is not None else Decimal("5")


def _proof_by_bin_side(
    proofs: Sequence[Any], candidate_bin_id
) -> dict[tuple[str, str], Any]:
    """Index the reactor proofs by (bin_id, side) for the selected-proof remap."""
    return _canonical_proofs_by_bin_side(
        proofs,
        candidate_bin_id,
        require_probability_unit_price=True,
    )


def _proof_side(proof: Any) -> str | None:
    direction = str(getattr(proof, "direction", "") or "")
    return "YES" if direction == "buy_yes" else ("NO" if direction == "buy_no" else None)


def _proof_execution_price_value(proof: Any) -> Decimal | None:
    execution_price = getattr(proof, "execution_price", None)
    if execution_price is None or getattr(execution_price, "currency", None) != "probability_units":
        return None
    try:
        return Decimal(str(getattr(execution_price, "value")))
    except Exception:  # noqa: BLE001
        return None


def _canonical_proofs_by_bin_side(
    proofs: Sequence[Any],
    candidate_bin_id,
    *,
    require_probability_unit_price: bool,
) -> dict[tuple[str, str], Any]:
    """Choose one proof per (bin_id, side), then reuse it across route/size/remap.

    The bridge is the boundary between reactor-native proof objects and qkernel
    route economics. A duplicate key cannot be allowed to size on one proof, route
    on another, and remap selection to a third; choose once by executable price and
    stable venue identity, and every downstream map consumes that same proof.
    """

    chosen: dict[tuple[str, str], Any] = {}
    chosen_key: dict[tuple[str, str], tuple] = {}
    for proof in proofs:
        if _proof_direction_law_rejected(proof):
            continue
        side = _proof_side(proof)
        if side is None:
            continue
        price = _proof_execution_price_value(proof)
        if require_probability_unit_price and price is None:
            continue
        candidate = getattr(proof, "candidate", None)
        key = (candidate_bin_id(proof), side)
        rank_key = (
            price if price is not None else Decimal("Infinity"),
            str(getattr(proof, "token_id", "") or ""),
            str(getattr(candidate, "condition_id", "") or ""),
            str(getattr(proof, "executable_snapshot_id", "") or ""),
        )
        if key not in chosen or rank_key < chosen_key[key]:
            chosen[key] = proof
            chosen_key[key] = rank_key
    return chosen


def _proof_direction_law_rejected(proof: Any) -> bool:
    """Whether a proof should be removed before qkernel scoring.

    The legacy ``DIRECTION_LAW_BIN_FORECAST_MISMATCH`` reason was a rounded-mu bin
    heuristic. It is not live authority after the qkernel payoff-vector selector owns
    side/bin admission, so it must not delete an otherwise valid native direct proof
    before the spine can compute edge and robust utility. Malformed native side is
    handled by the callers when they derive YES/NO from ``proof.direction``.
    """

    reason = str(getattr(proof, "missing_reason", "") or "")
    if reason.startswith(_LEGACY_ROUNDED_MU_DIRECTION_REJECTION_PREFIX):
        return False
    return False


def _parse_candidate_id(candidate_id: str) -> Optional[tuple[str, str]]:
    """Parse a spine ``CandidateEconomics.candidate_id`` ('SIDE:bin_id:route_id').

    Returns (bin_id, side) or ``None`` when the id is not parseable. The engine builds
    candidate ids as ``f"{side}:{bin_id}:{route_cost.route_id}"`` (side YES/NO).
    """
    parts = candidate_id.split(":", 2)
    if len(parts) < 2:
        return None
    side = parts[0]
    bin_id = parts[1]
    if side not in ("YES", "NO"):
        return None
    return bin_id, side


def _selected_route_is_direct(selected: Any) -> bool:
    """Whether the spine's selected candidate is a DIRECT native route.

    The submit path executes ONE native leg, so only ``DIRECT_YES`` / ``DIRECT_NO``
    routes map to a single ``_CandidateProof``. The engine stamps
    ``CandidateEconomics.route_id`` (and the candidate_id ``SIDE:bin_id:route_id``)
    from ``RouteCost.route_id`` = ``f"{route_type}:{bin_id}@{shares}"``. A direct route
    therefore begins with ``DIRECT_YES:`` / ``DIRECT_NO:``; a synthetic / arb /
    conversion route begins with ``SYNTHETIC_NOT_I_YES_BASKET:`` / ``PAIR_ARB:`` /
    ``FULL_YES_BASKET_ARB`` / ``CONVERSION_SELL_BASKET:`` and is NOT directly
    executable. Reads ``route_id`` (authoritative) and falls back to parsing the
    candidate_id's route segment.
    """
    route_id = str(getattr(selected, "route_id", "") or "")
    if not route_id:
        candidate_id = str(getattr(selected, "candidate_id", "") or "")
        parts = candidate_id.split(":", 2)
        route_id = parts[2] if len(parts) >= 3 else ""
    return route_id.startswith(_DIRECT_ROUTE_ID_PREFIXES)


# ===========================================================================
# The bridge entry point.
# ===========================================================================

def decide_family_via_spine(
    *,
    family: Any,
    payload: Mapping[str, Any],
    proofs: Sequence[Any],
    selection_proofs: Optional[Sequence[Any]] = None,
    decision_time: datetime,
    native_side_candidate_from_proof,
    candidate_bin_id,
    payoff_matrix_over_bins,
    exposure_builder,
    baseline_usd_provider,
    per_bin_yes_q_lcb: Mapping[str, float],
    extra_exposure_by_bin_id: Optional[Mapping[str, float]] = None,
    max_stake_usd: Optional[Decimal] = None,
) -> SpineDecisionResult:
    """Route ONE family's decision through the rebuilt spine and remap the selection.

    Called ONLY when ``qkernel_spine_enabled()`` is True. Builds the spine inputs from
    the reactor-native data in scope, calls ``FamilyDecisionEngine.decide()``, and
    maps ``FamilyDecision.selected`` back onto the matching reactor ``_CandidateProof``
    (with the spine's economics overlaid onto the receipt-facing fields) so the
    submission pipeline consumes it unchanged.

    Args (all reactor-native objects/callables passed by the seam to avoid a circular
    import of the giant adapter module):
        family: the reactor ``EventBoundCandidateFamily`` (city/date/metric/candidates).
        payload: the threaded payload (carries the Stage-0 ``_edli_spine_*`` inputs).
        proofs: the full per-candidate ``_CandidateProof`` tuple already materialized
            for the submission pipeline. This is the served family belief surface and
            must stay complete over Omega.
        selection_proofs: optional executable/admission-scoped subset used only for
            route/book/sizing construction and selected-proof remap.
        decision_time: the decision instant (tz-aware UTC).
        native_side_candidate_from_proof: the reactor's
            ``_native_side_candidate_from_proof`` (the ONE materialization path).
        candidate_bin_id: the reactor's ``_candidate_bin_id`` (proof -> bin_id).
        payoff_matrix_over_bins: ``utility_ranker.FamilyPayoffMatrix.over_bins``.
        exposure_builder: the reactor's ``_robust_marginal_utility_exposure``.
        baseline_usd_provider: the reactor's ``_robust_marginal_utility_baseline_usd``.
        per_bin_yes_q_lcb: the reactor's per-bin YES q_lcb (the robust π the matrix uses).
        extra_exposure_by_bin_id / max_stake_usd: the existing-exposure / cash bound.

    Returns a ``SpineDecisionResult`` — a selected proof OR a typed no-trade, plus the
    ``FamilyDecision`` for the receipt. Never raises into the reactor hot path.
    """
    family_key = str(getattr(family, "family_id", "") or "family")
    belief_proofs = tuple(proofs)
    route_proofs = tuple(selection_proofs) if selection_proofs is not None else belief_proofs
    served = _served_predictive_inputs(payload)
    if served is None:
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=f"{NO_TRADE_SPINE_INPUTS_UNAVAILABLE}:{_spine_inputs_missing_reason(payload)}",
            decision=None,
        )

    try:
        # The ForecastCase issue / source_cycle / lead derive from the FORECAST SOURCE
        # CYCLE that produced the served members (threaded under
        # _edli_spine_source_cycle_time_utc; _served_predictive_inputs already failed
        # closed to SPINE_INPUTS_UNAVAILABLE if it was absent), NOT decision_time.
        case = build_forecast_case(
            family, source_cycle_time_utc=served["source_cycle_time_utc"]
        )
        event_type = str(
            payload.get("event_type") or getattr(family, "event_type", "") or ""
        )
        is_day0_family = event_type == "DAY0_EXTREME_UPDATED"
        day0_reader = _PayloadDay0Reader(payload, family, enabled=is_day0_family)
        if day0_reader.block_reason is not None:
            return SpineDecisionResult(
                selected_proof=None,
                no_trade_reason=f"{NO_TRADE_SPINE_INPUTS_UNAVAILABLE}:{day0_reader.block_reason}",
                decision=None,
            )
        # LEAD-BUCKET ADMISSION (2026-06-15). The prior "only 24h" restriction was tied to
        # the settlement-EV REPLAY (round-2 §3) — which the operator DELETED (price replay is
        # not the validation; settlement-σ coverage is). Every FORECAST lead bucket
        # (24h/72h/96h_plus) carries a conservative per-lead σ-floor: build_sigma serves
        # max(global_lead_bucket_floor, realized_floor), and global_lead_bucket_floor widens
        # +0.10°C/lead-day, so a longer lead is honestly WIDER => q_lcb is strictly LOWER =>
        # the spine's own edge_lcb>0 filter sets a strictly HIGHER edge bar at long lead. The
        # q is therefore calibration-honest at every forecast lead, and edge_lcb>0 (not a
        # bucket whitelist) is the EV gate — the spine self-restricts to genuine positive
        # edge. For DAY0_EXTREME_UPDATED, the same family optimizer is now fed the live
        # observed extreme through ``day0_reader``; Day0 is a belief input, not a separate
        # legacy selector.
        from src.forecast.sigma_authority import lead_bucket_for

        if lead_bucket_for(case) == "day0" and not is_day0_family:
            return SpineDecisionResult(
                selected_proof=None,
                no_trade_reason=NO_TRADE_QKERNEL_LEAD_BUCKET_NOT_REPLAYED,
                decision=None,
            )
        omega = build_outcome_space(family, case)
        _band_alpha = _qkernel_spine_band_alpha()
        (
            served_joint_q,
            served_band,
            served_payoff_q_lcb_by_side,
            served_belief_reason,
        ) = _served_joint_belief_from_proofs(
            omega=omega,
            proofs=belief_proofs,
            candidate_bin_id=candidate_bin_id,
            alpha=_band_alpha,
        )
        if served_belief_reason:
            return SpineDecisionResult(
                selected_proof=None,
                no_trade_reason=f"{NO_TRADE_SPINE_INPUTS_UNAVAILABLE}:{served_belief_reason}",
                decision=None,
            )
        models = build_fresh_model_set(case, served)
        sizing_candidates = _sizing_candidates_from_proofs(
            family_key=family_key,
            proofs=route_proofs,
            native_side_candidate_from_proof=native_side_candidate_from_proof,
            candidate_bin_id=candidate_bin_id,
        )
        # The payoff matrix + exposure are the SAME utility_ranker geometry the legacy
        # ranker uses (built over the tradeable family bins).
        bin_ids = list(dict.fromkeys(b.bin_id for b in omega.bins))
        matrix = payoff_matrix_over_bins(bin_ids)
        baseline_usd = baseline_usd_provider()
        exposure = exposure_builder(
            matrix,
            baseline_usd=baseline_usd,
            extra_exposure_by_bin_id=extra_exposure_by_bin_id,
        )
        _engine_kwargs: dict[str, Any] = {}
        if SPINE_BAND_DRAWS is not None:
            _engine_kwargs["n_band_draws"] = int(SPINE_BAND_DRAWS)
        _engine_kwargs["band_alpha"] = _band_alpha
        engine = FamilyDecisionEngine(
            fresh_model_reader=_ReactorServedFreshModelReader(models),
            day0_reader=day0_reader,
            # The belief authority at this seam is the reactor-served live predictive
            # payload: center/day0/raw-law structure is assembled by the spine builder
            # over the RAW multi-model member envelope, while σ is preserved from the
            # payload's served predictive width. This keeps entry, monitor, and qkernel
            # on one belief; the bridge must not rebuild a second wider/narrower σ and
            # then score a different distribution than the live materializer served.
            predictive_builder=_ReactorServedPredictiveBuilder(
                _spine_debias_authority(case),
                served_sigma_native=float(served["sigma_native"]),
            ),
            # ROUTE IDENTITY (consult_review_pr409.md §5 BLOCKER): DIRECT native routes
            # ONLY. The unchanged submit path executes ONE native leg, so the decision
            # may only choose a route a single _CandidateProof can execute. Disabling the
            # neg-risk routes makes build_negrisk_route_set produce direct-only routes
            # (synthetic_not_i / pair_arbs / full_basket_arbs / conversion_routes are all
            # empty) and best_no_route returns the DIRECT NO — so the engine cannot select
            # a multi-leg synthetic/arb route the bridge would have to silently single-leg
            # map. The full multi-leg route-intent submit is a later arc; until it exists
            # this is the minimum-safe restriction. A non-direct selection (defensive,
            # unreachable while this flag is False) is REFUSED below as a typed no-trade.
            enable_negrisk_routes=False,
            # Inject a family_book_builder that assembles the FamilyBook DIRECTLY from
            # the reactor proofs' native ladders (the SAME books the reactor priced
            # each proof against) — bypassing ExecutableMarketSnapshot reconstruction.
            family_book_builder=_family_book_builder_from_proofs(route_proofs, candidate_bin_id),
            # PROOF-NATIVE direct routes (consult_review_pr409_round2.md §1): each direct
            # YES/NO route is priced at the proof's OWN maker/taker execution_price, not
            # the negrisk ask-ladder. This preserves the maker buy_no edge class (resting
            # bid into an empty NO ask) the ask-ladder taker cost would discard.
            route_set_builder=_proof_native_direct_route_set_builder(route_proofs, candidate_bin_id),
            # Live money ranks admissible native legs by ROI frontier: lower-bound edge
            # per capital with an absolute usefulness floor, then robust log-utility. This
            # preserves NO when it is truly best, but stops low-ROI large NO legs from
            # suppressing capital-efficient YES.
            selection_objective="roi_frontier",
            **_engine_kwargs,
        )
        captured_at_utc = decision_time if decision_time.tzinfo else decision_time.replace(tzinfo=timezone.utc)
        # The route surface is priced at a FEASIBLE share size — the family's venue min
        # order size (the smallest executable order). Pricing the routes at the engine
        # default of 1 share would mark every route non-executable on a book whose min
        # order is >1 (the NO_EXECUTABLE_ROUTE_CANDIDATE false no-trade). The min order
        # is read off the proofs' rows (probability units); default to a safe 5.
        shares_for_routing = _family_min_order_shares(route_proofs)
        decision = engine.decide(
            case,
            omega,
            # snapshots arg is ignored by the injected family_book_builder above (the
            # books come from the proofs); pass an empty map to satisfy the signature.
            {},
            portfolio=exposure,
            matrix=matrix,
            captured_at_utc=captured_at_utc,
            sizing_candidates=sizing_candidates,
            max_stake_usd=max_stake_usd,
            shares_for_routing=shares_for_routing,
            served_joint_q=served_joint_q,
            served_band=served_band,
            served_payoff_q_lcb_by_side=served_payoff_q_lcb_by_side,
        )
    except (ResolutionError, OutcomeSpaceError) as exc:
        # A settlement/topology resolution fault is a genuine reconstruction gap:
        # return a typed no-trade so the reactor emits a deterministic receipt.
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=f"{NO_TRADE_SPINE_INPUTS_UNAVAILABLE}:{exc}",
            decision=None,
        )
    except (FamilyDecisionError, Exception) as exc:  # noqa: BLE001 — never raise into the hot path
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=f"{NO_TRADE_SPINE_WIRING_FAULT}:{type(exc).__name__}:{exc}",
            decision=None,
        )

    # --- map the spine's selection back onto the reactor proof -----------------
    if decision.selected is None:
        # Non-gating telemetry: on no-trade, log the top candidates by edge_lcb so the
        # operator can distinguish no positive edge from a near-miss. Read-only; fail-safe.
        try:
            import logging as _spine_telemetry_logging

            _cands = sorted(
                getattr(decision, "candidates", ()) or (),
                key=lambda e: (e.edge_lcb if e.edge_lcb is not None else float("-inf")),
                reverse=True,
            )[:3]
            if _cands:
                _top = "; ".join(
                    f"{c.candidate_id} edge_lcb={c.edge_lcb:+.5f} dU={c.optimal_delta_u:+.6f} "
                    f"dU_min={c.delta_u_at_min:+.6f} pt_ev={c.point_ev:+.5f} "
                    f"cost={float(c.cost.value):.4f} stake={c.optimal_stake_usd}"
                    for c in _cands
                )
                _spine_telemetry_logging.getLogger("zeus.spine_edge").info(
                    "SPINE_NOTRADE_EDGE_TELEMETRY family=%s reason=%s top=[%s]",
                    getattr(case, "family_id", "?"),
                    decision.no_trade_reason,
                    _top,
                )
        except Exception:
            pass
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=decision.no_trade_reason or NO_TRADE_SPINE_NO_SELECTION,
            decision=decision,
        )

    # ROUTE IDENTITY GUARD (consult_review_pr409.md §5 BLOCKER). The unchanged submit
    # path executes ONE native leg, so only a DIRECT native route maps to a single
    # _CandidateProof. The engine is driven direct-only (enable_negrisk_routes=False),
    # so a non-direct selection is unreachable on the live lane — but if a route other
    # than DIRECT_YES/DIRECT_NO is ever selected, REFUSE it as a typed no-trade rather
    # than silently single-leg-mapping a multi-leg synthetic/arb route the submit path
    # cannot execute. (This is the second, defensive half of the minimum-safe fix: the
    # engine flag prevents it; this guard makes a regression that re-enabled neg-risk
    # routes fail closed instead of mis-executing.)
    if not _selected_route_is_direct(decision.selected):
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=NO_TRADE_ROUTE_NOT_DIRECTLY_EXECUTABLE,
            decision=decision,
        )

    proof_index = _proof_by_bin_side(route_proofs, candidate_bin_id)
    parsed = _parse_candidate_id(decision.selected.candidate_id)
    selected_proof = proof_index.get(parsed) if parsed is not None else None
    if selected_proof is None:
        # The spine selected a (bin, side) the reactor has no proof for — a wiring
        # fault (the proofs and the routes disagree about the family). Typed no-trade.
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=(
                f"{NO_TRADE_SPINE_WIRING_FAULT}:SELECTED_PROOF_NOT_FOUND:"
                f"{decision.selected.candidate_id}"
            ),
            decision=decision,
        )

    overlay_result = _overlay_spine_economics_onto_proof_with_reason(selected_proof, decision)
    if overlay_result.proof is None:
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=(
                f"{NO_TRADE_SPINE_WIRING_FAULT}:QKERNEL_EXECUTION_CERTIFICATE_OVERLAY_FAILED:"
                f"{overlay_result.reason or 'UNKNOWN'}:"
                f"{decision.selected.candidate_id}"
            ),
            decision=decision,
        )
    return SpineDecisionResult(
        selected_proof=overlay_result.proof,
        no_trade_reason=None,
        decision=decision,
    )


def _family_book_builder_from_proofs(
    proofs: Sequence[Any],
    candidate_bin_id,
):
    """Return a ``FamilyBookBuilder`` that assembles the family book from proof rows.

    This is the resolved family-book input (see module docstring). The reactor's
    per-family decision seam holds the executable snapshot ROWS on the proofs, not
    reconstructed ``ExecutableMarketSnapshot`` objects. Rebuilding the full snapshot
    contract from a raw row is schema-coupled and fragile; instead this builder reads
    each sibling's four native ladders DIRECTLY off the proof's row
    (``orderbook_depth_json`` / ``orderbook_depth_jsonb``) into a ``MarketBook`` — the
    SAME native ladders the reactor priced each proof's ``execution_price`` against. So
    the route set / candidate economics the spine computes walk the SAME books the
    reactor's q-build saw, with no second capture and no snapshot reconstruction.

    The returned callable matches the engine's injected ``FamilyBookBuilder`` protocol
    (``__call__(*, omega, snapshots_by_bin_id, captured_at_utc) -> FamilyBook``); the
    ``snapshots_by_bin_id`` argument is ignored (the books come from the proofs).
    """
    from src.execution.family_book import (
        ExecutableLadder,
        MarketBook,
        build_family_book,
    )
    from src.strategy.live_inference.executable_cost import QuoteLevel

    # One row per bin_id (the per-sibling executable surface). A bin's row is taken
    # from whichever proof on that bin carries it (YES and NO proofs share the row).
    row_by_bin: dict[str, Any] = {}
    for proof in proofs:
        bin_id = candidate_bin_id(proof)
        if bin_id in row_by_bin:
            continue
        row = getattr(proof, "row", None)
        if isinstance(row, Mapping):
            row_by_bin[bin_id] = row

    def _levels(side_obj: Any, key: str) -> tuple:
        levels = []
        for lvl in (side_obj or {}).get(key, []) or []:
            try:
                price = Decimal(str(lvl["price"]))
                size = Decimal(str(lvl["size"]))
            except (KeyError, TypeError, ValueError):
                continue
            if price > 0 and size > 0:
                levels.append(QuoteLevel(price=price, size=size))
        return tuple(levels)

    def _ladder(side_obj: Any, key: str, *, side: str, tick: Decimal, min_order: Decimal, fee: float):
        return ExecutableLadder(
            levels=_levels(side_obj, key),
            side=side,  # type: ignore[arg-type]
            fee_rate=fee,
            min_tick_size=tick,
            min_order_size=min_order,
        )

    def _build(*, omega: OutcomeSpace, snapshots_by_bin_id=None, captured_at_utc):  # noqa: ARG001
        import json as _json

        markets: dict[str, MarketBook] = {}
        omega_bins = {b.bin_id: b for b in omega.bins}
        for bin_id, row in row_by_bin.items():
            if bin_id not in omega_bins:
                continue
            bin_meta = omega_bins[bin_id]
            raw_depth = row.get("orderbook_depth_json") or row.get("orderbook_depth_jsonb") or "{}"
            try:
                depth = _json.loads(raw_depth) if isinstance(raw_depth, str) else dict(raw_depth)
            except (TypeError, ValueError):
                depth = {}
            yes = depth.get("YES") or {}
            no = depth.get("NO") or {}
            try:
                tick = Decimal(str(row.get("min_tick_size") or "0.01"))
                min_order = Decimal(str(row.get("min_order_size") or "5"))
            except (TypeError, ValueError):
                tick, min_order = Decimal("0.01"), Decimal("5")
            fee = 0.0
            try:
                fee_details = row.get("fee_details_json")
                if isinstance(fee_details, str) and fee_details:
                    fee = float(_json.loads(fee_details).get("fee_rate_fraction", 0.0))
            except (TypeError, ValueError):
                fee = 0.0
            try:
                market = MarketBook(
                    condition_id=str(row.get("condition_id") or bin_meta.condition_id or ""),
                    bin_id=bin_id,
                    yes_token_id=str(row.get("yes_token_id") or bin_meta.yes_token_id or ""),
                    no_token_id=str(row.get("no_token_id") or bin_meta.no_token_id or ""),
                    yes_asks=_ladder(yes, "asks", side="ask", tick=tick, min_order=min_order, fee=fee),
                    yes_bids=_ladder(yes, "bids", side="bid", tick=tick, min_order=min_order, fee=fee),
                    no_asks=_ladder(no, "asks", side="ask", tick=tick, min_order=min_order, fee=fee),
                    no_bids=_ladder(no, "bids", side="bid", tick=tick, min_order=min_order, fee=fee),
                    neg_risk=bool(row.get("neg_risk") or 0),
                )
            except Exception:  # noqa: BLE001 — a malformed sibling book is simply absent
                continue
            markets[bin_id] = market
        return build_family_book(
            omega=omega, markets=markets, captured_at_utc=captured_at_utc
        )

    return _build


def _proof_native_direct_route_set_builder(proofs: Sequence[Any], candidate_bin_id):
    """Return a ``RouteSetBuilder`` that prices DIRECT routes at each proof's own cost.

    PROOF-NATIVE single-leg routing (consult_review_pr409_round2.md §1/§5 BLOCKER
    "direct-native route realization"). The v1 live edge class is a maker buy_no into an
    empty NO ask, priced as the resting bid behind the complementary book — the reactor
    already submits this. ``negrisk_routes`` direct-NO walks the NO ASK (taker), which
    DISCARDS that maker edge. So in v1 the route surface is NOT built off the ask ladder:
    each reactor ``_CandidateProof`` IS one direct-native route, and its cost is the
    proof's own ``execution_price`` (the exact maker/taker all-in cost, fee-applied, in
    probability units, that the unchanged submit path will carry). This builder produces a
    ``NegRiskRouteSet`` whose ``direct_yes`` / ``direct_no`` per bin are priced at those
    proof execution prices, with EVERY neg-risk surface empty (synthetic / pair / basket /
    conversion) — synthetic/arb/conversion stay disabled until a real multi-leg
    route-intent submit exists. Each route is exactly ONE leg whose token/condition is the
    proof's, so the selected route maps back to that proof unambiguously.

    The returned callable matches the engine's ``RouteSetBuilder`` protocol
    ``(family_book, *, shares, enable_negrisk_routes) -> NegRiskRouteSet``; the family_book
    / shares / flag args are accepted for signature parity but the routes come from the
    proofs (the proof-native cost is the authority, not the book ladder).
    """
    from src.execution.negrisk_routes import NegRiskRouteSet, RouteCost, RouteLeg
    from src.probability.instruments import Instrument

    direct_yes: dict[str, RouteCost] = {}
    direct_no: dict[str, RouteCost] = {}
    for (bin_id, side), proof in _canonical_proofs_by_bin_side(
        proofs,
        candidate_bin_id,
        require_probability_unit_price=True,
    ).items():
        direction = str(getattr(proof, "direction", "") or "")
        execution_price = getattr(proof, "execution_price", None)
        candidate = getattr(proof, "candidate", None)
        token_id = str(getattr(proof, "token_id", "") or "")
        condition_id = str(getattr(candidate, "condition_id", "") or "")
        route_type = "DIRECT_YES" if side == "YES" else "DIRECT_NO"
        instrument = Instrument(
            instrument_id=f"{side}:{bin_id}",
            bin_id=bin_id,
            side=side,
            direct_token_id=token_id or None,
        )
        leg = RouteLeg(
            condition_id=condition_id,
            bin_id=bin_id,
            token_id=token_id,
            direction=direction,  # type: ignore[arg-type]
            shares=Decimal("1"),
            leg_cost=execution_price,
        )
        route = RouteCost(
            route_id=f"{route_type}:{bin_id}@proof",
            route_type=route_type,  # type: ignore[arg-type]
            instrument=instrument,
            shares=Decimal("1"),
            avg_cost=execution_price,
            max_shares=Decimal("1000000"),
            legs=(leg,),
            executable=True,
            reason=None,
        )
        target = direct_yes if side == "YES" else direct_no
        target[bin_id] = route

    def _build(family_book, *, shares=Decimal("1"), enable_negrisk_routes=False):  # noqa: ARG001
        return NegRiskRouteSet(
            direct_yes=dict(direct_yes),
            direct_no=dict(direct_no),
            synthetic_not_i={},
            pair_arbs=(),
            full_basket_arbs=(),
            conversion_routes=(),
        )

    return _build


def _overlay_spine_economics_onto_proof(proof: Any, decision: FamilyDecision) -> Any | None:
    return _overlay_spine_economics_onto_proof_with_reason(proof, decision).proof


def _overlay_spine_economics_onto_proof_with_reason(
    proof: Any,
    decision: FamilyDecision,
) -> _OverlayResult:
    """Overlay the spine decision's economics onto the selected reactor proof.

    The submission pipeline reads ``q_posterior`` / ``q_lcb_5pct`` /
    ``trade_score`` / ``execution_price`` etc. off the proof. Qkernel may rank,
    size, and tighten the lower bound for the selected direct route, but the
    direct-route point probability must be the same selected-side probability
    already served on the proof. If those disagree, the route/payoff identity is
    not the same belief surface and the live path must no-trade rather than mint
    a new probability by overlay.

    The executable identity (row / token / execution_price /
    native_quote_available) is LEFT UNCHANGED — the spine selected this exact
    executable leg, and the submit pipeline re-authorizes it at submit time.
    Returns a NEW proof (frozen dataclass replace) so the original tuple is
    untouched.
    """
    from dataclasses import replace

    selected = decision.selected
    if selected is None:
        return _OverlayResult(None, "SELECTION_MISSING")
    if _proof_direction_law_rejected(proof):
        return _OverlayResult(None, "DIRECTION_LAW_REJECTED_PROOF")
    missing_reason = str(getattr(proof, "missing_reason", "") or "").strip()
    if not _qkernel_may_clear_legacy_missing_reason(missing_reason):
        return _OverlayResult(
            None,
            f"PROOF_ADMISSION_REJECTION_NOT_QKERNEL_RECOVERABLE:{missing_reason}",
        )
    selected_decision = None
    for candidate_decision in getattr(decision, "candidate_decisions", ()) or ():
        try:
            if candidate_decision.economics.candidate_id == selected.candidate_id:
                selected_decision = candidate_decision
                break
        except Exception:  # noqa: BLE001
            continue
    qkernel_execution_economics = _candidate_qkernel_execution_economics_payload(
        decision,
        selected_decision,
        selected=selected,
    )
    if qkernel_execution_economics is None:
        return _OverlayResult(None, "PAYLOAD_BUILD_FAILED")
    selection_guard_reason = _qkernel_selection_guard_rejection_reason(
        qkernel_execution_economics
    )
    if selection_guard_reason:
        return _OverlayResult(None, selection_guard_reason)
    qkernel_q_point, qkernel_q_lcb = _direct_route_probability_pair(
        qkernel_execution_economics
    )
    if qkernel_q_point is None or qkernel_q_lcb is None:
        return _OverlayResult(None, "INVALID_DIRECT_Q_PAIR")
    try:
        proof_q_point = float(getattr(proof, "q_posterior"))
        proof_q_lcb = float(getattr(proof, "q_lcb_5pct"))
    except (TypeError, ValueError):
        proof_q_point = float("nan")
        proof_q_lcb = float("nan")
    if math.isfinite(proof_q_point):
        qkernel_execution_economics["pre_qkernel_q_posterior"] = proof_q_point
    if math.isfinite(proof_q_lcb):
        qkernel_execution_economics["pre_qkernel_q_lcb_5pct"] = proof_q_lcb
    qkernel_execution_economics["q_lcb_authority"] = "qkernel_payoff_bound"
    qkernel_execution_economics["probability_authority"] = "qkernel_payoff_direct_route"
    served_belief_reason = _qkernel_served_belief_consistency_rejection_reason(
        qkernel_q_point=qkernel_q_point,
        qkernel_q_lcb=qkernel_q_lcb,
        proof_q_point=proof_q_point,
        proof_q_lcb=proof_q_lcb,
    )
    if served_belief_reason:
        return _OverlayResult(None, served_belief_reason)
    if not _qkernel_execution_direction_admitted(
        qkernel_execution_economics,
        direction=str(getattr(proof, "direction", "") or ""),
    ):
        return _OverlayResult(None, "DIRECTION_NOT_ADMITTED")
    if qkernel_execution_economics.get("coherence_allows") is not True:
        return _OverlayResult(None, "COHERENCE_BLOCKED")
    edge_lcb = float(qkernel_execution_economics["edge_lcb"])
    false_edge_rate = _qkernel_false_edge_rate(decision, selected_decision)
    if false_edge_rate is None:
        return _OverlayResult(None, "FALSE_EDGE_RATE_UNAVAILABLE")
    qkernel_execution_economics["false_edge_rate"] = false_edge_rate
    overlay: dict[str, Any] = {
        # The selected qkernel candidate is licensed by its route economics. Feed
        # the same probability pair to receipt, sizing, and submit verification.
        "trade_score": edge_lcb,
        "q_posterior": qkernel_q_point,
        "q_lcb_5pct": qkernel_q_lcb,
        "qkernel_execution_economics": qkernel_execution_economics,
        "selection_authority_applied": "qkernel_spine",
        # qkernel has re-ranked this proof under the settlement/payoff family law.
        # A legacy scalar admission veto on the pre-spine proof must not survive
        # into the receipt/opportunity-book admission predicate.
        "missing_reason": None,
        "p_value": false_edge_rate,
        "passed_prefilter": edge_lcb > 0.0,
    }
    try:
        return _OverlayResult(replace(proof, **overlay))
    except Exception:  # noqa: BLE001 — non-replaceable proof is a bridge wiring fault
        return _OverlayResult(None, "PROOF_REPLACE_FAILED")


def _qkernel_may_clear_legacy_missing_reason(missing_reason: str | None) -> bool:
    """Allow qkernel to rescore obsolete pre-spine blockers.

    The low-price YES authority moved into the qkernel ROI/submit proof chain:
    qkernel may clear the old center-buy ultra-low scalar veto, while true live
    policy gates remain non-recoverable by overlay.
    """

    text = str(missing_reason or "").strip()
    if not text:
        return True
    return text.startswith(
        (
            "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV",
            "ADMISSION_CAPITAL_EFFICIENCY",
            "ADMISSION_WIN_RATE_FLOOR",
            "CENTER_BUY_ULTRA_LOW_PRICE",
            "DIRECTION_LAW_BIN_FORECAST_MISMATCH",
        )
    )


def _qkernel_selection_guard_rejection_reason(
    qkernel_execution_economics: Mapping[str, Any],
) -> str:
    basis = str(qkernel_execution_economics.get("selection_guard_basis") or "").strip()
    if not basis:
        return "SELECTION_GUARD_MISSING"
    if basis == "SIDE_NOT_ARMED":
        return "SELECTION_GUARD_SIDE_NOT_ARMED"
    raw_abstained = qkernel_execution_economics.get("selection_guard_abstained")
    if isinstance(raw_abstained, bool):
        abstained = raw_abstained
    else:
        text = str(raw_abstained).strip().lower()
        if text in {"0", "false", "no"}:
            abstained = False
        elif text in {"1", "true", "yes"}:
            abstained = True
        else:
            return "SELECTION_GUARD_ABSTAINED_UNKNOWN"
    if abstained:
        return "SELECTION_GUARD_ABSTAINED"
    try:
        q_safe = float(qkernel_execution_economics.get("selection_guard_q_safe"))
    except (TypeError, ValueError):
        return "SELECTION_GUARD_Q_SAFE_MISSING"
    if not (math.isfinite(q_safe) and q_safe > 0.0):
        return "SELECTION_GUARD_Q_SAFE_NON_POSITIVE"
    return ""


def _qkernel_served_belief_consistency_rejection_reason(
    *,
    qkernel_q_point: float,
    qkernel_q_lcb: float,
    proof_q_point: float,
    proof_q_lcb: float,
) -> str:
    """Fail closed when qkernel direct-route q is not the served proof belief.

    For a DIRECT_YES/DIRECT_NO leg, ``q_dot_payoff`` is the selected-side scalar
    probability of the same settlement outcome the proof already carries. The
    qkernel can use a tighter lower bound after route/selection guards, but it
    cannot raise the served point probability or loosen the served lower bound.
    """

    tolerance = 1e-6
    if not (
        math.isfinite(proof_q_point)
        and math.isfinite(proof_q_lcb)
        and 0.0 <= proof_q_lcb <= proof_q_point <= 1.0
    ):
        return "QKERNEL_SERVED_BELIEF_INVALID"
    if not math.isclose(qkernel_q_point, proof_q_point, rel_tol=1e-9, abs_tol=tolerance):
        return (
            "QKERNEL_SERVED_BELIEF_POINT_MISMATCH:"
            f"payoff_q_point={qkernel_q_point:.9f}:"
            f"served_q_point={proof_q_point:.9f}"
        )
    if qkernel_q_lcb > proof_q_lcb + tolerance:
        return (
            "QKERNEL_SERVED_BELIEF_LCB_LOOSENED:"
            f"payoff_q_lcb={qkernel_q_lcb:.9f}:"
            f"served_q_lcb={proof_q_lcb:.9f}"
        )
    return ""


def _qkernel_execution_direction_admitted(
    qkernel_execution_economics: Mapping[str, Any],
    *,
    direction: str | None = None,
) -> bool:
    """Mirror the live family selector's native-side admission."""

    if qkernel_execution_economics.get("direction_law_ok") is not True:
        return False
    side = str(qkernel_execution_economics.get("side") or "").upper()
    if side not in {"YES", "NO"}:
        return False
    native_side = "YES" if str(direction or "") == "buy_yes" else (
        "NO" if str(direction or "") == "buy_no" else ""
    )
    if native_side and side != native_side:
        return False
    return True


def _candidate_qkernel_execution_economics_payload(
    decision: FamilyDecision,
    candidate_decision: Any | None,
    *,
    selected: Any | None = None,
) -> dict[str, Any] | None:
    """Compact qkernel economics certificate for one enumerated candidate.

    This is receipt/regret evidence for both selected and no-trade candidates. It
    records the vector payoff economics the qkernel actually ranked on, instead of
    forcing operators to infer qkernel decisions from legacy scalar trade_score.
    """
    if candidate_decision is None and selected is None:
        return None
    selected = selected if selected is not None else getattr(candidate_decision, "economics", None)
    route = getattr(candidate_decision, "route", None) if candidate_decision is not None else None
    if selected is None or route is None:
        if selected is None:
            return None
    try:
        route_cost_value = float(getattr(selected.cost, "value", 0.0) or 0.0)
        chosen_cost = getattr(selected, "chosen_stake_cost", None)
        cost_value = (
            float(getattr(chosen_cost, "value", 0.0) or 0.0)
            if chosen_cost is not None
            else route_cost_value
        )
        edge_lcb_raw = getattr(selected, "chosen_stake_edge_lcb", None)
        point_ev_raw = getattr(selected, "chosen_stake_point_ev", None)
        edge_lcb = float(edge_lcb_raw) if edge_lcb_raw is not None else float(selected.edge_lcb)
        point_ev = float(point_ev_raw) if point_ev_raw is not None else float(selected.point_ev)
        delta_u_at_min = float(selected.delta_u_at_min)
        optimal_delta_u = float(selected.optimal_delta_u)
        q_dot_payoff = float(selected.q_dot_payoff)
        payoff_q_lcb_raw = getattr(selected, "payoff_q_lcb", None)
        if payoff_q_lcb_raw is None:
            return None
        payoff_q_lcb = float(payoff_q_lcb_raw)
        finite_execution_values = (
            cost_value,
            edge_lcb,
            point_ev,
            delta_u_at_min,
            optimal_delta_u,
            q_dot_payoff,
            payoff_q_lcb,
        )
        if not all(math.isfinite(value) for value in finite_execution_values):
            return None
        if not (0.0 <= payoff_q_lcb <= q_dot_payoff + 1e-9 <= 1.0 + 1e-9):
            return None
        expected_edge_lcb = payoff_q_lcb - cost_value
        if not math.isclose(edge_lcb, expected_edge_lcb, rel_tol=1e-9, abs_tol=1e-9):
            return None
        payload: dict[str, Any] = {
            "source": "qkernel_spine",
            "decision_id": getattr(decision, "decision_id", None),
            "receipt_hash": getattr(decision, "receipt_hash", None),
            "candidate_id": selected.candidate_id,
            "route_id": selected.route_id,
            "payoff_q_point": q_dot_payoff,
            "payoff_q_lcb": payoff_q_lcb,
            "edge_lcb": edge_lcb,
            "point_ev": point_ev,
            "delta_u_at_min": delta_u_at_min,
            "optimal_stake_usd": str(selected.optimal_stake_usd),
            "optimal_delta_u": optimal_delta_u,
            "q_dot_payoff": q_dot_payoff,
            "cost": cost_value,
            "cost_basis": "chosen_stake" if chosen_cost is not None else "route",
            "route_cost": route_cost_value,
            "route_edge_lcb": float(selected.edge_lcb),
            "route_point_ev": float(selected.point_ev),
        }
        if chosen_cost is not None:
            payload["chosen_stake_cost"] = cost_value
        if route is not None and candidate_decision is not None:
            payload.update(
                {
                    "side": route.side,
                    "bin_id": route.bin_id,
                    "q_lcb_guard_basis": candidate_decision.q_lcb_guard_basis,
                    "q_lcb_guard_abstained": bool(candidate_decision.q_lcb_guard_abstained),
                    "q_lcb_guard_cell_key": candidate_decision.q_lcb_guard_cell_key,
                    "selection_guard_basis": getattr(
                        candidate_decision, "selection_guard_basis", ""
                    ),
                    "selection_guard_abstained": bool(
                        getattr(candidate_decision, "selection_guard_abstained", False)
                    ),
                    "selection_guard_cell_key": getattr(
                        candidate_decision, "selection_guard_cell_key", ""
                    ),
                    "selection_guard_n": int(
                        getattr(candidate_decision, "selection_guard_n", 0) or 0
                    ),
                    "selection_guard_q_safe": getattr(
                        candidate_decision, "selection_guard_q_safe", None
                    ),
                    "direction_law_ok": bool(
                        getattr(candidate_decision, "direction_law_ok", False)
                    ),
                    "coherence_allows": bool(
                        getattr(candidate_decision, "coherence_allows", False)
                    ),
                    "robust_trade_score": float(
                        getattr(candidate_decision, "robust_trade_score", 0.0) or 0.0
                    ),
                }
            )
        false_edge_rate = _qkernel_false_edge_rate(decision, candidate_decision)
        if false_edge_rate is not None:
            payload["false_edge_rate"] = false_edge_rate
    except (TypeError, ValueError, AttributeError):
        return None
    return payload


def _direct_route_probability_pair(
    qkernel_execution_economics: Mapping[str, Any],
) -> tuple[float | None, float | None]:
    """Return the direct-route selected-side q pair or ``(None, None)``.

    The live submit path executes a single native YES/NO leg for DIRECT routes. For
    that route, ``q_dot_payoff`` is the same selected-side probability the
    receipt/monitor must use (YES_i for buy_yes, 1-YES_i for buy_no). The guarded
    lower bound must be conservative for that same scalar.
    """

    route_id = str(qkernel_execution_economics.get("route_id") or "")
    if not route_id.startswith(("DIRECT_YES:", "DIRECT_NO:")):
        return None, None
    try:
        payoff_q_point = float(qkernel_execution_economics.get("payoff_q_point"))
        payoff_q_lcb = float(qkernel_execution_economics.get("payoff_q_lcb"))
    except (TypeError, ValueError):
        return None, None
    if not all(
        math.isfinite(value)
        for value in (payoff_q_point, payoff_q_lcb)
    ):
        return None, None
    if not (-1e-12 <= payoff_q_lcb <= payoff_q_point + 1e-9 <= 1.0 + 1e-9):
        return None, None
    payoff_q_point = min(max(payoff_q_point, 0.0), 1.0)
    payoff_q_lcb = min(max(payoff_q_lcb, 0.0), payoff_q_point)
    return payoff_q_point, payoff_q_lcb


def qkernel_candidate_economics_by_bin_side(
    decision: FamilyDecision | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return qkernel economics keyed by ``(bin_id, side)`` for receipt projection."""
    if decision is None:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate_decision in getattr(decision, "candidate_decisions", ()) or ():
        payload = _candidate_qkernel_execution_economics_payload(
            decision,
            candidate_decision,
        )
        if payload is None:
            continue
        bin_id = str(payload.get("bin_id") or "").strip()
        side = str(payload.get("side") or "").strip()
        if not bin_id or side not in {"YES", "NO"}:
            continue
        out[(bin_id, side)] = payload
    return out


_GUARDED_FALSE_EDGE_RATE_95_BASES = {
    "DAY0_REMAINING_DAY_Q_LCB",
    "OOF_WILSON_95",
    "OOF_WILSON_95_POOLED_TAIL",
    "SELECTION_BETA_95",
    "SELECTION_EB_BETA",
}


def _guarded_qkernel_false_edge_rate(selected_decision: Any | None) -> float | None:
    """False-edge bound aligned to the guard that produced the served q_lcb.

    Once the family engine applies an active OOF/selection reliability guard,
    the selected edge is no longer the raw ``band.samples @ payoff - cost``
    quantile. It is the guarded ``q_safe - cost`` value. Feeding raw band edges
    to FDR after that creates two live authorities for the same candidate and can
    reject a trade whose served 95% lower bound is already above cost.
    """

    if selected_decision is None:
        return None
    econ = getattr(selected_decision, "economics", None)
    if econ is None:
        return None
    try:
        edge_lcb_raw = getattr(econ, "chosen_stake_edge_lcb", None)
        edge_lcb = float(edge_lcb_raw) if edge_lcb_raw is not None else float(econ.edge_lcb)
        payoff_q_lcb = float(econ.payoff_q_lcb)
        chosen_cost = getattr(econ, "chosen_stake_cost", None)
        cost_obj = chosen_cost if chosen_cost is not None else econ.cost
        cost = float(cost_obj.value)
    except Exception:  # noqa: BLE001
        return None
    if not (
        math.isfinite(edge_lcb)
        and math.isfinite(payoff_q_lcb)
        and math.isfinite(cost)
        and edge_lcb > 0.0
        and 0.0 <= payoff_q_lcb <= 1.0
        and math.isclose(edge_lcb, payoff_q_lcb - cost, rel_tol=1e-9, abs_tol=1e-9)
    ):
        return None

    guarded_bases: list[str] = []
    q_lcb_basis = str(getattr(selected_decision, "q_lcb_guard_basis", "") or "").strip()
    if (
        q_lcb_basis in _GUARDED_FALSE_EDGE_RATE_95_BASES
        and getattr(selected_decision, "q_lcb_guard_abstained", None) is False
    ):
        guarded_bases.append(q_lcb_basis)

    selection_basis = str(getattr(selected_decision, "selection_guard_basis", "") or "").strip()
    if (
        selection_basis in _GUARDED_FALSE_EDGE_RATE_95_BASES
        and getattr(selected_decision, "selection_guard_abstained", None) is False
    ):
        try:
            selection_q_safe = float(getattr(selected_decision, "selection_guard_q_safe"))
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(selection_q_safe) and selection_q_safe > 0.0):
            return None
        guarded_bases.append(selection_basis)

    if not guarded_bases:
        return None
    # The active guard bases above are 95% lower bounds. Use the same confidence
    # semantics as the FDR p-value for the guarded route; unguarded routes fall
    # back to the raw band empirical rate below.
    return 0.05


def _qkernel_false_edge_rate(
    decision: FamilyDecision,
    selected_decision: Any | None,
) -> float | None:
    """Empirical false-edge rate from the qkernel band for the selected route.

    ``edge_lcb`` is a quantile over ``band.samples @ payoff - cost``. FDR must
    consume the same route and same sample distribution, not the legacy proof
    p-value from a scalar selected-side bootstrap. Return ``mean(edge <= 0)`` with
    a finite-sample correction so the selected qkernel candidate carries an
    empirical p-value over the tested band draws.
    """

    guarded_rate = _guarded_qkernel_false_edge_rate(selected_decision)
    if guarded_rate is not None:
        return guarded_rate
    if selected_decision is None or getattr(decision, "band", None) is None:
        return None
    try:
        samples = np.asarray(decision.band.samples, dtype=float)
        payoff = np.asarray(selected_decision.route.payoff_vector, dtype=float)
        chosen_cost = getattr(selected_decision.economics, "chosen_stake_cost", None)
        cost_obj = chosen_cost if chosen_cost is not None else selected_decision.economics.cost
        cost = float(cost_obj.value)
    except Exception:  # noqa: BLE001
        return None
    if samples.ndim != 2 or payoff.ndim != 1 or samples.shape[1] != payoff.shape[0]:
        return None
    if not (np.isfinite(samples).all() and np.isfinite(payoff).all() and np.isfinite(cost)):
        return None
    edges = samples @ payoff - cost
    if edges.size == 0 or not np.isfinite(edges).all():
        return None
    failures = float(np.count_nonzero(edges <= 0.0))
    # Finite-sample correction mirrors the replacement bootstrap p-value style:
    # never emit an exact zero from a finite Monte Carlo band.
    return float((failures + 1.0) / (float(edges.size) + 1.0))
