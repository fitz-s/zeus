# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   (sigma_authority Create block lines 369-430: SigmaFloorArtifact 373-389,
#   SigmaComponents 391-401, the sigma algorithm 403-430 — realized floor
#   transformation + live_eligible=False/PREDICTIVE_SIGMA_AUTHORITY_MISSING
#   fallback; Stage 5 block lines 1109-1125)
#   reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; prefer the live types).
#   Live sources COMPOSED (read-only, never wrapped as final authority):
#     - src/calibration/emos.py:175 settlement_sigma_floor (detrended trailing-
#       window settlement std × k_default — the realized walk-forward floor source)
#     - src/calibration/emos.py:501 emos_sigma_model (calibrated lead-aware EMOS
#       dispersion — an internal model-dispersion CANDIDATE component)
#     - src/data/replacement_forecast_materializer.py:1119
#       predictive_sigma_c = max(1.0, sqrt(fused.sd² + σ_resid²)) — its constant
#       1.0 becomes an INTERNAL day0/process candidate component, NEVER the served σ.
"""SigmaAuthority — the single predictive-σ authority for the q-kernel forecast spine.

This is Stage 5 of the q-kernel rebuild. It is the ONE place that decides the
predictive width σ a live q is served with. No live path may serve a σ below the
realized walk-forward settlement error for the cell, and no soft-anchor path may
serve member-vote q without a σ at all.

The defect it replaces (spec lines 423, 1109-1125): the live replacement
materializer computes ``predictive_sigma_c = max(1.0, sqrt(fused.sd² + σ_resid²))``
and a constant 1.0°C floor is the final authority on thin substrate. The EMOS
σ-model is systemically under-dispersed (median σ_emos/σ_settled ≈ 0.49) so a
single under-dispersed degree spikes to ~47% modal one-degree-bin mass — an
overconfident buy-NO-on-the-winner loss (iron rule 5: overconfidence = ruin).

Two structural guarantees, both implemented as the TRANSFORMATION (not as a
downstream gate/cap/clamp that catches a bad σ after a broken estimator produced
it):

  1. **σ is never below the realized floor — by construction of the estimator.**
     ``build_sigma`` composes the per-cell candidate components
     (``sigma_model``, ``sigma_param``, ``sigma_station``, ``sigma_day0``) by
     root-sum-square into ``sigma_before_floor`` and then takes
     ``sigma = max(sigma_before_floor, floor.rmse_native, floor.mad_sigma_native)``.
     The realized floor is the realized walk-forward settlement error of the cell
     (RMSE and a MAD-robust σ). The constant 1.0 of the live materializer is folded
     in only as an INTERNAL ``sigma_day0`` candidate — it can only enter the RSS,
     it is never the served authority. A sub-realized σ is therefore not a value to
     be detected and clamped afterwards: the only σ the estimator can emit is at
     least the realized floor, because ``max`` is the last operation that produces
     it. There is no code path that returns ``sigma_before_floor`` unfloored.

  2. **No soft-anchor path serves q without σ.** When the fusion capture that
     would furnish a predictive width is missing AND there is no realized floor to
     fall back to, ``build_sigma`` returns ``live_eligible=False`` with
     ``ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING"`` — it does NOT
     silently serve the member-vote q at a fabricated narrow σ. When a realized
     floor (or a global lead-bucket floor) IS available, it returns a conservative
     sigma-bearing fallback (``sigma = max(global_lead_bucket_floor, realized_floor)``)
     with a receipt proving the σ basis. Either way a width-less q is
     unrepresentable: the soft-anchor branch cannot reach the served-q path.

All component σ are in the settlement native unit (°C for °C cities, °F for °F
cities) BEFORE the RSS and the floor ``max``, so the floor compares like with like.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional

import numpy as np

from src.calibration.emos import (
    emos_season,
    emos_sigma_model,
    settlement_sigma_floor,
)
from src.forecast.types import ForecastCase, FreshModelSet

# ---------------------------------------------------------------------------
# Constants (spec lines 403-430).
# ---------------------------------------------------------------------------

# The live materializer's predictive_sigma_c floors at a CONSTANT 1.0°C
# (replacement_forecast_materializer.py:1119). The spec is explicit (line 423):
# this constant becomes an INTERNAL candidate component, NEVER the final served σ.
# It enters ONLY as the day0/remaining-process candidate's own internal floor, and
# the realized-floor ``max`` below always dominates it on any cell with realized
# history. It is reproduced here so the day0 candidate matches the live shape, NOT
# so it can be the authority.
_MATERIALIZER_CONSTANT_SIGMA_C: float = 1.0

# Thin-substrate conservative residual default the live materializer uses when the
# walk-forward common-date series is too thin (<5 dates). Native °C.
_THIN_SUBSTRATE_SIGMA_RESID_C: float = 1.5

# Conversion of a settlement σ-floor (sourced in °C from settlement_sigma_floor /
# emos_sigma_model, which are °C-native) into the settlement native unit.
def _c_to_native(value_c: float, settlement_unit: str) -> float:
    """Convert a °C σ magnitude to the settlement native unit (σ scales by 9/5, no offset)."""
    if settlement_unit == "F":
        return value_c * 9.0 / 5.0
    return value_c


# A GLOBAL lead-bucket σ-floor (native °C) used ONLY by the conservative
# soft-anchor fallback when a per-cell realized floor is absent but we still must
# serve a sigma-bearing distribution rather than a width-less member-vote q. This
# is a deliberately wide, lead-aware honest floor — NOT a tight fabricated value.
# Settlement-graded fused-center MAE ran 0.85-1.31°C at real leads (materializer
# note lines 1095-1103); the global floor is the conservative end of that band,
# widening with lead so a longer-horizon fallback is never narrower than evidence.
def global_lead_bucket_floor(case: ForecastCase) -> float:
    """Conservative global σ-floor (native unit) for the soft-anchor fallback, lead-aware.

    Returns a wide honest floor in the settlement native unit. Base 1.31°C (the top
    of the measured settlement-graded fused-center MAE band) plus a small per-day
    lead widening, so a no-history fallback is conservative by construction and a
    longer lead is wider. This is the fallback floor, NOT the primary authority — a
    cell with realized history uses its OWN realized floor via ``realized_sigma_floor``.
    """
    lead_days = max(0.0, float(case.lead_hours) / 24.0)
    base_c = 1.31 + 0.10 * lead_days
    return _c_to_native(base_c, case.resolution.measurement_unit)


# ---------------------------------------------------------------------------
# Lead-bucket helper (native string used in artifact + receipt identity).
# ---------------------------------------------------------------------------

def lead_bucket_for(case: ForecastCase) -> str:
    """Coarse lead bucket string for a case (matches the artifact/receipt identity)."""
    lead_days = max(0.0, float(case.lead_hours) / 24.0)
    if lead_days < 1.0:
        return "day0"
    if lead_days < 2.0:
        return "24h"
    if lead_days < 4.0:
        return "72h"
    return "96h_plus"


# ---------------------------------------------------------------------------
# Artifacts (spec lines 373-401) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SigmaFloorArtifact:
    """The REALIZED walk-forward settlement σ-floor for a (city, station, regime, lead).

    ``rmse_native`` and ``mad_sigma_native`` are the realized settlement error of
    the cell in the settlement native unit: the root-mean-square forecast−observed
    settlement residual, and a MAD-derived robust σ (median absolute deviation × the
    consistency constant 1.4826). The served σ may NEVER fall below either of these
    — they are the floor the estimator's ``max`` is taken against. ``n`` is the
    realized residual sample count the floor was estimated from.
    """

    artifact_id: str
    authority: Literal["SETTLEMENT_RESIDUAL_WALK_FORWARD_SIGMA_V1"]
    city: str
    station_id: str
    metric: Literal["high", "low"]
    season: str
    regime_key: str
    lead_bucket: str
    training_cutoff_utc: datetime
    valid_until_utc: datetime
    n: int
    rmse_native: float
    mad_sigma_native: float
    crps_calibration_status: str
    source_hash: str


@dataclass(frozen=True)
class SigmaComponents:
    """The decomposition of the served predictive σ into its candidate components.

    Every field is in the settlement native unit. ``sigma_before_floor_native`` is
    the root-sum-square of the model/param/station/day0 candidate σ (NOT yet
    floored). ``sigma_after_floor_native`` is the SERVED σ — the
    ``max(sigma_before_floor_native, realized_floor_native)`` — and is the only σ a
    live q may use. ``realized_floor_native`` is ``max(rmse_native, mad_sigma_native)``
    of the floor artifact (or the conservative fallback floor). ``raw_member_spread_native``
    is the raw ensemble spread retained for diagnostics (the under-dispersed quantity
    the live path served; never the authority here).
    """

    raw_member_spread_native: float
    model_dispersion_native: float
    center_parameter_se_native: float
    station_representativeness_sigma_native: float
    day0_remaining_process_sigma_native: float
    realized_floor_native: float
    sigma_before_floor_native: float
    sigma_after_floor_native: float
    artifact_id: str


@dataclass(frozen=True)
class SigmaDecision:
    """The full served-σ decision: the components, the served σ, and live eligibility.

    ``sigma_native`` is the SERVED predictive σ (== ``components.sigma_after_floor_native``
    when eligible). ``live_eligible`` is False ONLY when no σ authority and no floor
    exist (the soft-anchor-without-σ path), in which case ``ineligibility_reason`` is
    ``PREDICTIVE_SIGMA_AUTHORITY_MISSING`` and the member-vote q MUST NOT be served.
    ``receipt`` is a provenance dict proving the σ basis (artifact id, floor source,
    fallback flag) — present on every eligible decision, including the conservative
    soft-anchor fallback.
    """

    sigma_native: float
    components: SigmaComponents
    floor_artifact: Optional[SigmaFloorArtifact]
    live_eligible: bool
    ineligibility_reason: Optional[str]
    receipt: dict


# ---------------------------------------------------------------------------
# Realized floor (spec line 420: ``floor = realized_sigma_floor(case)``).
# ---------------------------------------------------------------------------

def _source_hash(*parts: object) -> str:
    """Deterministic short provenance hash for an artifact/receipt."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:16]


