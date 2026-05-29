# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P2 D-J1/D-S1 (P2_LEDGER_SEAM_FINDINGS_2026-05-29.md). The legacy
#   ledger JOIN matched (city,target_date,metric) only — pairing wrong-station settlements.
#   This is the cross-module relationship test: a forecast snapshot row + a settlement row
#   form a residual ONLY when their full targets reconcile (incl station + normalized authority).
"""Relationship contract across the forecast↔settlement boundary, on REAL row shapes.

The forecast snapshot claims its settlement target via settlement_source_type ("wu_icao");
the settlement row carries the authority as provenance_json.data_version ("wu_icao_history_v1")
and the station inside the settlement_source URL. Both must normalize to the SAME identity for
a true pair to match — and a wrong-station settlement must be refused (D-J1 fix).
"""

from __future__ import annotations

import json

import pytest

from src.contracts.forecast_object import ForecastObject
from src.contracts.forecast_target import (
    ForecastTargetMismatchError,
    normalize_settlement_authority,
)
from src.contracts.residual_key import SettlementObject, pair_residual


def _opendata_row(**overrides) -> dict:
    base = dict(
        city="Chicago", temperature_metric="HIGH", target_date="2026-05-20",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max_v1",
        issue_time="2026-05-18T12:00:00+00:00", source_cycle_time="2026-05-18T12:00:00+00:00",
        lead_hours=50.0, members_json=json.dumps([72.0, 74.0, 76.0]), members_unit="degF",
        forecast_window_start_utc="2026-05-20T05:00:00+00:00",
        forecast_window_end_utc="2026-05-21T05:00:00+00:00",
        settlement_station_id="KORD", settlement_unit="degF", settlement_source_type="wu_icao",
    )
    base.update(overrides)
    return base


def _settlement_row(**overrides) -> dict:
    base = dict(
        city="Chicago", temperature_metric="HIGH", target_date="2026-05-20",
        settlement_value=72.0,
        settlement_source="https://www.wunderground.com/history/daily/us/il/chicago/KORD",
        provenance_json=json.dumps({"data_version": "wu_icao_history_v1"}),
    )
    base.update(overrides)
    return base


def test_normalize_strips_authority_version_suffix():
    assert normalize_settlement_authority("wu_icao_history_v1") == "wu_icao"
    assert normalize_settlement_authority("wu_icao") == "wu_icao"
    assert normalize_settlement_authority("hko_daily_api_v1") == "hko"
    assert normalize_settlement_authority("ogimet_metar_v1") == "ogimet"


def test_from_settlement_row_parses_station_from_wu_url():
    s = SettlementObject.from_settlement_row(_settlement_row(), claimed_unit="degF")
    assert s.target.settlement_station == "KORD"
    assert s.settlement_value == 72.0


def test_from_settlement_row_authority_normalized_from_provenance():
    s = SettlementObject.from_settlement_row(_settlement_row(), claimed_unit="degF")
    assert s.target.settlement_authority == "wu_icao"  # from wu_icao_history_v1


def test_from_settlement_row_missing_provenance_data_version_raises():
    with pytest.raises(Exception):
        SettlementObject.from_settlement_row(
            _settlement_row(provenance_json=json.dumps({})), claimed_unit="degF")


def test_from_settlement_row_unparseable_station_raises():
    with pytest.raises(Exception):
        SettlementObject.from_settlement_row(
            _settlement_row(settlement_source="ogimet"), claimed_unit="degF")


def test_true_pair_reconciles_to_residual_key():
    """Forecast snapshot + its TRUE settlement (same station/authority/date/unit) -> ResidualKey.
    Proves the two representations (wu_icao vs wu_icao_history_v1) reconcile."""
    fo = ForecastObject.from_snapshot_row(_opendata_row())
    so = SettlementObject.from_settlement_row(_settlement_row(), claimed_unit="degF")
    rk = pair_residual(fo, so)
    assert rk.source_kind == "opendata_live"
    assert rk.target.settlement_station == "KORD"
    assert rk.target.settlement_authority == "wu_icao"


def test_wrong_station_settlement_is_refused():
    """The exact D-J1 bug: a Chicago forecast for KORD must NOT pair with a KMDW settlement,
    even though city/date/metric match (the legacy loose join would have paired them)."""
    fo = ForecastObject.from_snapshot_row(_opendata_row(settlement_station_id="KORD"))
    so = SettlementObject.from_settlement_row(
        _settlement_row(
            settlement_source="https://www.wunderground.com/history/daily/us/il/chicago/KMDW"),
        claimed_unit="degF")
    with pytest.raises(ForecastTargetMismatchError):
        pair_residual(fo, so)
