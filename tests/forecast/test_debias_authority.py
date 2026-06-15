# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md Stage 2 (lines 1053-1070
#   RED-on-revert names) + DebiasAuthority Create block (lines 135-218: activation
#   rule with N_SIGMA_BIAS=2.0 and the deterministic single-shift priority order).
#   Reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY; the −4.847 artifact against a −0.33 realized band MUST be
#   MAGNITUDE_REFUSED inside the estimator; de-bias applies exactly ONCE).
"""RED-on-revert tests for DebiasAuthority (q-kernel rebuild Stage 2).

Each test fails if the corrected TRANSFORMATION is reverted to the broken
behavior the spec replaces:

  * ``test_bias_row_must_be_fresh_product_matched_station_matched`` — fails if an
    artifact that is stale / product-mismatched / station-mismatched is allowed to
    shift the members (the live EDLI path subtracted ``effective_bias_c`` keyed
    only on season/metric/data-version/authority, with NO settlement-station or
    member-product identity check). Reverting to that behavior makes the
    mismatched/stale artifact APPLY, and the status assertions fail.

  * ``test_tokyo_minus_4847_bias_refused_against_realized_residual_band`` — fails
    if the −4.847°C claimed shift against a ≈−0.33°C realized residual band is
    served instead of MAGNITUDE_REFUSED. The live path subtracted the stored bias
    unconditionally (no realized-residual band check); reverting to that makes the
    members shift by ≈−4.847 and BOTH the status and the no-shift assertions fail.

  * ``test_only_one_temperature_mean_shift_can_apply`` — fails if more than one
    correction basis can shift the center. The single-authority transform applies
    exactly ONE basis (deterministic priority) once; reverting to independent
    parallel corrections (EMOS offset + EDLI bias + grid row each shifting) makes
    the net shift exceed a single basis and the assertions fail.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, time

import numpy as np
import pytest

from src.forecast.debias_authority import (
    DebiasAuthority,
    BiasArtifact,
    N_SIGMA_BIAS,
)
from src.forecast.types import ForecastCase, RawModelMember, FreshModelSet
from src.probability.event_resolution import EventResolution, SEMANTICS_VERSION


# ---------------------------------------------------------------------------
# Fixtures: a Tokyo (WU °C, wmo_half_up) ForecastCase + a fresh member set.
# ---------------------------------------------------------------------------

ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
PRODUCT_HASH = "modelset_tokyo_high_v1"
STATION_MAPPING = "RJTT_wu_icao"


def _resolution(
    *, station_id: str = STATION, unit: str = "C", rule: str = "wmo_half_up"
) -> EventResolution:
    return EventResolution(
        city="Tokyo",
        station_id=station_id,
        settlement_source_type="wu_icao",
        resolution_source=f"WU_{station_id}",
        target_local_date=date(2026, 6, 15),
        settlement_timezone="Asia/Tokyo",
        metric="high",
        measurement_unit=unit,  # type: ignore[arg-type]
        settlement_step_native=1.0,
        precision=1.0,
        rounding_rule=rule,  # type: ignore[arg-type]
        finalization_local_time=time(12, 0, 0),
        semantics_version=SEMANTICS_VERSION,
    )


def _case(*, station_id: str = STATION, unit: str = "C") -> ForecastCase:
    return ForecastCase(
        city="Tokyo",
        city_id="tokyo",
        station_id=station_id,
        settlement_source_type="wu_icao",
        target_local_date=date(2026, 6, 15),
        metric="high",
        issue_time_utc=ISSUE,
        lead_hours=24.0,
        season="summer",
        regime_key="zonal",
        unit=unit,  # type: ignore[arg-type]
        resolution=_resolution(station_id=station_id, unit=unit),
        family_id="tokyo_high_2026-06-15",
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


def _model_set(values_c, *, model_ids=None, mapping=STATION_MAPPING, product_hash=PRODUCT_HASH, case=None):
    case = case or _case()
    model_ids = model_ids or [f"m{i}" for i in range(len(values_c))]
    members = tuple(
        _member(mid, v, mapping=mapping) for mid, v in zip(model_ids, values_c)
    )
    arr = np.asarray(values_c, dtype=float)
    return FreshModelSet(
        case=case,
        members=members,
        member_values_native=arr,
        min_native=float(arr.min()),
        max_native=float(arr.max()),
        model_set_hash=product_hash,
    )


def _artifact(**overrides) -> BiasArtifact:
    """A well-formed, applicable artifact whose claim agrees with its realized band."""
    base = dict(
        artifact_id="art_ok",
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
        training_cutoff_utc=ISSUE - timedelta(hours=12),  # fresh
        valid_until_utc=ISSUE + timedelta(days=2),
        n=200,
        residual_mean_native=-0.33,
        residual_std_native=0.40,
        residual_se_native=0.03,
        proposed_shift_native=-0.33,  # agrees with realized mean
        oos_crps_before=0.50,
        oos_crps_after=0.48,
        oos_logscore_before=None,
        oos_logscore_after=None,
        station_mapping_id=STATION_MAPPING,
        source_hash="src_v1",
    )
    base.update(overrides)
    return BiasArtifact(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Test 1 — fresh / product-matched / station-matched gating.
# ---------------------------------------------------------------------------

def test_bias_row_must_be_fresh_product_matched_station_matched():
    case = _case()
    models = _model_set([29.0, 30.0, 31.0])

    # Baseline: a fresh, product-matched, station-matched artifact APPLIES.
    ok = DebiasAuthority((_artifact(),))
    corrected, applied = ok.apply(case, models)
    assert applied.activation_status == "APPLIED"
    # The applied shift is the realized residual mean (−0.33), NOT a raw stored
    # bias and NOT zero. Members warm toward observed truth by exactly that.
    assert applied.aggregate_shift_native == pytest.approx(-0.33)
    np.testing.assert_allclose(corrected, np.asarray([29.0, 30.0, 31.0]) + 0.33)

    # STALE: a training cutoff well before the freshness window must NOT apply.
    # (Live EDLI keyed on season/metric/data-version only — no freshness gate —
    #  so reverting would APPLY this and the assertion fails.)
    stale = DebiasAuthority(
        (_artifact(artifact_id="art_stale", training_cutoff_utc=ISSUE - timedelta(days=30)),)
    )
    corrected_s, applied_s = stale.apply(case, models)
    assert applied_s.activation_status == "STALE_REFUSED"
    assert applied_s.aggregate_shift_native == 0.0
    np.testing.assert_array_equal(corrected_s, models.member_values_native)

    # PRODUCT MISMATCH: artifact fitted on a different product set, no model_id
    # match, must NOT apply.
    prod = DebiasAuthority(
        (_artifact(artifact_id="art_prod", product_set_hash="other_modelset", model_id="absent_model"),)
    )
    corrected_p, applied_p = prod.apply(case, models)
    assert applied_p.activation_status == "PRODUCT_MISMATCH_REFUSED"
    assert applied_p.aggregate_shift_native == 0.0
    np.testing.assert_array_equal(corrected_p, models.member_values_native)

    # STATION MISMATCH: artifact fitted at a different settlement station must NOT
    # apply (the live path had NO settlement-station identity check at all).
    stn = DebiasAuthority((_artifact(artifact_id="art_stn", station_id="RJAA"),))
    corrected_st, applied_st = stn.apply(case, models)
    assert applied_st.activation_status == "STATION_MISMATCH_REFUSED"
    assert applied_st.aggregate_shift_native == 0.0
    np.testing.assert_array_equal(corrected_st, models.member_values_native)


# ---------------------------------------------------------------------------
# Test 2 — the Tokyo −4.847 model-validity refusal (the headline defect).
# ---------------------------------------------------------------------------

def test_tokyo_minus_4847_bias_refused_against_realized_residual_band():
    case = _case()
    members_c = [29.0, 30.0, 31.0]
    models = _model_set(members_c)

    # The stale, pathological artifact: it CLAIMS a −4.847°C shift while its OWN
    # realized trailing residual band is centered at ≈−0.33°C with a small std.
    # |−4.847 − (−0.33)| = 4.517, far outside N_SIGMA_BIAS·max(std, eps).
    bad = _artifact(
        artifact_id="art_minus_4847",
        proposed_shift_native=-4.847,
        residual_mean_native=-0.33,
        residual_std_native=0.40,
    )
    band_half_width = N_SIGMA_BIAS * max(bad.residual_std_native, 0.25)
    assert abs(bad.proposed_shift_native - bad.residual_mean_native) > band_half_width

    authority = DebiasAuthority((bad,))
    corrected, applied = authority.apply(case, models)

    # Model-validity REFUSAL inside the estimator — not a downstream cap.
    assert applied.activation_status == "MAGNITUDE_REFUSED"

    # The broken −4.847 output is mathematically impossible: NO shift reached the
    # members. The members are returned UNCHANGED (a reverted unconditional-
    # subtraction transform would have shifted them by ≈+4.847 and failed here).
    assert applied.aggregate_shift_native == 0.0
    np.testing.assert_array_equal(corrected, np.asarray(members_c, dtype=float))
    # Crucially, the served center is NOWHERE NEAR members − (−4.847) = members+4.847.
    assert not np.allclose(corrected, np.asarray(members_c) + 4.847)

    # And an admitted artifact serves the REALIZED band center, never its own
    # proposed_shift_native: even with a benign claim equal to the band, the
    # applied shift is the residual mean, so the histogram is bounded by realized
    # residuals by construction.
    good = _artifact(artifact_id="art_good", proposed_shift_native=-0.33, residual_mean_native=-0.33)
    corrected_g, applied_g = DebiasAuthority((good,)).apply(case, models)
    assert applied_g.activation_status == "APPLIED"
    assert applied_g.aggregate_shift_native == pytest.approx(-0.33)
    assert abs(applied_g.aggregate_shift_native) <= abs(-4.847)


# ---------------------------------------------------------------------------
# Test 3 — exactly one temperature-mean shift can apply.
# ---------------------------------------------------------------------------

def test_only_one_temperature_mean_shift_can_apply():
    case = _case()
    members_c = [29.0, 30.0, 31.0]
    models = _model_set(members_c, model_ids=["m0", "m1", "m2"])

    # Three independently-applicable artifacts, one per correction basis. If the
    # transform were the OLD parallel-correction design (EMOS offset + EDLI bias +
    # grid representativeness each shifting the center), the net shift would be the
    # SUM of all three residual means. The single-authority transform applies
    # EXACTLY ONE basis (the highest priority).
    per_model = _artifact(
        artifact_id="art_per_model",
        model_id="m0",  # per_model_station_walk_forward (highest priority)
        residual_mean_native=-0.30,
        proposed_shift_native=-0.30,
    )
    family = _artifact(
        artifact_id="art_family",
        model_id=None,  # model_family_station_walk_forward
        residual_mean_native=-0.50,
        proposed_shift_native=-0.50,
    )
    # A third "representativeness" basis simulated as another family-level artifact
    # with a distinct id and residual; only ONE family basis may win regardless.
    representativeness = _artifact(
        artifact_id="art_repr",
        model_id=None,
        residual_mean_native=-0.70,
        proposed_shift_native=-0.70,
    )

    authority = DebiasAuthority((family, representativeness, per_model))
    corrected, applied = authority.apply(case, models)

    # Exactly one artifact CHOSEN, and it is the highest-priority basis (per-model).
    assert applied.activation_status == "APPLIED"
    assert applied.artifact_ids[0] == "art_per_model"
    assert applied.aggregate_shift_native == pytest.approx(-0.30)

    # The applied per-member shift is a SINGLE basis's shift, NOT the sum of the
    # three (−0.30 + −0.50 + −0.70 = −1.50). Reverting to parallel corrections
    # would make the net shift −1.50 and these assertions fail.
    assert applied.aggregate_shift_native != pytest.approx(-1.50)
    np.testing.assert_allclose(corrected, np.asarray(members_c) + 0.30)
    # The other two bases are recorded as rejected, never independently applied.
    assert set(applied.artifact_ids[1:]) == {"art_family", "art_repr"}
    # Only one distinct non-zero shift magnitude is present in the per-member vector.
    assert len(set(applied.per_member_shift_native)) == 1