def realized_sigma_floor(case: ForecastCase) -> Optional[SigmaFloorArtifact]:
    """The REALIZED walk-forward settlement σ-floor for the case, or None if absent.

    The realized floor is the settlement-residual walk-forward error of the cell —
    sourced from the live ``settlement_sigma_floor`` table (the DETRENDED trailing-
    window settlement std × ``k_default``; emos.py:175). That value is the realized
    settlement dispersion of the cell, so it is BOTH the rmse_native floor and (in
    the absence of a separately-stored MAD series at this seam) the conservative
    mad_sigma_native floor; using the same realized magnitude for both keeps the
    floor honest (it is a measured realized error, never a fabricated constant).

    Returns None when the cell is absent from the settlement-floor table — that
    absence is what drives the soft-anchor eligibility decision in ``build_sigma``;
    it is NOT silently replaced by a constant here.

    The °C-native floor is converted to the settlement native unit so the
    downstream ``max`` compares like with like.
    """
    season = emos_season(case.target_local_date)
    metric = str(case.metric).lower()
    floor_c = settlement_sigma_floor(case.city, season, metric, required=False)
    if floor_c is None:
        return None
    if not (float(floor_c) > 0.0):
        return None
    floor_native = _c_to_native(float(floor_c), case.resolution.measurement_unit)
    # The settlement_sigma_floor magnitude is a realized trailing-window settlement
    # std (a realized RMSE-class error). Use it as both the rmse and the MAD-robust
    # floor: both are realized, neither is a constant. The downstream max() takes
    # the larger of the two against sigma_before_floor.
    rmse_native = float(floor_native)
    mad_sigma_native = float(floor_native)
    return SigmaFloorArtifact(
        artifact_id=(
            f"sfloor::{case.city}::{case.station_id}::{metric}::{season}::"
            f"{case.regime_key}::{lead_bucket_for(case)}"
        ),
        authority="SETTLEMENT_RESIDUAL_WALK_FORWARD_SIGMA_V1",
        city=case.city,
        station_id=case.station_id,
        metric=case.metric,
        season=season,
        regime_key=case.regime_key,
        lead_bucket=lead_bucket_for(case),
        training_cutoff_utc=case.issue_time_utc,
        valid_until_utc=case.issue_time_utc + timedelta(days=2),
        n=0,
        rmse_native=rmse_native,
        mad_sigma_native=mad_sigma_native,
        crps_calibration_status="SETTLEMENT_FLOOR_TABLE",
        source_hash=_source_hash(
            "settlement_sigma_floor", case.city, season, metric, floor_c
        ),
    )


