# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (residual-pairing seam) + CRITIC_SYNTHESIS_2026-05-29
#   §2 (C1: source_kind='prior' hardcoded -> lineage collapse; lineage must be DERIVED) and
#   the target-equality antibody (forecast_target.assert_same_target).
"""pair_residual: a residual key is constructible ONLY when a ForecastObject and a
SettlementObject describe the same random variable (assert_same_target), and it carries
a DERIVED source_kind (tigge_prior vs opendata_live) — never the hardcoded 'prior' that
collapsed TIGGE and OpenData lineage in the legacy ledger.
"""

from __future__ import annotations

import json

import pytest

from src.contracts.forecast_object import ForecastObject
from src.contracts.forecast_target import ForecastTarget, ForecastTargetMismatchError
from src.contracts.residual_key import (
    ResidualKey,
    SettlementObject,
    pair_residual,
    source_kind_for_data_version,
)


def _opendata_row(**overrides) -> dict:
    base = dict(
        city="Chicago",
        temperature_metric="HIGH",
        target_date="2026-05-20",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        issue_time="2026-05-18T12:00:00+00:00",
        source_cycle_time="2026-05-18T12:00:00+00:00",
        lead_hours=50.0,
        members_json=json.dumps([20.0, 21.0, 22.0]),
        members_unit="degC",
        forecast_window_start_utc="2026-05-20T05:00:00+00:00",
        forecast_window_end_utc="2026-05-21T05:00:00+00:00",
        settlement_station_id="KORD",
        settlement_unit="degF",
        settlement_source_type="wu_icao",
    )
    base.update(overrides)
    return base


def _settlement(**overrides) -> SettlementObject:
    target = ForecastTarget(
        city="Chicago",
        metric="HIGH",
        target_local_date="2026-05-20",
        settlement_station="KORD",
        settlement_unit="degF",
        settlement_authority="wu_icao",
    )
    return SettlementObject(target=target, settlement_value=72.0)


def test_source_kind_opendata_is_live():
    assert source_kind_for_data_version(
        "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
    ) == "opendata_live"


def test_source_kind_tigge_is_prior():
    assert source_kind_for_data_version(
        "tigge_mx2t6_local_calendar_day_max_v1"
    ) == "tigge_prior"


def test_unknown_lineage_is_refused():
    with pytest.raises(ValueError):
        source_kind_for_data_version("mystery_source_v1")


def test_pair_residual_on_same_target_yields_keyed_residual():
    fo = ForecastObject.from_snapshot_row(_opendata_row())
    rk = pair_residual(fo, _settlement())
    assert isinstance(rk, ResidualKey)
    assert rk.source_kind == "opendata_live"   # DERIVED, not 'prior'
    assert rk.product == "mx2t3"
    assert rk.cycle == "12z"
    assert rk.lead_hours == 50.0
    assert rk.target == fo.target


def test_pair_residual_tigge_carries_prior_lineage():
    fo = ForecastObject.from_snapshot_row(
        _opendata_row(data_version="tigge_mx2t6_local_calendar_day_max_v1")
    )
    rk = pair_residual(fo, _settlement())
    assert rk.source_kind == "tigge_prior"
    assert rk.product == "mx2t6"


def test_pair_residual_refuses_target_date_mismatch():
    """A forecast for 05-20 paired to a settlement for 05-21 is a different RV."""
    fo = ForecastObject.from_snapshot_row(_opendata_row(target_date="2026-05-20"))
    bad_settlement_target = ForecastTarget(
        city="Chicago", metric="HIGH", target_local_date="2026-05-21",
        settlement_station="KORD", settlement_unit="degF", settlement_authority="wu_icao",
    )
    with pytest.raises(ForecastTargetMismatchError):
        pair_residual(fo, SettlementObject(target=bad_settlement_target, settlement_value=72.0))


def test_pair_residual_refuses_station_mismatch():
    fo = ForecastObject.from_snapshot_row(_opendata_row(settlement_station_id="KORD"))
    bad = ForecastTarget(
        city="Chicago", metric="HIGH", target_local_date="2026-05-20",
        settlement_station="KMDW", settlement_unit="degF", settlement_authority="wu_icao",
    )
    with pytest.raises(ForecastTargetMismatchError):
        pair_residual(fo, SettlementObject(target=bad, settlement_value=72.0))


def test_forecast_object_retains_data_version_for_lineage():
    fo = ForecastObject.from_snapshot_row(_opendata_row())
    assert fo.data_version == "ecmwf_opendata_mx2t3_local_calendar_day_max_v1"
