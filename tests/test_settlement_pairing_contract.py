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
    UnknownSettlementAuthorityError,
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


def test_normalize_reconciles_collector_vocabulary_to_authority_family():
    """The forecast tags the city's settlement_source_type (noaa / cwa_station); the harvester
    records the COLLECTOR (ogimet_metar_v1 / cwa_no_collector_v0). Same observation truth
    (operator-confirmed 2026-05-29) -> both reduce to ONE canonical authority family so the
    pairing gate reconciles instead of dropping the city (P2 SEV-2 fix)."""
    # forecast-side tags
    assert normalize_settlement_authority("noaa") == "noaa"
    assert normalize_settlement_authority("cwa_station") == "cwa"
    # settlement-side collector data_versions reduce to the SAME family
    assert normalize_settlement_authority("ogimet_metar_v1") == "noaa"   # ogimet collects NWS METAR
    assert normalize_settlement_authority("cwa_no_collector_v0") == "cwa"
    # wu / hko already aligned
    assert normalize_settlement_authority("hko") == "hko"


def test_normalize_unknown_authority_raises_loud_not_silent():
    """An authority token outside the known family registry must RAISE (loud quarantine), never
    pass through to a silent mismatch/drop that could starve the ledger."""
    with pytest.raises(UnknownSettlementAuthorityError):
        normalize_settlement_authority("frobnicate_satellite_v3")


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


def test_noaa_forecast_reconciles_with_ogimet_settlement_via_query_param_station():
    """P2 SEV-2 antibody — Istanbul/Moscow/Tel Aviv: the forecast tags settlement_source_type
    'noaa', the settlement was collected via ogimet (data_version 'ogimet_metar_v1') and its
    station lives in a weather.gov '?site=' query param (NOT the last path segment). Same
    airport-METAR truth (operator-confirmed) -> must reconcile to ONE ResidualKey, not drop.
    RED before the authority registry + query-param station parser; the legacy contract dropped
    all three of these cities' VERIFIED HIGH residuals."""
    fo = ForecastObject.from_snapshot_row(_opendata_row(
        city="Tel Aviv", settlement_station_id="LLBG",
        settlement_source_type="noaa", settlement_unit="C"))
    so = SettlementObject.from_settlement_row(_settlement_row(
        city="Tel Aviv",
        settlement_source="https://www.weather.gov/wrh/timeseries?site=LLBG",
        provenance_json=json.dumps({"data_version": "ogimet_metar_v1"})), claimed_unit="C")
    rk = pair_residual(fo, so)
    assert rk.target.settlement_authority == "noaa"   # ogimet reconciled to the noaa family
    assert rk.target.settlement_station == "LLBG"      # parsed from ?site=, not the path tail


def test_station_parsed_from_weather_gov_query_param():
    """The weather.gov timeseries URL carries the station in '?site=XXX', not the path tail."""
    so = SettlementObject.from_settlement_row(_settlement_row(
        settlement_source="https://www.weather.gov/wrh/timeseries?site=UUWW",
        provenance_json=json.dumps({"data_version": "ogimet_metar_v1"})), claimed_unit="C")
    assert so.target.settlement_station == "UUWW"


# --- D-S1: first-class settlement_station / settlement_unit columns -----------------
# settlement_outcomes gains nullable settlement_station + settlement_unit columns. When
# present they are the VERIFIED truth (station no longer heuristically parsed from the URL;
# unit no longer the forecast's unverifiable CLAIM). On NULL/absent the contract falls back
# to the prior heuristic so un-backfilled legacy rows behave exactly as before (never
# fail-closed on a missing column).


def test_settlement_station_column_overrides_unparseable_url():
    """D-S1 / Hong Kong un-block: HKO settles via a climat.htm URL with NO parseable station
    code, so the URL heuristic raises and the residual was dropped. With the first-class
    settlement_station column populated, the SettlementObject uses it and the unparseable URL
    is never consulted (settlement_source is no longer REQUIRED when the column is present)."""
    so = SettlementObject.from_settlement_row(
        _settlement_row(
            settlement_station="VHHH",
            settlement_source="https://www.hko.gov.hk/en/cis/climat.htm",  # no station code
            provenance_json=json.dumps({"data_version": "hko_daily_api_v1"}),
        ),
        claimed_unit="C",
    )
    assert so.target.settlement_station == "VHHH"


def test_settlement_unit_column_is_verified_truth_detautologizes_pairing():
    """D-S1 antibody: when settlement_unit is a first-class column it is the VERIFIED settlement
    unit, INDEPENDENT of the forecast's claim. A forecast claiming F paired to a settlement the
    column says is C is a degC/degF mis-scale (Cons-SEV-1.C) and MUST be refused — not silently
    coerced to the forecast's claim (the pre-D-S1 tautology where unit==claimed always matched).
    RED before from_settlement_row prefers the column over claimed_unit."""
    fo = ForecastObject.from_snapshot_row(_opendata_row(settlement_unit="F"))
    so = SettlementObject.from_settlement_row(
        _settlement_row(settlement_unit="C"), claimed_unit="F")  # column C overrides the F claim
    assert so.target.settlement_unit == "C"
    with pytest.raises(ForecastTargetMismatchError):
        pair_residual(fo, so)


def test_settlement_unit_column_agreeing_still_pairs():
    """A verified settlement_unit column that AGREES with the forecast claim pairs normally."""
    fo = ForecastObject.from_snapshot_row(_opendata_row(settlement_unit="F"))
    so = SettlementObject.from_settlement_row(
        _settlement_row(settlement_unit="F"), claimed_unit="F")
    rk = pair_residual(fo, so)
    assert rk.target.settlement_unit == "F"


def test_settlement_columns_absent_falls_back_to_heuristic():
    """D-S1 backward-compat: a settlement row WITHOUT the new columns (the un-backfilled live
    state) behaves EXACTLY as before — station from the URL, unit from the forecast's claim.
    The contract must NOT fail-closed on a missing column."""
    so = SettlementObject.from_settlement_row(_settlement_row(), claimed_unit="F")
    assert so.target.settlement_station == "KORD"  # parsed from the wunderground URL
    assert so.target.settlement_unit == "F"  # the forecast's claim (fallback)


def test_settlement_station_column_blank_treated_as_absent():
    """An empty-string station column is absent → URL fallback (no spurious blank station)."""
    so = SettlementObject.from_settlement_row(
        _settlement_row(settlement_station=""), claimed_unit="F")
    assert so.target.settlement_station == "KORD"