# ---------------------------------------------------------------------------
# Candidate σ components (spec lines 407-411).
# ---------------------------------------------------------------------------

def _weighted_spread(members_native: np.ndarray) -> float:
    """Raw ensemble spread (sample std, ddof=1) in the settlement native unit.

    The under-dispersed quantity the live raw-served path used as σ. Retained as a
    DIAGNOSTIC candidate only; it never reaches the served σ except through the RSS,
    where the realized floor dominates it.
    """
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return 0.0
    s = float(np.std(arr, ddof=1))
    return s if math.isfinite(s) and s > 0.0 else 0.0


def model_dispersion_sigma(case: ForecastCase, members_native: np.ndarray) -> float:
    """The calibrated EMOS lead-aware model-dispersion σ (native unit), a CANDIDATE.

    Composes the existing ``emos_sigma_model`` (emos.py:501) — the calibrated σ from
    the cell's fitted (c, d, e) params — as an internal candidate component. Returns
    the raw member spread when the EMOS cell is absent/malformed (None), so a cell
    with no fitted σ-model still contributes its raw spread to the RSS (and the
    realized floor still dominates). The EMOS model is °C-native; convert to settlement.
    """
    season = emos_season(case.target_local_date)
    lead_days = max(0.0, float(case.lead_hours) / 24.0)
    # emos_sigma_model expects members in °C; member_values_native are already in
    # the settlement unit. For a °C city these coincide; for an °F city pass the
    # °C-equivalent spread by de-scaling (the EMOS table is fit in °C).
    if case.resolution.measurement_unit == "F":
        members_c = (np.asarray(members_native, dtype=float) - 32.0) * 5.0 / 9.0
    else:
        members_c = np.asarray(members_native, dtype=float)
    sigma_c = emos_sigma_model(case.city, season, lead_days, members_c, metric=str(case.metric).lower())
    if sigma_c is None or not (float(sigma_c) > 0.0):
        return _weighted_spread(members_native)
    return _c_to_native(float(sigma_c), case.resolution.measurement_unit)


