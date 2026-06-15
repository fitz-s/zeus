# Created: 2026-06-14
# Last reused or audited: 2026-06-15 (ARM-replay width recalibration)
# Authority basis: docs/rebuild/consult_build_spec.md Stage 5 (lines 1109-1125
#   RED-on-revert names + live signal) + sigma_authority Create block (lines
#   369-430) + docs/rebuild/arm_replay_report.md (2026-06-15, n=693): the served σ
#   is ANCHORED to the realized walk-forward floor (the honest settlement-validated
#   width), NOT max(sigma_before_floor, floor) — the RSS over-disperses ~1.94x by
#   double-counting modeled uncertainty on top of an already-complete realized
#   error. The RSS is retained as the thin/new-cell fallback (floor None) only, and
#   the soft-anchor-without-sigma live_eligible=False / PREDICTIVE_SIGMA_AUTHORITY_MISSING
#   fallback is unchanged.
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY; the constant-1.0 floor and member-vote-q-without-sigma are the
#   broken behaviors these tests forbid).
"""RED-on-revert tests for SigmaAuthority (q-kernel rebuild Stage 5).

Each test fails if the corrected TRANSFORMATION is reverted to the broken behavior
the spec replaces:

  * ``test_sigma_never_below_realized_floor_on_emos_raw_replacement_day0`` — fails
    if the served σ falls below the realized walk-forward settlement floor. The live
    path (replacement_forecast_materializer.py:1119) served
    ``predictive_sigma_c = max(1.0, sqrt(fused.sd² + σ_resid²))`` — a CONSTANT 1.0°C
    is the final authority on a thin/raw-replacement day0 cell whose calibrated EMOS
    σ is under-dispersed (~0.6°C), so the served σ sat BELOW the cell's realized
    settlement error and the modal one-degree bin spiked to ~47%. Reverting the
    estimator to serve ``sigma_before_floor`` (or the constant 1.0) instead of
    ``max(sigma_before_floor, floor.rmse_native, floor.mad_sigma_native)`` makes the
    served σ drop below the realized floor and the assertion fails.

  * ``test_soft_anchor_without_sigma_is_not_live_eligible`` — fails if the
    soft-anchor (no-fusion-capture, no-realized-floor) path silently serves the
    member-vote q without a σ. The corrected transform returns
    ``live_eligible=False`` with ``ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING"``
    (or a sigma-bearing conservative fallback WITH a receipt). Reverting to the
    soft-anchor path that serves member-vote q at a fabricated narrow σ makes the
    decision live-eligible with a sub-floor σ and the assertions fail.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, time

import numpy as np
import pytest

import src.forecast.sigma_authority as sa
from src.forecast.sigma_authority import (
    SigmaDecision,
    SigmaFloorArtifact,
    build_sigma,
    realized_sigma_floor,
)
from src.forecast.types import ForecastCase, RawModelMember, FreshModelSet
from src.probability.event_resolution import EventResolution, SEMANTICS_VERSION


# ---------------------------------------------------------------------------
# Fixtures: a Tokyo (WU °C, wmo_half_up) day0 ForecastCase + a TIGHT raw member
# set (the under-dispersed raw-replacement spread the live path served as σ).
# ---------------------------------------------------------------------------

ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
PRODUCT_HASH = "modelset_tokyo_high_v1"
STATION_MAPPING = "RJTT_wu_icao"


def _resolution(*, station_id: str = STATION, unit: str = "C", rule: str = "wmo_half_up") -> EventResolution:
    return EventResolution(
        city="Tokyo",
        station_id=station_id,
        settlement_source_type="wu_icao",
        resolution_source=f"WU_{station_id}",
        target_local_date=date(2026, 6, 14),
        settlement_timezone="Asia/Tokyo",
        metric="high",
        measurement_unit=unit,  # type: ignore[arg-type]
        settlement_step_native=1.0,
        precision=1.0,
        rounding_rule=rule,  # type: ignore[arg-type]
        finalization_local_time=time(12, 0, 0),
        semantics_version=SEMANTICS_VERSION,
    )


def _case(*, station_id: str = STATION, unit: str = "C", lead_hours: float = 6.0) -> ForecastCase:
    """A day0 case (lead < 24h) so lead_bucket_for == 'day0' (the raw-replacement day0 cell)."""
    return ForecastCase(
        city="Tokyo",
        city_id="tokyo",
        station_id=station_id,
        settlement_source_type="wu_icao",
        target_local_date=date(2026, 6, 14),
        metric="high",
        issue_time_utc=ISSUE,
        lead_hours=lead_hours,
        season="summer",
        regime_key="zonal",
        unit=unit,  # type: ignore[arg-type]
        resolution=_resolution(station_id=station_id, unit=unit),
        family_id="tokyo_high_2026-06-14",
        source_cycle_time_utc=ISSUE - timedelta(hours=6),
    )


def _member(model_id: str, value_c: float, *, mapping: str = STATION_MAPPING) -> RawModelMember:
    return RawModelMember(
        model_id=model_id,
        product_id=f"{model_id}_mx2t3",
        source_run_id=f"{model_id}_run_2026061400",
        source_cycle_time_utc=ISSUE - timedelta(hours=6),
        available_at_utc=ISSUE - timedelta(hours=1),
        value_native=value_c,
        station_mapping_id=mapping,
        raw_forecast_artifact_id=f"{model_id}_artifact",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
    )


def _model_set(values_native, *, case=None) -> FreshModelSet:
    case = case or _case()
    model_ids = [f"m{i}" for i in range(len(values_native))]
    members = tuple(_member(mid, v) for mid, v in zip(model_ids, values_native))
    arr = np.asarray(values_native, dtype=float)
    return FreshModelSet(
        case=case,
        members=members,
        member_values_native=arr,
        min_native=float(arr.min()),
        max_native=float(arr.max()),
        model_set_hash=PRODUCT_HASH,
    )


# ---------------------------------------------------------------------------
# Test 1 — the served σ is NEVER below the realized walk-forward settlement floor,
# on an EMOS-raw / replacement / day0 cell whose raw spread is under-dispersed.
# ---------------------------------------------------------------------------

def test_sigma_never_below_realized_floor_on_emos_raw_replacement_day0(monkeypatch):
    case = _case()
    # A TIGHT raw member set: spread ≈ 0.5°C, the under-dispersed raw-replacement
    # σ the live path served. Its RSS of candidate σ (model/param/station/day0) is
    # dominated by the day0 candidate's internal max(1.0, ...) constant — i.e.
    # sigma_before_floor sits around the 1.0°C constant the spec forbids as authority.
    models = _model_set([29.7, 30.0, 30.3])

    # The REALIZED walk-forward settlement floor for this (city, season, metric)
    # cell is WIDER than the under-dispersed raw/EMOS spread AND wider than the
    # constant 1.0 the live materializer would serve. settlement_sigma_floor returns
    # k·σ_settled (°C); inject a realized floor of 2.4°C (a real cell where settled
    # error is large) so the floor is the binding authority.
    REALIZED_FLOOR_C = 2.4

    def _fake_settlement_sigma_floor(city, season, metric, *, required=False):
        assert city == "Tokyo"
        return REALIZED_FLOOR_C

    monkeypatch.setattr(sa, "settlement_sigma_floor", _fake_settlement_sigma_floor)
    # Also pin the EMOS σ-model to a deliberately UNDER-DISPERSED value (~0.6°C,
    # the median σ_emos that drives the spike) so without the realized floor the
    # served σ would be tight.
    monkeypatch.setattr(sa, "emos_sigma_model", lambda *a, **k: 0.6)

    floor = realized_sigma_floor(case)
    assert floor is not None
    assert floor.authority == "SETTLEMENT_RESIDUAL_WALK_FORWARD_SIGMA_V1"
    # The floor magnitudes are the realized error in the native unit (°C here).
    assert floor.rmse_native == pytest.approx(REALIZED_FLOOR_C)
    assert floor.mad_sigma_native == pytest.approx(REALIZED_FLOOR_C)

    decision: SigmaDecision = build_sigma(case, models, has_fusion_capture=True)

    realized_floor_native = max(floor.rmse_native, floor.mad_sigma_native)

    # THE INVARIANT (spec line 1112: "No sub-realized σ"): the served σ is at least
    # the realized walk-forward settlement floor. A reverted estimator that served
    # sigma_before_floor (or the constant 1.0) would emit a σ BELOW realized_floor.
    assert decision.live_eligible is True
    assert decision.sigma_native >= realized_floor_native - 1e-9
    assert decision.components.sigma_after_floor_native >= realized_floor_native - 1e-9

    # The realized floor is the BINDING authority here (it strictly exceeds the
    # under-dispersed RSS). The served σ equals the realized floor, NOT the constant
    # 1.0 and NOT the under-dispersed sigma_before_floor.
    assert decision.components.sigma_before_floor_native < realized_floor_native
    assert decision.sigma_native == pytest.approx(realized_floor_native)
    assert decision.receipt["realized_floor_anchored"] is True

    # A constant 1.0 is NEVER the final authority (spec line 423): the served σ here
    # is 2.4, not 1.0, and the served σ is strictly greater than the materializer's
    # constant-1.0 floor. The day0 candidate itself carries the materializer's
    # max(1.0, sqrt(fused.sd²+σ_resid²)) construction (here 1.5°C via the thin-
    # substrate residual default) as an INTERNAL component — at least the constant
    # 1.0, never below it, and always subordinate to the realized floor.
    assert decision.sigma_native > sa._MATERIALIZER_CONSTANT_SIGMA_C
    assert (
        decision.components.day0_remaining_process_sigma_native
        >= sa._MATERIALIZER_CONSTANT_SIGMA_C - 1e-9
    )
    assert decision.components.day0_remaining_process_sigma_native < realized_floor_native

    # DIRECT REVERT GUARD: the broken transform served sigma_before_floor (the RSS
    # that bottoms out at the constant 1.0). Prove the served σ is strictly above it,
    # so reverting to "sigma = sigma_before_floor" (drop the realized-floor max)
    # makes sigma_native == sigma_before_floor < realized_floor and this fails.
    assert decision.sigma_native > decision.components.sigma_before_floor_native


# ---------------------------------------------------------------------------
# Test 1b — the ARM-replay width recalibration (2026-06-15): when the component
# RSS is WIDER than the realized walk-forward floor, the served σ is the realized
# floor (the honest settlement-validated width), NOT the over-dispersed RSS.
# ---------------------------------------------------------------------------

def test_served_sigma_anchors_to_realized_floor_not_inflated_rss(monkeypatch):
    """RED-on-revert for the ARM-replay fix. The offline settlement replay
    (docs/rebuild/arm_replay_report.md, n=693) proved the served predictive-RSS σ
    over-disperses ~1.94x (σ/realized-RMSE=1.94, std(z)=0.52) while the realized
    floor is honest (std(z)=0.86). The corrected transform serves the realized
    floor when present. Reverting to ``sigma = max(sigma_before_floor, floor...)``
    serves the inflated RSS (> floor) and these assertions fail."""
    case = _case()
    # A WIDE member set + a wide EMOS σ-model so the composed RSS is large — the
    # over-dispersed width the ARM replay measured.
    models = _model_set([26.0, 30.0, 34.0])
    monkeypatch.setattr(sa, "emos_sigma_model", lambda *a, **k: 3.0)
    # The realized walk-forward settlement floor for this cell is the HONEST width,
    # NARROWER than the inflated RSS.
    REALIZED_FLOOR_C = 1.6
    monkeypatch.setattr(sa, "settlement_sigma_floor", lambda *a, **k: REALIZED_FLOOR_C)

    floor = realized_sigma_floor(case)
    assert floor is not None
    realized_floor_native = max(floor.rmse_native, floor.mad_sigma_native)

    decision: SigmaDecision = build_sigma(case, models, has_fusion_capture=True)

    # The composed RSS is strictly WIDER than the realized floor (the over-dispersion).
    assert decision.components.sigma_before_floor_native > realized_floor_native
    # THE FIX: the served σ is the realized floor, NOT the inflated RSS.
    assert decision.sigma_native == pytest.approx(realized_floor_native)
    assert decision.sigma_native < decision.components.sigma_before_floor_native
    # Still >= realized floor (no under-dispersion): equality holds, never below.
    assert decision.sigma_native >= realized_floor_native - 1e-9
    assert decision.live_eligible is True
    assert decision.receipt["basis"] == "realized_floor_anchored"
    assert decision.receipt["realized_floor_anchored"] is True
    assert decision.receipt["rss_over_realized_ratio"] > 1.0


# ---------------------------------------------------------------------------
# Test 2 — a soft-anchor path with no captured σ and no realized floor is NOT
# live-eligible; it must not silently serve member-vote q without a σ.
# ---------------------------------------------------------------------------

def test_soft_anchor_without_sigma_is_not_live_eligible(monkeypatch):
    case = _case()
    models = _model_set([29.7, 30.0, 30.3])

    # No realized floor exists for this cell (settlement_sigma_floor returns None)
    # AND no fusion capture furnished a predictive width. The corrected transform
    # must REFUSE to serve member-vote q at a fabricated narrow σ.
    monkeypatch.setattr(sa, "settlement_sigma_floor", lambda *a, **k: None)
    # Force the conservative global lead-bucket floor to 0 so the ONLY honest
    # outcome is ineligibility (isolates the "no σ authority at all" branch).
    monkeypatch.setattr(sa, "global_lead_bucket_floor", lambda case: 0.0)

    decision = build_sigma(case, models, has_fusion_capture=False)

    # NOT live-eligible, with the exact spec reason (spec line 428).
    assert decision.live_eligible is False
    assert decision.ineligibility_reason == "PREDICTIVE_SIGMA_AUTHORITY_MISSING"
    # No σ is served — the member-vote q cannot reach the served path.
    assert decision.sigma_native == 0.0
    assert decision.components.sigma_after_floor_native == 0.0
    assert decision.floor_artifact is None
    # The receipt proves the refusal (never a silent serve).
    assert decision.receipt["live_eligible"] is False
    assert decision.receipt["ineligibility_reason"] == "PREDICTIVE_SIGMA_AUTHORITY_MISSING"
    assert decision.receipt["has_fusion_capture"] is False

    # A reverted soft-anchor path would have served the member-vote q at the raw
    # member spread (~0.25°C) as σ → live_eligible True with a tight σ. Prove that
    # the raw spread is NOT what gets served here.
    raw_spread = decision.components.raw_member_spread_native
    assert raw_spread > 0.0  # the members DO have a (tight) spread...
    assert decision.sigma_native != pytest.approx(raw_spread)  # ...but it is NOT served.


# ---------------------------------------------------------------------------
# Companion — the sigma-bearing conservative fallback (spec line 430): when a
# realized floor (or global lead-bucket floor) exists but fusion capture is
# missing, the soft-anchor path serves a WIDE σ WITH a receipt, never a bare q.
# ---------------------------------------------------------------------------

def test_soft_anchor_with_floor_serves_conservative_sigma_with_receipt(monkeypatch):
    case = _case()
    models = _model_set([29.7, 30.0, 30.3])

    # A realized floor exists for the cell even though fusion capture is missing.
    monkeypatch.setattr(sa, "settlement_sigma_floor", lambda *a, **k: 2.0)

    decision = build_sigma(case, models, has_fusion_capture=False)

    # Live-eligible via the conservative sigma-bearing fallback, with a receipt.
    assert decision.live_eligible is True
    assert decision.ineligibility_reason is None
    # The served σ is the WIDER of the realized floor and the global lead-bucket
    # floor — and at least the realized floor (never below it).
    assert decision.sigma_native >= 2.0 - 1e-9
    assert decision.receipt["basis"] == "soft_anchor_conservative_fallback"
    assert decision.receipt["realized_floor_present"] is True
    assert decision.receipt["floor_artifact_id"] is not None


# ---------------------------------------------------------------------------
# Companion — when NO realized floor exists but fusion capture IS present, the
# served σ is the composed honest RSS (never below the day0 candidate's internal
# floor); a constant 1.0 is still never the served authority on a wider cell.
# ---------------------------------------------------------------------------

def test_predictive_sigma_is_composed_honest_width_without_realized_floor(monkeypatch):
    case = _case()
    # A wider member set so the model/param candidates push the RSS above 1.0.
    models = _model_set([27.0, 30.0, 33.0])
    monkeypatch.setattr(sa, "settlement_sigma_floor", lambda *a, **k: None)
    # EMOS σ-model present and wide (2.0°C) — a real composed candidate.
    monkeypatch.setattr(sa, "emos_sigma_model", lambda *a, **k: 2.0)

    decision = build_sigma(case, models, has_fusion_capture=True)

    assert decision.live_eligible is True
    assert decision.floor_artifact is None
    # The served σ is the RSS of the candidates (no realized floor to max against),
    # and it is strictly wider than the constant 1.0 (the wide EMOS candidate +
    # the day0 candidate's own internal floor compose a wide honest width).
    assert decision.sigma_native == pytest.approx(decision.components.sigma_before_floor_native)
    assert decision.sigma_native > sa._MATERIALIZER_CONSTANT_SIGMA_C
