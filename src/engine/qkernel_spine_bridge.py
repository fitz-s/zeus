# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md (Wave 5 reactor wiring) +
#   docs/rebuild/impl_w4_family_decision_engine.md (the engine contract this bridge
#   drives) + docs/rebuild/arm_replay_report.md (the spine validated BEFORE this
#   integration) + docs/rebuild/impl_w5b_integration.md (this integration's report).
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md.
"""qkernel_spine_bridge — the Wave-5B cutover bridge from the live reactor to the
rebuilt q-kernel spine.

This module is the ONLY place the reactor's per-family decision is routed through
``src/decision/family_decision_engine.FamilyDecisionEngine.decide()``. It exists so
the reactor seam edit stays a single ``if/else`` branch: when the
``qkernel_spine_enabled`` flag is ON, the reactor calls
:func:`decide_family_via_spine` here; when OFF, the legacy decision path runs
byte-for-byte unchanged and NOTHING in this module is imported on the hot path.

WHAT IT DOES (the cutover contract):

  1. ``qkernel_spine_enabled()`` — reads the single boolean cutover flag from
     ``settings["feature_flags"]["qkernel_spine_enabled"]`` (default False) using the
     SAME accessor the other reactor flags use (no new config mechanism). A config
     read fault keeps the OFF default (fail-closed to legacy).

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

  * The VALIDATED belief authority runs at the seam. The spine builds the ONE
    predictive distribution via the REAL ``PredictiveDistributionBuilder`` —
    ``build_center`` (envelope-locked) + ``build_sigma`` (realized-floor) — over the
    reactor's chain-of-record-DEBIASED members (threaded under
    ``_edli_spine_debiased_members_native`` by the Stage-0 producer). This is the
    ARM-replay-validated center+σ (center PROVEN, σ honest std(z)=0.93; see
    docs/rebuild/arm_replay_report.md), NOT the reactor's legacy served mu*/σ. The
    reactor's served mu*/σ are the LEGACY EMOS/replacement values being replaced — they
    are no longer used for belief. De-bias is a no-op at the seam (``_NoOpDebiasAuthority``):
    the chain-of-record per-model de-bias already ran upstream (the single correct
    de-bias; the contaminating EDLI lane is OFF), and the reactor does not thread the
    member provenance the real ``DebiasAuthority`` would need to safely re-run here —
    so the seam applies NO further shift (no double-de-bias). Wiring the full
    ``DebiasAuthority`` on RAW members with provenance is a follow-up that only changes
    behavior where a per-city artifact would diverge from the already-validated
    chain-of-record debias. If the reactor served no debiased members at all (the
    threaded inputs are absent — a genuine reconstruction gap), the bridge returns a
    TYPED no-trade (``SPINE_INPUTS_UNAVAILABLE``) rather than fabricating a center.

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
from dataclasses import dataclass
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
from src.forecast.predictive_distribution_builder import (
    PredictiveDistribution,
    PredictiveDistributionBuilder,
)
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import (
    ResolutionError,
    event_resolution_for_city,
)
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

# The joint-q band draw count the engine uses for the coherent ΔU band. The engine's
# own default is 4000 (the validated production width). ``None`` means "use the engine
# default"; a test may set this to a smaller value to keep the smoke fast. This is the
# ONLY tunable that affects the band width; it never changes the selection LOGIC, only
# the Monte-Carlo resolution of the robust edge lower bound.
SPINE_BAND_DRAWS: Optional[int] = None


# ===========================================================================
# (1) The single cutover flag accessor.
# ===========================================================================

def qkernel_spine_enabled() -> bool:
    """The single Wave-5B cutover/rollback flag (default False).

    Read from ``settings["feature_flags"]["qkernel_spine_enabled"]`` using the SAME
    accessor the other reactor feature flags use (e.g. the replacement-authority
    flag reads ``settings["feature_flags"][...]``). A config read fault keeps the OFF
    default — fail-closed to the legacy decision path. When False, the reactor's
    legacy per-family decision path is byte-for-byte unchanged and this bridge is
    never on the decision path.
    """
    try:
        from src.config import settings

        return bool(settings["feature_flags"].get("qkernel_spine_enabled", False))
    except Exception:  # noqa: BLE001 — fail-closed to legacy on any config fault
        return False


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
    decision_time: datetime,
) -> ForecastCase:
    """Build the spine ``ForecastCase`` from the reactor family + decision time.

    Resolves the versioned ``EventResolution`` via the live
    ``event_resolution_for_city`` (the SAME per-city settlement identity the q layer
    threads). Raises ``ResolutionError`` (fail-closed) if the city cannot be
    resolved to a settlement station — the caller turns that into a typed no-trade.
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

    issue = decision_time if decision_time.tzinfo else decision_time.replace(tzinfo=timezone.utc)
    return ForecastCase(
        city=resolution.city,
        city_id=str(getattr(city, "name", resolution.city)),
        station_id=resolution.station_id,
        settlement_source_type=resolution.settlement_source_type,
        target_local_date=target_local_date,
        metric=metric,  # type: ignore[arg-type]
        issue_time_utc=issue,
        lead_hours=0.0,
        season="",
        regime_key="",
        unit=resolution.measurement_unit,
        resolution=resolution,
        family_id=str(getattr(family, "family_id", "")),
        source_cycle_time_utc=issue,
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


def _served_predictive_inputs(payload: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Lift the reactor's served predictive center/dispersion/members from the payload.

    The Stage-0 producer stashed ``_edli_spine_mu_native`` / ``_edli_spine_sigma_native``
    / ``_edli_spine_debiased_members_native`` (and the raw members / q vector) on the
    THREADED payload at the single point where they were all in scope. These are the
    reactor's ALREADY-COMPUTED, ARM-validated center/width/envelope — the q the spine
    integrates is built over the SAME N(mu*, sigma). Returns ``None`` when the served
    predictive inputs are genuinely absent (the caller emits a typed no-trade rather
    than fabricating a center).
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
    return {
        "mu_native": mu_f,
        "sigma_native": sigma_f,
        "debiased_members_native": members,
        "raw_members_native": raw_members,
    }


def build_fresh_model_set(
    case: ForecastCase, served: Mapping[str, Any]
) -> FreshModelSet:
    """Build a ``FreshModelSet`` from the reactor's CHAIN-OF-RECORD-DEBIASED members.

    The values are the reactor's served ``debiased_members_native`` — the members AFTER
    the reactor's chain-of-record per-model de-bias (the single correct de-bias;
    diagnosis: the +1.2 chain-debias is correct, and the contaminating EDLI per-city
    lane is OFF upstream). The VALIDATED ``build_center`` (envelope-lock) and
    ``build_sigma`` (realized-floor) authorities then run on THESE debiased members —
    identical to the ARM-replay-validated path — with a no-op de-bias at the seam (see
    ``_NoOpDebiasAuthority``: no double-de-bias, no missing de-bias). Falls back to the
    raw array, then to ``mu_native``, only if no debiased array was threaded.
    """
    values = served.get("debiased_members_native") or served.get("raw_members_native") or ()
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        arr = np.asarray([float(served["mu_native"])], dtype=float)
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
    """De-bias is a NO-OP at this live seam — the SINGLE correct de-bias already ran.

    The reactor's chain-of-record per-model de-bias is the one correct de-bias
    (diagnosis: the +1.2 chain-debias is correct; the contaminating EDLI per-city lane
    is OFF upstream). The members threaded here (``debiased_members_native``) are ALREADY
    that debiased set, so the seam applies NO further shift. Re-running the real
    ``DebiasAuthority`` here would need member provenance the reactor does not thread to
    the seam (it would either no-op on synthetic provenance or, worse, double-de-bias on
    a city/metric artifact match). The VALIDATED ``build_center`` (envelope-lock) and
    ``build_sigma`` (realized-floor) authorities STILL run on these debiased members —
    identical to the ARM-replay-validated belief — they just do not re-de-bias. (Wiring
    the full ``DebiasAuthority`` on RAW members with real provenance is a follow-up that
    only changes behavior where a per-city artifact would diverge from the chain-of-record
    debias the diagnosis already validated as correct.)
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


class _NoDay0Reader:
    """A ``Day0Reader`` that serves no observation (the reactor's forecast lane).

    The forecast decision lane has no day0 observed extreme at this seam; the spine's
    predictive builder treats ``None`` as the inactive (NO_DAY0) identity transform.
    A day0-scope wiring is a follow-up; this bridge serves the forecast lane.
    """

    def read(self, case: ForecastCase) -> Optional[Day0ObservationState]:  # noqa: ARG002
        return None


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
    for proof in proofs:
        try:
            candidate = native_side_candidate_from_proof(family_key=family_key, proof=proof)
        except Exception:  # noqa: BLE001 — a non-materializable proof is simply absent
            continue
        bin_id = candidate_bin_id(proof)
        direction = str(getattr(proof, "direction", "") or "")
        side = "YES" if direction == "buy_yes" else ("NO" if direction == "buy_no" else None)
        if side is None:
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
    out: dict[tuple[str, str], Any] = {}
    for proof in proofs:
        direction = str(getattr(proof, "direction", "") or "")
        side = "YES" if direction == "buy_yes" else ("NO" if direction == "buy_no" else None)
        if side is None:
            continue
        out[(candidate_bin_id(proof), side)] = proof
    return out


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


# ===========================================================================
# The bridge entry point.
# ===========================================================================

def decide_family_via_spine(
    *,
    family: Any,
    payload: Mapping[str, Any],
    proofs: Sequence[Any],
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
        proofs: the per-candidate ``_CandidateProof`` tuple already materialized for
            the submission pipeline (carries rows/execution_price/native costs).
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
    served = _served_predictive_inputs(payload)
    if served is None:
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=NO_TRADE_SPINE_INPUTS_UNAVAILABLE,
            decision=None,
        )

    try:
        case = build_forecast_case(family, decision_time=decision_time)
        omega = build_outcome_space(family, case)
        models = build_fresh_model_set(case, served)
        sizing_candidates = _sizing_candidates_from_proofs(
            family_key=family_key,
            proofs=proofs,
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
        engine = FamilyDecisionEngine(
            fresh_model_reader=_ReactorServedFreshModelReader(models),
            day0_reader=_NoDay0Reader(),
            # The VALIDATED belief authority: build_center (envelope-lock) + build_sigma
            # (realized-floor) run on the reactor's chain-of-record-debiased members —
            # the ARM-replay-validated center+σ, NOT the reactor's legacy served mu/σ.
            # De-bias is a no-op here (already applied upstream; see _NoOpDebiasAuthority).
            predictive_builder=PredictiveDistributionBuilder(_NoOpDebiasAuthority()),
            # Inject a family_book_builder that assembles the FamilyBook DIRECTLY from
            # the reactor proofs' native ladders (the SAME books the reactor priced
            # each proof against) — bypassing ExecutableMarketSnapshot reconstruction.
            family_book_builder=_family_book_builder_from_proofs(proofs, candidate_bin_id),
            **_engine_kwargs,
        )
        captured_at_utc = decision_time if decision_time.tzinfo else decision_time.replace(tzinfo=timezone.utc)
        # The route surface is priced at a FEASIBLE share size — the family's venue min
        # order size (the smallest executable order). Pricing the routes at the engine
        # default of 1 share would mark every route non-executable on a book whose min
        # order is >1 (the NO_EXECUTABLE_ROUTE_CANDIDATE false no-trade). The min order
        # is read off the proofs' rows (probability units); default to a safe 5.
        shares_for_routing = _family_min_order_shares(proofs)
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
        return SpineDecisionResult(
            selected_proof=None,
            no_trade_reason=decision.no_trade_reason or NO_TRADE_SPINE_NO_SELECTION,
            decision=decision,
        )

    proof_index = _proof_by_bin_side(proofs, candidate_bin_id)
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

    overlaid = _overlay_spine_economics_onto_proof(selected_proof, decision)
    return SpineDecisionResult(
        selected_proof=overlaid,
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


def _overlay_spine_economics_onto_proof(proof: Any, decision: FamilyDecision) -> Any:
    """Overlay the spine decision's economics onto the selected reactor proof.

    The submission pipeline reads ``q_posterior`` / ``q_lcb_5pct`` / ``trade_score`` /
    ``execution_price`` etc. off the proof. The spine's selection is the authority now,
    so the receipt-facing q / edge / trade_score are restamped from the spine's
    selected ``CandidateEconomics`` (point fair value, robust edge_lcb, optimal ΔU).
    The executable identity (row / token / execution_price / native_quote_available)
    is LEFT UNCHANGED — the spine selected this exact executable leg, and the submit
    pipeline re-authorizes it at submit time. Returns a NEW proof (frozen dataclass
    replace) so the original tuple is untouched.
    """
    from dataclasses import replace

    selected = decision.selected
    if selected is None:
        return proof
    # The spine's point fair value (q @ payoff) is the decision's q for this leg; its
    # edge_lcb is the robust lower bound; optimal_delta_u is the ΔU. Restamp the
    # receipt-facing fields; keep the executable identity.
    try:
        new_q = float(selected.q_dot_payoff)
    except Exception:  # noqa: BLE001
        new_q = float(getattr(proof, "q_posterior", 0.0))
    try:
        new_trade_score = float(selected.point_ev)
    except Exception:  # noqa: BLE001
        new_trade_score = float(getattr(proof, "trade_score", 0.0))
    overlay: dict[str, Any] = {
        "q_posterior": new_q,
        "trade_score": new_trade_score,
        "q_source": "qkernel_spine",
    }
    # q_lcb_5pct: the spine's robust edge lower bound is an edge (q-price), not a bare
    # q_lcb; keep the proof's own q_lcb_5pct (its robust q lower bound) so the
    # q_lcb>price capital-efficiency receipt field stays a probability, while the
    # spine's edge_lcb>0 selection guarantee is the gate that already fired.
    try:
        return replace(proof, **overlay)
    except Exception:  # noqa: BLE001 — if the proof is not a replaceable dataclass, return as-is
        return proof