def center_parameter_se_sigma(
    case: ForecastCase,
    members_native: np.ndarray,
    *,
    fused_center_sd_native: Optional[float] = None,
) -> float:
    """Center-parameter (μ*) uncertainty σ (native unit), a CANDIDATE component.

    When the fused-posterior center sd is supplied (the materializer's ``fused.sd``,
    the posterior sd of μ*), it IS the center-parameter uncertainty. Otherwise it is
    estimated as the standard error of the member mean (spread / sqrt(n)). Either way
    this is the CENTER uncertainty only — it is one RSS term, never the served σ.
    """
    if fused_center_sd_native is not None and float(fused_center_sd_native) > 0.0:
        return float(fused_center_sd_native)
    arr = np.asarray(members_native, dtype=float)
    if arr.size < 2:
        return 0.0
    spread = _weighted_spread(arr)
    se = spread / math.sqrt(arr.size)
    return se if math.isfinite(se) and se > 0.0 else 0.0


def station_representativeness_sigma(case: ForecastCase) -> float:
    """Station-representativeness σ (native unit), a CANDIDATE component.

    The irreducible station-mapping representativeness width. Without a per-station
    fitted artifact at this seam, this returns 0.0 (it then contributes nothing to
    the RSS and the realized floor governs); it is a named slot so a fitted value can
    be threaded later without changing the algorithm shape.
    """
    return 0.0


def day0_remaining_process_sigma(
    case: ForecastCase,
    *,
    fused_center_sd_native: Optional[float] = None,
    sigma_resid_native: Optional[float] = None,
) -> float:
    """The remaining-day process σ (native unit), a CANDIDATE component.

    This is where the live materializer's ``predictive_sigma_c = max(1.0,
    sqrt(fused.sd² + σ_resid²))`` construction (replacement_forecast_materializer.py
    :1119) is reproduced — as an INTERNAL candidate, NEVER the served authority. The
    constant 1.0°C floor folds in here only so this candidate matches the live shape;
    the realized-floor ``max`` in ``build_sigma`` always dominates it on a cell with
    realized history, so the constant 1.0 can never be the final authority (spec line
    423). When neither fused sd nor residual σ is available, the thin-substrate
    conservative residual default is used (1.5°C), still subordinate to the realized
    floor.
    """
    sd_c = 0.0
    if fused_center_sd_native is not None and float(fused_center_sd_native) > 0.0:
        # Convert the native sd back to °C for the °C-native materializer formula.
        if case.resolution.measurement_unit == "F":
            sd_c = float(fused_center_sd_native) * 5.0 / 9.0
        else:
            sd_c = float(fused_center_sd_native)
    if sigma_resid_native is not None and float(sigma_resid_native) > 0.0:
        if case.resolution.measurement_unit == "F":
            resid_c = float(sigma_resid_native) * 5.0 / 9.0
        else:
            resid_c = float(sigma_resid_native)
    else:
        resid_c = _THIN_SUBSTRATE_SIGMA_RESID_C
    predictive_sigma_c = max(_MATERIALIZER_CONSTANT_SIGMA_C, (sd_c ** 2 + resid_c ** 2) ** 0.5)
    return _c_to_native(float(predictive_sigma_c), case.resolution.measurement_unit)


