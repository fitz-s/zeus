# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (ForecastObject contract); CRITIC_SYNTHESIS_2026-05-29
#   §2a (product segregation: mx2t3 6h-vs-3h are different RVs) + Cons-SEV-1.C (unit) +
#   the writer/reader seam enforcement. Relationship/fail-closed tests precede impl.
# Lifecycle: created=2026-05-29; last_reviewed=2026-05-29; last_reused=never
# Purpose: Fail-closed contract for ForecastObject.from_snapshot_row — rows missing product/target/unit are unconstructable as typed RV objects.
# Reuse: Run after any change to ForecastObject.from_snapshot_row, ForecastTarget, or MembersUnitInvalidError.
"""ForecastObject.from_snapshot_row: a forecast row becomes a typed random-variable
object ONLY when every RV-defining field is present and valid. A row missing its
product (data_version), settlement target, or members unit is unconstructable as a
ForecastObject — the writer/reader seam refuses it instead of silently serving a
half-defined RV.

Carries the product token (mx2t3 vs mx2t6 = different RVs, asymmetry SEV-1-B),
the issue cycle, and RAW lead_hours (the bucket-boundary choice is a P3 statistical
decision, deliberately NOT locked into the contract).
"""

from __future__ import annotations

import json

import pytest

from src.contracts.forecast_object import (
    ForecastObject,
    ForecastObjectIncompleteError,
)
from src.contracts.forecast_target import ForecastTarget
from src.contracts.ensemble_snapshot_provenance import MembersUnitInvalidError


def _row(**overrides) -> dict:
    base = dict(
        city="Chicago",
        temperature_metric="HIGH",
        target_date="2026-05-20",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        issue_time="2026-05-18T12:00:00+00:00",
        source_cycle_time="2026-05-18T12:00:00+00:00",
        lead_hours=50.0,
        members_json=json.dumps([20.1, 21.3, 19.8, 22.0]),
        members_unit="degC",
        forecast_window_start_utc="2026-05-20T05:00:00+00:00",
        forecast_window_end_utc="2026-05-21T05:00:00+00:00",
        settlement_station_id="KORD",
        settlement_unit="degF",
        settlement_source_type="wu_icao",
    )
    base.update(overrides)
    return base


def test_complete_row_builds_forecast_object():
    fo = ForecastObject.from_snapshot_row(_row())
    assert fo.product == "mx2t3"           # 3h product token
    assert fo.cycle == "12z"
    assert fo.lead_hours == 50.0           # raw lead retained, not bucketed
    assert fo.members == [20.1, 21.3, 19.8, 22.0]
    assert fo.members_unit == "degC"


def test_target_is_a_forecast_target_with_settlement_identity():
    fo = ForecastObject.from_snapshot_row(_row())
    assert fo.target == ForecastTarget(
        city="Chicago",
        metric="HIGH",
        target_local_date="2026-05-20",
        settlement_station="KORD",
        settlement_unit="degF",
        settlement_authority="wu_icao",
    )


def test_product_distinguishes_6h_from_3h():
    """mx2t6 (6h TIGGE) is a DIFFERENT random variable than mx2t3 (3h) — the
    product token must separate them (asymmetry SEV-1-B)."""
    fo = ForecastObject.from_snapshot_row(
        _row(data_version="tigge_mx2t6_local_calendar_day_max_v1")
    )
    assert fo.product == "mx2t6"


def test_missing_data_version_is_unconstructable():
    with pytest.raises(ForecastObjectIncompleteError) as exc:
        ForecastObject.from_snapshot_row(_row(data_version=None))
    assert "data_version" in str(exc.value)


def test_missing_settlement_station_is_unconstructable():
    with pytest.raises(ForecastObjectIncompleteError) as exc:
        ForecastObject.from_snapshot_row(_row(settlement_station_id=None))
    assert "station" in str(exc.value)


def test_missing_members_unit_raises_unit_error():
    with pytest.raises(MembersUnitInvalidError):
        ForecastObject.from_snapshot_row(_row(members_unit=None))


def test_kelvin_members_unit_rejected():
    with pytest.raises(MembersUnitInvalidError):
        ForecastObject.from_snapshot_row(_row(members_unit="K"))


def test_cycle_falls_back_to_issue_time_when_cycle_time_missing():
    """source_cycle_time is nullable on pre-PLAN legacy rows (D_impl_feasibility);
    cycle must fall back to issue_time hour, not crash."""
    fo = ForecastObject.from_snapshot_row(
        _row(source_cycle_time=None, issue_time="2026-05-18T00:00:00+00:00")
    )
    assert fo.cycle == "00z"


def test_forecast_object_is_frozen():
    fo = ForecastObject.from_snapshot_row(_row())
    with pytest.raises(Exception):
        fo.product = "mx2t6"  # type: ignore[misc]
