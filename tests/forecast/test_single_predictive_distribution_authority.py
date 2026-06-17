# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/forecast/predictive_distribution_builder.py" block lines 344-365:
#   the PredictiveDistribution dataclass; the [BLOCKER] forecast-authority-split fix
#   lines 23, 28 — enforce mu* in [min,max] debiased members unless a day0 observed
#   extreme licenses leaving; the sigma-missing fallback lines 426-430; Stage 3 block
#   lines 1072-1090). Spec-named RED-on-revert tests:
#     test_every_live_path_returns_same_receipt_contract
#     test_mu_star_cannot_select_tokyo_26_when_fresh_members_are_20_to_23
#     test_pd_live_eligible_false_when_sigma_authority_missing
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY; this is the only live builder; wired to the reactor later).
"""RED-on-revert tests for the single predictive-distribution authority (Stage 3/PD).

Each test fails if the corrected ASSEMBLY is reverted to the broken behavior the
spec replaces:

  * ``test_every_live_path_returns_same_receipt_contract`` — fails if any live path
    (eligible NORMAL, eligible day0-conditioned, σ-missing-ineligible, REFUSED
    center) omits a receipt field. The pre-rebuild reactor had an EMOS lane and a
    fallback/day0 lane that produced DIFFERENT mu*/σ/q semantics and DIFFERENT
    receipts (spec lines 23, 28). The corrected builder returns ONE
    ``PredictiveDistribution`` with an ``identity_hash`` and every provenance
    sub-object populated on EVERY path. Reverting to a lane that returns a bare
    value (no center/debias/day0/sigma_components, no identity_hash) fails the
    contract assertions.

  * ``test_mu_star_cannot_select_tokyo_26_when_fresh_members_are_20_to_23`` — the
    headline defect: fails if the served μ* can be 26°C while the fresh debiased
    members are 20-23°C and NO observed extreme licenses leaving the envelope.
    Reverting the assembly so EMOS/EDLI output becomes μ directly (bypassing the
    envelope-enforced center, or letting a non-observed value open the envelope)
    yields 26; the corrected assembly makes 26 unreachable absent a day0 observed
    extreme — and reachable ONLY toward an actually-observed extreme.

  * ``test_pd_live_eligible_false_when_sigma_authority_missing`` — fails if the
    builder serves a live-eligible distribution when the σ authority is missing
    (no fusion capture AND no realized floor). Reverting to a soft-anchor lane that
    serves member-vote q without a σ makes the distribution live-eligible with a
    fabricated/absent σ; the corrected builder returns ``live_eligible=False`` with
    ``ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING"``.
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields
from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

import numpy as np
import pytest

import src.forecast.center as center_mod
import src.forecast.sigma_authority as sa
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.debias_authority import BiasArtifact, DebiasAuthority
from src.forecast.predictive_distribution_builder import (
    PredictiveDistribution,
    PredictiveDistributionBuilder,
    build_predictive_distribution,
)
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import EventResolution, SEMANTICS_VERSION


# ---------------------------------------------------------------------------
# Fixtures: a Tokyo (WU °C, wmo_half_up) ForecastCase + a fresh member set. Shares
# the vocabulary of test_center_envelope.py / test_sigma_authority.py so the three
# Stage-3/PD/5 modules assemble against one fixture shape.
# ---------------------------------------------------------------------------

ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
PRODUCT_HASH = "modelset_tokyo_high_v1"
STATION_MAPPING = "RJTT_wu_icao"
REALIZED_FLOOR_C = 2.2  # a real cell with a wide realized settlement floor


def _resolution(*, unit: str = "C", metric: str = "high") -> EventResolution:
    return EventResolution(
        city="Tokyo",
        station_id=STATION,
        settlement_source_type="wu_icao",
        resolution_source=f"WU_{STATION}",
        target_local_date=date(2026, 6, 14),
        settlement_timezone="Asia/Tokyo",
        metric=metric,  # type: ignore[arg-type]
        measurement_unit=unit,  # type: ignore[arg-type]
        settlement_step_native=1.0,
        precision=1.0,
        rounding_rule="wmo_half_up",
        finalization_local_time=time(12, 0, 0),
        semantics_version=SEMANTICS_VERSION,
    )


def _case(*, unit: str = "C", metric: str = "high", lead_hours: float = 6.0) -> ForecastCase:
    """A day0 case (lead < 24h) so day0 conditioning is meaningful."""
    return ForecastCase(
        city="Tokyo",
        city_id="tokyo",
        station_id=STATION,
        settlement_source_type="wu_icao",
        target_local_date=date(2026, 6, 14),
        metric=metric,  # type: ignore[arg-type]
        issue_time_utc=ISSUE,
        lead_hours=lead_hours,
        season="summer",
        regime_key="zonal",
        unit=unit,  # type: ignore[arg-type]
        resolution=_resolution(unit=unit, metric=metric),
        family_id="tokyo_high_2026-06-14",
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


def _model_set(values_native: Sequence[float], *, case: Optional[ForecastCase] = None) -> FreshModelSet:
    case = case or _case()
    model_ids = [f"m{i}" for i in range(len(values_native))]
    members = tuple(_member(mid, v) for mid, v in zip(model_ids, values_native))
    arr = np.asarray(values_native, dtype=float)
    return FreshModelSet(
        case=case,
        members=members,
        member_values_native=arr,
        min_native=float(arr.min()) if arr.size else float("nan"),
        max_native=float(arr.max()) if arr.size else float("nan"),
        model_set_hash=PRODUCT_HASH,
    )


def _obs_high(observed_high: float) -> Day0ObservationState:
    """An active day0 observation state with a resolved running HIGH extreme."""
    return Day0ObservationState(
        observed=True,
        station_id=STATION,
        source="metar_wu",
        samples_count=14,
        latest_observed_at_utc=ISSUE + timedelta(hours=5),
        observed_high_native=observed_high,
        observed_low_native=None,
        observed_extreme_native=observed_high,
        raw_observation_hash="obs_hash_high",
    )


def _no_obs() -> Day0ObservationState:
    return Day0ObservationState(
        observed=False,
        station_id=STATION,
        source="none",
        samples_count=0,
        latest_observed_at_utc=None,
        observed_high_native=None,
        observed_low_native=None,
        observed_extreme_native=None,
        raw_observation_hash=None,
    )


def _pin_realized_floor(monkeypatch, floor_c: Optional[float]) -> None:
    """Pin the realized settlement σ-floor the sigma authority reads.

    ``None`` -> no realized floor for the cell (drives the σ-missing path when
    combined with ``has_fusion_capture=False`` and a zeroed global floor)."""
    monkeypatch.setattr(sa, "settlement_sigma_floor", lambda *a, **k: floor_c)


def _no_emos(monkeypatch) -> None:
    """Make EMOS absent so the center is the pure in-envelope debiased consensus.

    Patches the name bound in ``center`` (it imports ``emos_predictive`` directly).
    With EMOS absent the served center is exactly the robust debiased consensus,
    which is in-envelope by construction.
    """
    monkeypatch.setattr(center_mod, "emos_predictive", lambda *a, **k: None)


def _force_emos(monkeypatch, mu_emos: float) -> None:
    """Force EMOS to return a fixed (pathological) mean at full shrink strength.

    Drives the broken-design scenario: EMOS hands back a value (e.g. 26) the old
    lane would have served as μ directly. The corrected assembly must refuse it
    (envelope fallback) absent a day0 observed extreme.
    """
    monkeypatch.setattr(center_mod, "_emos_oos_strength", lambda case: 1.0)
    monkeypatch.setattr(center_mod, "emos_predictive", lambda *a, **k: (float(mu_emos), 1.0))


# ---------------------------------------------------------------------------
# Receipt-contract helper: the full field set EVERY PredictiveDistribution carries.
# ---------------------------------------------------------------------------

_PD_FIELDS = {f.name for f in dataclass_fields(PredictiveDistribution)}


def _assert_full_receipt_contract(pd: PredictiveDistribution) -> None:
    """Assert the predictive distribution carries the COMPLETE receipt contract.

    The same set of populated fields on EVERY path — eligible or not. A reverted
    lane that returns a bare value or omits a provenance sub-object fails here.
    """
    # The dataclass shape itself (spec lines 348-365) is the contract.
    assert _PD_FIELDS == {
        "case",
        "mu_native",
        "sigma_native",
        "debiased_members_native",
        "member_min_native",
        "member_max_native",
        "center",
        "debias",
        "day0",
        "sigma_components",
        "distribution_family",
        "live_eligible",
        "ineligibility_reason",
        "identity_hash",
    }
    # identity_hash is ALWAYS present and non-empty (the receipt anchor).
    assert isinstance(pd.identity_hash, str) and len(pd.identity_hash) == 64
    # Every provenance sub-object is populated (never None) regardless of outcome.
    assert pd.center is not None
    assert pd.debias is not None
    assert pd.day0 is not None
    assert pd.sigma_components is not None
    assert pd.distribution_family in ("NORMAL", "DAY0_HIGH_MAX_NORMAL", "DAY0_LOW_MIN_NORMAL")
    assert isinstance(pd.debiased_members_native, tuple)
    assert isinstance(pd.live_eligible, bool)
    # The eligibility flag and its reason are coherent.
    if pd.live_eligible:
        assert pd.ineligibility_reason is None
    else:
        assert isinstance(pd.ineligibility_reason, str) and pd.ineligibility_reason


# ---------------------------------------------------------------------------
# Test 1 (spec RED-on-revert) — every live path returns the SAME receipt contract.
# ---------------------------------------------------------------------------

def test_every_live_path_returns_same_receipt_contract(monkeypatch):
    _no_emos(monkeypatch)
    _pin_realized_floor(monkeypatch, REALIZED_FLOOR_C)

    members = [20.0, 21.0, 22.0, 23.0]
    authority = DebiasAuthority(())  # no shift; debiased == raw, envelope [20, 23]
    builder = PredictiveDistributionBuilder(authority)

    # PATH A — eligible, bare NORMAL (no day0 observation).
    pd_normal = builder.build(_case(), _model_set(members), _no_obs(), has_fusion_capture=True)
    _assert_full_receipt_contract(pd_normal)
    assert pd_normal.live_eligible is True
    assert pd_normal.distribution_family == "NORMAL"
    assert pd_normal.day0.active is False

    # PATH B — eligible, day0-conditioned (observed high INSIDE the envelope so the
    # center does not leave it; the family flips to the settlement-conditioned form).
    pd_day0 = builder.build(_case(), _model_set(members), _obs_high(22.5), has_fusion_capture=True)
    _assert_full_receipt_contract(pd_day0)
    assert pd_day0.live_eligible is True
    assert pd_day0.distribution_family == "DAY0_HIGH_MAX_NORMAL"
    assert pd_day0.day0.active is True

    # PATH C — INELIGIBLE: σ authority missing (no fusion capture, no realized floor).
    _pin_realized_floor(monkeypatch, None)
    monkeypatch.setattr(sa, "global_lead_bucket_floor", lambda case: 0.0)
    pd_no_sigma = builder.build(_case(), _model_set(members), _no_obs(), has_fusion_capture=False)
    _assert_full_receipt_contract(pd_no_sigma)
    assert pd_no_sigma.live_eligible is False
    assert pd_no_sigma.ineligibility_reason == "PREDICTIVE_SIGMA_AUTHORITY_MISSING"

    # PATH D — INELIGIBLE: REFUSED center (no fresh members). Even with no members,
    # the SAME receipt contract is returned (identity_hash + sub-objects present).
    _pin_realized_floor(monkeypatch, REALIZED_FLOOR_C)
    pd_refused = builder.build(_case(), _model_set([]), _no_obs(), has_fusion_capture=True)
    _assert_full_receipt_contract(pd_refused)
    assert pd_refused.live_eligible is False
    assert pd_refused.center.center_status == "REFUSED"
    assert "CENTER_REFUSED" in (pd_refused.ineligibility_reason or "")

    # ALL FOUR paths share the SAME contract field set AND every one has a 64-char
    # identity_hash — the single unified receipt the rebuild requires (spec line 9:
    # "the same receipt contract"). Distinct content -> distinct hashes.
    hashes = {
        pd_normal.identity_hash,
        pd_day0.identity_hash,
        pd_no_sigma.identity_hash,
        pd_refused.identity_hash,
    }
    assert len(hashes) == 4  # different content, all hashed under the same contract


# ---------------------------------------------------------------------------
# Test 2 (spec RED-on-revert) — μ* cannot select Tokyo 26 when members are 20-23.
# ---------------------------------------------------------------------------

def test_mu_star_cannot_select_tokyo_26_when_fresh_members_are_20_to_23(monkeypatch):
    _pin_realized_floor(monkeypatch, REALIZED_FLOOR_C)

    # Every fresh model member for Tokyo high sits in 20-23°C — the headline scenario.
    members = [20.0, 20.7, 21.4, 22.1, 22.6, 23.0]
    authority = DebiasAuthority(())  # debiased == raw; envelope [20, 23]

    # The BROKEN design: EMOS (or a stale EDLI mean shift) hands back 26.0 at full
    # shrink strength — the value the old lane served as μ directly.
    _force_emos(monkeypatch, 26.0)

    # --- NO day0 observation: 26 is unreachable, μ* stays in [20, 23] ----------
    pd = build_predictive_distribution(_case(), _model_set(members), authority, _no_obs(), has_fusion_capture=True)
    assert 20.0 <= pd.mu_native <= 23.0
    assert pd.mu_native != pytest.approx(26.0)
    assert not (pd.mu_native >= 24.0)  # nowhere near the broken 26
    # The served μ* IS inside the debiased member envelope (the [BLOCKER] invariant).
    assert pd.member_min_native <= pd.mu_native <= pd.member_max_native
    assert (pd.member_min_native, pd.member_max_native) == pytest.approx((20.0, 23.0))
    # The center authority fell back to the in-envelope consensus (EMOS left the
    # envelope); day0 is inactive so it did not (and could not) re-open it.
    assert pd.center.center_status == "ENVELOPE_FALLBACK"
    assert pd.day0.active is False
    assert pd.distribution_family == "NORMAL"

    # --- A day0 observed extreme is the ONLY thing that can license leaving -----
    # Even then it can only move μ* TOWARD the actually-observed value, never to the
    # unobserved 26 that EMOS invented. Observe a running high of 24.0: μ* clamps UP
    # to 24.0 (the observed extreme), NOT 26.0.
    pd_obs = build_predictive_distribution(
        _case(), _model_set(members), authority, _obs_high(24.0), has_fusion_capture=True
    )
    assert pd_obs.mu_native == pytest.approx(24.0)  # the OBSERVED extreme, not 26
    assert pd_obs.mu_native != pytest.approx(26.0)
    assert pd_obs.day0.active is True
    assert pd_obs.day0.status == "HIGH_CLAMPED"
    assert pd_obs.distribution_family == "DAY0_HIGH_MAX_NORMAL"
    # The leave-envelope license is bounded by the observation: μ* never exceeds the
    # observed extreme just because EMOS wanted 26.
    assert pd_obs.mu_native <= 24.0 + 1e-9

    # --- A stale, oversized EDLI-style mean shift cannot smuggle 26 in either ---
    # A −4.847 magnitude claim against a small realized band is MAGNITUDE_REFUSED by
    # DebiasAuthority, so it never shifts the members; the consensus stays in 20-23.
    stale_big = BiasArtifact(
        artifact_id="art_minus_4847",
        authority="SETTLEMENT_STATION_WALK_FORWARD_V1",
        city="Tokyo",
        station_id=STATION,
        metric="high",
        season="summer",
        regime_key="zonal",
        lead_bucket="day0",
        product_set_hash=PRODUCT_HASH,
        model_id=None,
        training_start_utc=ISSUE - timedelta(days=120),
        training_cutoff_utc=ISSUE - timedelta(hours=12),
        valid_until_utc=ISSUE + timedelta(days=2),
        n=200,
        residual_mean_native=-0.33,      # realized band center
        residual_std_native=0.40,
        residual_se_native=0.03,
        proposed_shift_native=-4.847,    # pathological claim, far outside the band
        oos_crps_before=0.50,
        oos_crps_after=0.48,
        oos_logscore_before=None,
        oos_logscore_after=None,
        station_mapping_id=STATION_MAPPING,
        source_hash="src_v1",
    )
    pd_stale = build_predictive_distribution(
        _case(), _model_set(members), DebiasAuthority((stale_big,)), _no_obs(), has_fusion_capture=True
    )
    assert 20.0 <= pd_stale.mu_native <= 23.0
    assert pd_stale.mu_native != pytest.approx(26.0)
    assert "MAGNITUDE_REFUSED" in pd_stale.debias.activation_status


# ---------------------------------------------------------------------------
# Test 3 (spec RED-on-revert) — live_eligible=False when σ authority is missing.
# ---------------------------------------------------------------------------

def test_pd_live_eligible_false_when_sigma_authority_missing(monkeypatch):
    _no_emos(monkeypatch)

    members = [20.0, 21.0, 22.0, 23.0]
    authority = DebiasAuthority(())

    # No realized floor for the cell AND no fusion capture furnished a predictive
    # width AND the conservative global floor is forced to 0 — the ONLY honest
    # outcome is ineligibility (spec lines 426-428). A reverted soft-anchor lane
    # would serve member-vote q at a fabricated narrow σ -> live_eligible True.
    _pin_realized_floor(monkeypatch, None)
    monkeypatch.setattr(sa, "global_lead_bucket_floor", lambda case: 0.0)

    pd = build_predictive_distribution(
        _case(), _model_set(members), authority, _no_obs(), has_fusion_capture=False
    )

    # NOT live-eligible, with the exact spec reason.
    assert pd.live_eligible is False
    assert pd.ineligibility_reason == "PREDICTIVE_SIGMA_AUTHORITY_MISSING"
    # No σ is served — the member-vote q cannot reach the served path.
    assert pd.sigma_native == 0.0
    assert pd.sigma_components.sigma_after_floor_native == 0.0
    # The receipt contract is STILL complete (ineligible is not a silent drop).
    _assert_full_receipt_contract(pd)
    assert isinstance(pd.identity_hash, str) and len(pd.identity_hash) == 64

    # CONTRAST: with fusion capture present the SAME case IS live-eligible (proves
    # the ineligibility is the σ-missing condition, not the case itself). Here the
    # realized floor is still absent, so the served σ is the composed honest RSS.
    pd_ok = build_predictive_distribution(
        _case(), _model_set(members), authority, _no_obs(), has_fusion_capture=True
    )
    assert pd_ok.live_eligible is True
    assert pd_ok.ineligibility_reason is None
    assert pd_ok.sigma_native > 0.0

    # A reverted lane that served member-vote q without a σ would have produced a
    # tight σ ≈ the raw member spread on the INELIGIBLE case. Prove the raw spread
    # is NOT what the σ-missing path serves (it serves nothing).
    raw_spread = pd.sigma_components.raw_member_spread_native
    assert raw_spread > 0.0
    assert pd.sigma_native != pytest.approx(raw_spread)
