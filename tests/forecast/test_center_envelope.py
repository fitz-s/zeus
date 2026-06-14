# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md Stage 3 (lines 1072-1090
#   RED-on-revert names) + the "Create src/forecast/center.py" block (lines 220-270:
#   CenterEstimate, the center algorithm, and the envelope-enforcement code 256-268).
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY; the envelope is a TRANSFORMATION — mu_candidate is constructed
#   so min(debiased)<=mu<=max(debiased) holds by construction, ENVELOPE_FALLBACK to
#   the in-envelope debiased consensus when EMOS proposes outside; absent day0 the
#   center MUST stay inside the debiased member envelope).
"""RED-on-revert tests for the forecast center builder (q-kernel rebuild Stage 3).

Each test fails if the corrected TRANSFORMATION is reverted to the broken behavior
the spec replaces — the design where ``build_emos_q`` let EMOS output BECOME the
live μ directly, so an EMOS slope/intercept (or a stale mean shift) could push μ to
a value (Tokyo 26°C) that NO fresh debiased member supports when every member is in
[20, 23]°C:

  * ``test_mu_star_inside_debiased_member_envelope`` — fails if the served μ* ever
    leaves ``[min(debiased), max(debiased)]``. The convex-combination consensus
    keeps it inside by construction; reverting to "EMOS center becomes μ" lets a
    biased EMOS mean escape and the bound assertion fails.

  * ``test_emos_slope_cannot_push_mu_outside_envelope`` — fails if a steep EMOS
    slope (``b`` large, intercept biased) drives μ* outside the envelope. The new
    design shrinks EMOS toward the in-envelope consensus and FALLS BACK to the
    consensus when the shrunk candidate escapes, so the slope can never push μ out.

  * ``test_tokyo_26_impossible_when_members_are_20_to_23`` — the headline defect:
    fails if μ* can be 26°C while the fresh debiased members are 20-23°C. Reverting
    the transform (EMOS/EDLI mean shift becomes μ) yields 26; the corrected
    transform makes 26 mathematically unreachable.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Sequence

import numpy as np
import pytest

from src.calibration import emos as emos_mod
from src.forecast import center as center_mod
from src.forecast.center import (
    CenterEstimate,
    build_center,
    shrink,
    walk_forward_model_weights,
    weighted_huber_location,
)
from src.forecast.debias_authority import BiasArtifact, DebiasAuthority
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import EventResolution, SEMANTICS_VERSION


# ---------------------------------------------------------------------------
# Fixtures: a Tokyo (WU °C, wmo_half_up) ForecastCase + a fresh member set, plus
# a well-formed in-band debias artifact. Mirrors test_debias_authority.py so the
# two Stage-2/Stage-3 modules share one fixture vocabulary.
# ---------------------------------------------------------------------------

ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
PRODUCT_HASH = "modelset_tokyo_high_v1"
STATION_MAPPING = "RJTT_wu_icao"


def _resolution(*, unit: str = "C") -> EventResolution:
    return EventResolution(
        city="Tokyo",
        station_id=STATION,
        settlement_source_type="wu_icao",
        resolution_source=f"WU_{STATION}",
        target_local_date=date(2026, 6, 15),
        settlement_timezone="Asia/Tokyo",
        metric="high",
        measurement_unit=unit,  # type: ignore[arg-type]
        settlement_step_native=1.0,
        precision=1.0,
        rounding_rule="wmo_half_up",
        finalization_local_time=time(12, 0, 0),
        semantics_version=SEMANTICS_VERSION,
    )


def _case(*, unit: str = "C") -> ForecastCase:
    return ForecastCase(
        city="Tokyo",
        city_id="tokyo",
        station_id=STATION,
        settlement_source_type="wu_icao",
        target_local_date=date(2026, 6, 15),
        metric="high",
        issue_time_utc=ISSUE,
        lead_hours=24.0,
        season="summer",
        regime_key="zonal",
        unit=unit,  # type: ignore[arg-type]
        resolution=_resolution(unit=unit),
        family_id="tokyo_high_2026-06-15",
        source_cycle_time_utc=ISSUE - timedelta(hours=6),
    )


def _member(model_id: str, value_native: float) -> RawModelMember:
    return RawModelMember(
        model_id=model_id,
        product_id=f"{model_id}_mx2t3",
        source_run_id=f"{model_id}_run_2026061400",
        source_cycle_time_utc=ISSUE - timedelta(hours=6),
        available_at_utc=ISSUE - timedelta(hours=1),
        value_native=value_native,
        station_mapping_id=STATION_MAPPING,
        raw_forecast_artifact_id=f"{model_id}_artifact",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
    )


def _model_set(values_native: Sequence[float], *, model_ids=None, case=None) -> FreshModelSet:
    case = case or _case()
    model_ids = model_ids or [f"m{i}" for i in range(len(values_native))]
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


def _in_band_artifact(*, residual_mean_native: float, **overrides) -> BiasArtifact:
    """A well-formed artifact whose claim equals its realized band center.

    Used to drive a NON-zero, IN-BAND de-bias so the test exercises the debiased
    envelope (not the trivial no-shift case). The shift is the realized residual
    mean (DebiasAuthority serves the band center, never proposed_shift_native).
    """
    base = dict(
        artifact_id="art_inband",
        authority="SETTLEMENT_STATION_WALK_FORWARD_V1",
        city="Tokyo",
        station_id=STATION,
        metric="high",
        season="summer",
        regime_key="zonal",
        lead_bucket="24h",
        product_set_hash=PRODUCT_HASH,
        model_id=None,
        training_start_utc=ISSUE - timedelta(days=120),
        training_cutoff_utc=ISSUE - timedelta(hours=12),
        valid_until_utc=ISSUE + timedelta(days=2),
        n=200,
        residual_mean_native=residual_mean_native,
        residual_std_native=0.40,
        residual_se_native=0.03,
        proposed_shift_native=residual_mean_native,  # agrees with realized mean
        oos_crps_before=0.50,
        oos_crps_after=0.48,
        oos_logscore_before=None,
        oos_logscore_after=None,
        station_mapping_id=STATION_MAPPING,
        source_hash="src_v1",
    )
    base.update(overrides)
    return BiasArtifact(**base)  # type: ignore[arg-type]


def _no_emos_monkeypatch(monkeypatch, mu_emos):
    """Force emos_predictive (as imported into center) to return a fixed mean.

    Returns ``(mu_emos, sigma)`` regardless of the cell table so a test can inject a
    pathological EMOS center WITHOUT depending on the live calibration artifact.
    Patches the name bound in ``center`` (it imports ``emos_predictive`` directly).
    """
    def _fake(city, season, lead_days, members_c, *, metric="high"):
        if mu_emos is None:
            return None
        return (float(mu_emos), 1.0)

    monkeypatch.setattr(center_mod, "emos_predictive", _fake)


# ---------------------------------------------------------------------------
# Helper-level proofs: the convex-combination property the invariant rests on.
# ---------------------------------------------------------------------------

def test_weighted_huber_location_is_inside_value_hull():
    # A robust weighted location is a convex combination of the inputs, so it can
    # never exceed the extremes — including with one wild outlier member.
    values = [20.0, 21.0, 22.0, 23.0, 99.0]  # 99 is an outlier model
    weights = walk_forward_model_weights(_case(), _model_set(values).members)
    mu = weighted_huber_location(values, weights)
    assert min(values) <= mu <= max(values)
    # Robust: the outlier does NOT drag the center to the high tail; it stays in
    # the 20-23 bulk (well below the non-robust weighted mean of ~37).
    assert mu <= 23.0 + 1e-9


def test_shrink_is_convex_combination_when_strength_in_unit_interval():
    # shrink(value, toward, s) = (1-s)*toward + s*value: at s=0 it IS toward, at
    # s=1 it IS value, and in between it stays between them.
    assert shrink(100.0, toward=21.0, strength=0.0) == pytest.approx(21.0)
    assert shrink(100.0, toward=21.0, strength=1.0) == pytest.approx(100.0)
    mid = shrink(100.0, toward=21.0, strength=0.5)
    assert 21.0 <= mid <= 100.0


# ---------------------------------------------------------------------------
# Test 1 (spec RED-on-revert) — mu* inside the debiased member envelope.
# ---------------------------------------------------------------------------

def test_mu_star_inside_debiased_member_envelope(monkeypatch):
    case = _case()
    members_native = [20.5, 21.0, 21.5, 22.0, 23.0]
    models = _model_set(members_native)

    # Drive a real, in-band de-bias of −0.5°C so the debiased members are the raw
    # members + 0.5 (DebiasAuthority subtracts the residual mean). The debiased
    # envelope is therefore [21.0, 23.5].
    authority = DebiasAuthority((_in_band_artifact(residual_mean_native=-0.5),))

    # Inject a biased EMOS center far ABOVE the envelope; with the default OOS
    # shrink strength (0.0) EMOS gets zero weight, but even a non-zero strength
    # cannot push μ* out (proved in test 2). Here we assert the invariant holds.
    _no_emos_monkeypatch(monkeypatch, mu_emos=40.0)

    est = build_center(case, models, authority)
    assert isinstance(est, CenterEstimate)

    lo = est.debiased_member_min_native
    hi = est.debiased_member_max_native
    # The debiased envelope is the raw envelope shifted by +0.5.
    assert lo == pytest.approx(21.0)
    assert hi == pytest.approx(23.5)
    # THE INVARIANT: μ* is inside the debiased member envelope.
    assert lo <= est.mu_native <= hi
    # The de-bias actually fired (non-trivial debiased envelope), so the test is
    # exercising the real path, not the no-shift trivial case.
    assert "APPLIED" in est.reason

    # Sanity: with no debias artifact at all, μ* still sits in the RAW envelope.
    bare = build_center(case, models, DebiasAuthority(()))
    assert bare.debiased_member_min_native == pytest.approx(20.5)
    assert bare.debiased_member_max_native == pytest.approx(23.0)
    assert bare.debiased_member_min_native <= bare.mu_native <= bare.debiased_member_max_native


# ---------------------------------------------------------------------------
# Test 2 (spec RED-on-revert) — an EMOS slope cannot push μ outside the envelope.
# ---------------------------------------------------------------------------

def test_emos_slope_cannot_push_mu_outside_envelope(monkeypatch):
    case = _case()
    members_native = [20.0, 21.0, 22.0, 23.0]
    models = _model_set(members_native)
    authority = DebiasAuthority(())  # no shift; debiased == raw, envelope [20, 23]

    # A pathological EMOS center produced by a steep slope + biased intercept:
    # mu_emos = a + b*xbar with xbar≈21.5 -> e.g. a=5, b=1.0 -> 26.5, well ABOVE 23.
    # Force a NON-ZERO OOS shrink strength so EMOS genuinely tries to move μ.
    monkeypatch.setattr(center_mod, "_emos_oos_strength", lambda case: 1.0)
    _no_emos_monkeypatch(monkeypatch, mu_emos=26.5)

    est = build_center(case, models, authority)

    lo, hi = est.debiased_member_min_native, est.debiased_member_max_native
    assert (lo, hi) == pytest.approx((20.0, 23.0))
    # Even at full EMOS strength, the shrunk candidate (26.5) is OUTSIDE [20, 23],
    # so the envelope transform FALLS BACK to the in-envelope debiased consensus.
    assert est.center_status == "ENVELOPE_FALLBACK"
    assert est.center_method == "WEIGHTED_HUBER_CONSENSUS"
    assert lo <= est.mu_native <= hi
    # The served μ is the consensus, NOT the EMOS 26.5 it tried to become.
    assert est.mu_native == pytest.approx(est.debiased_consensus_native)
    assert est.mu_native < 26.5

    # A steep slope pushing BELOW the envelope is equally refused (symmetry).
    _no_emos_monkeypatch(monkeypatch, mu_emos=10.0)
    est_low = build_center(case, models, authority)
    assert est_low.center_status == "ENVELOPE_FALLBACK"
    assert est_low.debiased_member_min_native <= est_low.mu_native <= est_low.debiased_member_max_native
    assert est_low.mu_native > 10.0

    # CONTROL: an EMOS center INSIDE the envelope is honored as SHRUNK_EMOS (the
    # transform does not needlessly fall back on a valid in-envelope EMOS μ).
    _no_emos_monkeypatch(monkeypatch, mu_emos=21.7)
    est_ok = build_center(case, models, authority)
    assert est_ok.center_status == "OK"
    assert est_ok.center_method == "SHRUNK_EMOS"
    assert est_ok.mu_native == pytest.approx(21.7)


# ---------------------------------------------------------------------------
# Test 3 (spec RED-on-revert) — Tokyo 26°C impossible when members are 20-23°C.
# ---------------------------------------------------------------------------

def test_tokyo_26_impossible_when_members_are_20_to_23(monkeypatch):
    case = _case()
    # The exact headline scenario: every fresh model member for Tokyo high sits in
    # 20-23°C. A 26°C center is what the BROKEN transform produced (EMOS / stale
    # EDLI mean shift becoming μ directly).
    members_native = [20.0, 20.7, 21.4, 22.1, 22.6, 23.0]
    models = _model_set(members_native)
    authority = DebiasAuthority(())  # debiased == raw; envelope [20, 23]

    # Make EMOS hand back 26.0 at FULL shrink strength — the very value the old
    # design served. The corrected transform must make 26 unreachable.
    monkeypatch.setattr(center_mod, "_emos_oos_strength", lambda case: 1.0)
    _no_emos_monkeypatch(monkeypatch, mu_emos=26.0)

    est = build_center(case, models, authority)

    # μ* is in [20, 23], NOT 26. The 26 the broken transform produced is
    # mathematically unreachable: the only value served when EMOS leaves the
    # envelope is the in-envelope robust consensus.
    assert 20.0 <= est.mu_native <= 23.0
    assert est.mu_native != pytest.approx(26.0)
    assert not (est.mu_native >= 24.0)  # nowhere near the broken 26
    assert est.center_status == "ENVELOPE_FALLBACK"
    assert est.mu_native == pytest.approx(est.debiased_consensus_native)

    # And a stale, oversized EDLI-style mean shift cannot smuggle 26 in either: a
    # −4.847 magnitude claim against a small realized band is MAGNITUDE_REFUSED by
    # DebiasAuthority, so it never shifts the members, and the consensus stays in
    # 20-23 regardless. (Same defect class the Stage-2 module kills, re-proved at
    # the center seam.)
    stale_big = _in_band_artifact(
        artifact_id="art_minus_4847",
        residual_mean_native=-0.33,      # realized band center
        proposed_shift_native=-4.847,    # pathological claim, far outside the band
    )
    est2 = build_center(case, _model_set(members_native), DebiasAuthority((stale_big,)))
    assert 20.0 <= est2.mu_native <= 23.0
    assert "MAGNITUDE_REFUSED" in est2.reason
    assert est2.mu_native != pytest.approx(26.0)