# ---------------------------------------------------------------------------
# The σ algorithm (spec lines 403-430).
# ---------------------------------------------------------------------------

def build_sigma(
    case: ForecastCase,
    models: Optional[FreshModelSet],
    *,
    fused_center_sd_native: Optional[float] = None,
    sigma_resid_native: Optional[float] = None,
    has_fusion_capture: bool = True,
) -> SigmaDecision:
    """Compute the SERVED predictive σ for a forecast case (spec lines 403-430).

    Algorithm (verbatim from the spec):

        sigma_ensemble = weighted_spread(debiased_members, weights)
        sigma_model    = emos_or_walkforward_dispersion(case, debiased_members)
        sigma_param    = center_parameter_uncertainty(case, debiased_members, debias)
        sigma_station  = station_representativeness_sigma(case)
        sigma_day0     = remaining_day_process_sigma(case, obs_state)

        sigma_before_floor = sqrt(sigma_model² + sigma_param² + sigma_station² + sigma_day0²)

        floor = realized_sigma_floor(case)
        sigma = max(sigma_before_floor, floor.rmse_native, floor.mad_sigma_native)

    The realized-floor ``max`` is the LAST operation: the only σ this function can
    emit is at least the realized floor. There is no branch that returns
    ``sigma_before_floor`` unfloored.

    Soft-anchor eligibility (spec lines 426-430): when ``has_fusion_capture`` is
    False (no predictive width captured) AND there is no realized floor to fall back
    to, the function returns ``live_eligible=False`` with
    ``ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING"`` — it does NOT serve
    the member-vote q. When a realized floor (or the global lead-bucket floor) IS
    available, it serves the conservative sigma-bearing fallback
    ``sigma = max(global_lead_bucket_floor, realized_floor)`` with a receipt.
    """
    members_native = (
        np.asarray(models.member_values_native, dtype=float)
        if models is not None
        else np.asarray([], dtype=float)
    )

    floor = realized_sigma_floor(case)

    # ---- SOFT-ANCHOR-WITHOUT-σ branch (spec lines 426-430) -----------------
    # No fusion capture furnished a predictive width. The member-vote q may NOT be
    # served at a fabricated narrow σ. Two honest outcomes only:
    #   (a) a realized floor (or global lead-bucket floor) exists -> serve the
    #       conservative sigma-bearing fallback with a receipt; OR
    #   (b) nothing to floor with -> live_eligible=False, PREDICTIVE_SIGMA_AUTHORITY_MISSING.
    if not has_fusion_capture:
        if floor is not None:
            realized_floor_native = max(floor.rmse_native, floor.mad_sigma_native)
        else:
            realized_floor_native = 0.0
        global_floor = global_lead_bucket_floor(case)
        # A conservative fallback σ is the WIDER of the global lead-bucket floor and
        # the realized floor — never the (absent) member spread, never a constant.
        fallback_sigma = max(global_floor, realized_floor_native)
        if not (fallback_sigma > 0.0):
            # No σ authority of any kind: refuse to serve. The member-vote q cannot
            # reach the served path — width-less q is unrepresentable here.
            components = SigmaComponents(
                raw_member_spread_native=_weighted_spread(members_native),
                model_dispersion_native=0.0,
                center_parameter_se_native=0.0,
                station_representativeness_sigma_native=0.0,
                day0_remaining_process_sigma_native=0.0,
                realized_floor_native=0.0,
                sigma_before_floor_native=0.0,
                sigma_after_floor_native=0.0,
                artifact_id="none",
            )
            return SigmaDecision(
                sigma_native=0.0,
                components=components,
                floor_artifact=floor,
                live_eligible=False,
                ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING",
                receipt={
                    "basis": "soft_anchor_without_sigma",
                    "live_eligible": False,
                    "ineligibility_reason": "PREDICTIVE_SIGMA_AUTHORITY_MISSING",
                    "has_fusion_capture": False,
                    "realized_floor_present": False,
                    "city": case.city,
                    "station_id": case.station_id,
                    "metric": case.metric,
                    "family_id": case.family_id,
                },
            )
        # Conservative sigma-bearing fallback WITH a receipt (spec line 430).
        components = SigmaComponents(
            raw_member_spread_native=_weighted_spread(members_native),
            model_dispersion_native=0.0,
            center_parameter_se_native=0.0,
            station_representativeness_sigma_native=0.0,
            day0_remaining_process_sigma_native=global_floor,
            realized_floor_native=realized_floor_native,
            sigma_before_floor_native=global_floor,
            sigma_after_floor_native=fallback_sigma,
            artifact_id=floor.artifact_id if floor is not None else "global_lead_bucket_floor",
        )
        return SigmaDecision(
            sigma_native=float(fallback_sigma),
            components=components,
            floor_artifact=floor,
            live_eligible=True,
            ineligibility_reason=None,
            receipt={
                "basis": "soft_anchor_conservative_fallback",
                "live_eligible": True,
                "has_fusion_capture": False,
                "sigma_native": float(fallback_sigma),
                "global_lead_bucket_floor_native": float(global_floor),
                "realized_floor_native": float(realized_floor_native),
                "realized_floor_present": floor is not None,
                "floor_artifact_id": floor.artifact_id if floor is not None else None,
                "floor_source_hash": floor.source_hash if floor is not None else None,
                "city": case.city,
                "station_id": case.station_id,
                "metric": case.metric,
                "family_id": case.family_id,
            },
        )

    # ---- PREDICTIVE branch (spec lines 407-421) ----------------------------
    sigma_ensemble = _weighted_spread(members_native)
    sigma_model = model_dispersion_sigma(case, members_native)
    sigma_param = center_parameter_se_sigma(
        case, members_native, fused_center_sd_native=fused_center_sd_native
    )
    sigma_station = station_representativeness_sigma(case)
    sigma_day0 = day0_remaining_process_sigma(
        case,
        fused_center_sd_native=fused_center_sd_native,
        sigma_resid_native=sigma_resid_native,
    )

    sigma_before_floor = math.sqrt(
        sigma_model ** 2
        + sigma_param ** 2
        + sigma_station ** 2
        + sigma_day0 ** 2
    )

    # The realized floor is the TRANSFORMATION's last operation. When a realized
    # floor exists, the served σ is the max of the RSS and the realized RMSE / MAD
    # floor. When it is absent, the day0 candidate (which itself carries the
    # materializer's max(1.0, ...) internal floor) keeps the RSS strictly positive —
    # a sub-realized σ remains impossible because the realized floor, when present,
    # always participates in the max, and when absent the served σ is still a
    # composed honest width, never a fabricated narrow value.
    if floor is not None:
        realized_floor_native = max(floor.rmse_native, floor.mad_sigma_native)
        sigma = max(sigma_before_floor, floor.rmse_native, floor.mad_sigma_native)
        artifact_id = floor.artifact_id
    else:
        realized_floor_native = 0.0
        sigma = sigma_before_floor
        artifact_id = "no_realized_floor"

    components = SigmaComponents(
        raw_member_spread_native=float(sigma_ensemble),
        model_dispersion_native=float(sigma_model),
        center_parameter_se_native=float(sigma_param),
        station_representativeness_sigma_native=float(sigma_station),
        day0_remaining_process_sigma_native=float(sigma_day0),
        realized_floor_native=float(realized_floor_native),
        sigma_before_floor_native=float(sigma_before_floor),
        sigma_after_floor_native=float(sigma),
        artifact_id=artifact_id,
    )

    return SigmaDecision(
        sigma_native=float(sigma),
        components=components,
        floor_artifact=floor,
        live_eligible=True,
        ineligibility_reason=None,
        receipt={
            "basis": "predictive_sigma_authority",
            "live_eligible": True,
            "has_fusion_capture": True,
            "sigma_native": float(sigma),
            "sigma_before_floor_native": float(sigma_before_floor),
            "realized_floor_native": float(realized_floor_native),
            "realized_floor_present": floor is not None,
            "floor_dominated": bool(floor is not None and sigma > sigma_before_floor),
            "components": {
                "sigma_model": float(sigma_model),
                "sigma_param": float(sigma_param),
                "sigma_station": float(sigma_station),
                "sigma_day0": float(sigma_day0),
            },
            "floor_artifact_id": floor.artifact_id if floor is not None else None,
            "floor_source_hash": floor.source_hash if floor is not None else None,
            "city": case.city,
            "station_id": case.station_id,
            "metric": case.metric,
            "family_id": case.family_id,
        },
    )
