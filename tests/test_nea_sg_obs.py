# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator v4 NEA 2026-06-17 (zeus_problematic20_best_sources_v4
#   .csv — Singapore NEA data.gov.sg air-temperature as a day0 SHADOW covariate).
#   API shape captured live 2026-06-17 from
#   api-open.data.gov.sg/v2/real-time/api/air-temperature (nearest station to
#   WSSS = S24 "Upper Changi Road North" at 0.040 km).
"""Relationship tests for the Singapore NEA day0 SHADOW observation source.

Asserts, against a captured NEA payload fixture:
  - nearest-station selection resolves S24 (Upper Changi, ~0.04 km from WSSS);
  - the parsed reading carries the correct value + full station provenance;
  - NEA is SHADOW (is_settlement_faithful False) and can NEVER be marked
    settlement truth — a hard constructor guard rejects faithful=True;
  - nea_shadow_source_for_city returns None for non-Singapore cities;
  - fetch is fail-soft (a raising/garbage transport yields None, never a crash).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.data.nea_sg_obs import (
    NEA_SOURCE_ID,
    NEA_SOURCE_ROLE,
    NeaObsReading,
    fetch_nea_reading,
    haversine_km,
    nea_obs_to_fusion_reading,
    nea_shadow_source_for_city,
    nearest_station,
    parse_nea_payload,
)

UTC = timezone.utc

# WSSS Changi settlement coords (config/cities.json Singapore).
WSSS_LAT, WSSS_LON = 1.368, 103.982

# --- captured live v2 payload (2026-06-17), trimmed to 6 stations -------------
NEA_FIXTURE_V2 = {
    "code": 0,
    "data": {
        "stations": [
            {"id": "S109", "deviceId": "S109", "name": "Ang Mo Kio Avenue 5",
             "location": {"latitude": 1.3793, "longitude": 103.85}},
            {"id": "S106", "deviceId": "S106", "name": "Pulau Ubin",
             "location": {"latitude": 1.4168, "longitude": 103.9673}},
            {"id": "S107", "deviceId": "S107", "name": "East Coast Parkway",
             "location": {"latitude": 1.3133, "longitude": 103.962}},
            {"id": "S43", "deviceId": "S43", "name": "Kim Chuan Road",
             "location": {"latitude": 1.3406, "longitude": 103.8882}},
            {"id": "S24", "deviceId": "S24", "name": "Upper Changi Road North",
             "location": {"latitude": 1.3678, "longitude": 103.9823}},
            {"id": "S06", "deviceId": "S06", "name": "Paya Lebar Airport",
             "location": {"latitude": 1.357, "longitude": 103.904}},
        ],
        "readings": [
            {"timestamp": "2026-06-17T22:54:00+08:00", "data": [
                {"stationId": "S109", "value": 26.8},
                {"stationId": "S106", "value": 25.5},
                {"stationId": "S107", "value": 26.7},
                {"stationId": "S43", "value": 27.7},
                {"stationId": "S24", "value": 26.1},
                {"stationId": "S06", "value": 25.9},
            ]},
        ],
        "readingType": "DBT 1M F",
        "readingUnit": "deg C",
    },
    "errorMsg": None,
}

# --- captured legacy v1 payload shape (single nearest station only) -----------
NEA_FIXTURE_LEGACY = {
    "metadata": {
        "stations": [
            {"id": "S24", "device_id": "S24", "name": "Upper Changi Road North",
             "location": {"latitude": 1.3678, "longitude": 103.9823}},
        ],
        "reading_type": "DBT 1M F", "reading_unit": "deg C",
    },
    "items": [
        {"timestamp": "2026-06-17T22:55:00+08:00",
         "readings": [{"station_id": "S24", "value": 26.3}]},
    ],
    "api_info": {"status": "healthy"},
}


def _singapore_city():
    return SimpleNamespace(name="Singapore", lat=WSSS_LAT, lon=WSSS_LON,
                           settlement_unit="C", settlement_source_type="wu_icao",
                           wu_station="WSSS", timezone="Asia/Singapore")


class TestNearestStation:
    def test_resolves_changi_nearest_to_wsss(self):
        st = nearest_station(NEA_FIXTURE_V2, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON)
        assert st is not None
        assert st.station_id == "S24"
        assert st.name == "Upper Changi Road North"
        # S24 is essentially co-located with WSSS (well under 0.5 km).
        assert st.distance_km < 0.5

    def test_legacy_shape_single_station_resolves(self):
        st = nearest_station(NEA_FIXTURE_LEGACY, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON)
        assert st is not None and st.station_id == "S24"

    def test_empty_payload_returns_none(self):
        assert nearest_station({}, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON) is None
        assert nearest_station({"data": {}}, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON) is None

    def test_haversine_zero_at_same_point(self):
        assert haversine_km(WSSS_LAT, WSSS_LON, WSSS_LAT, WSSS_LON) == pytest.approx(0.0, abs=1e-9)


class TestParse:
    def test_parses_nearest_value_with_provenance(self):
        r = parse_nea_payload(NEA_FIXTURE_V2, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON)
        assert r is not None
        assert r.station_id == "S24"
        assert r.station_name == "Upper Changi Road North"
        assert r.value_c == pytest.approx(26.1)
        assert r.source_id == NEA_SOURCE_ID
        assert r.distance_km < 0.5
        assert r.timestamp == datetime(2026, 6, 17, 14, 54, tzinfo=UTC)  # 22:54+08 → UTC

    def test_legacy_payload_parses(self):
        r = parse_nea_payload(NEA_FIXTURE_LEGACY, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON)
        assert r is not None and r.station_id == "S24" and r.value_c == pytest.approx(26.3)

    def test_nearest_station_without_value_returns_none(self):
        payload = {
            "data": {
                "stations": NEA_FIXTURE_V2["data"]["stations"],
                "readings": [{"timestamp": "2026-06-17T22:54:00+08:00",
                              "data": [{"stationId": "S109", "value": 26.8}]}],  # no S24
                "readingUnit": "deg C",
            }
        }
        assert parse_nea_payload(payload, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON) is None


class TestShadowInvariant:
    def test_reading_is_never_settlement_faithful(self):
        r = parse_nea_payload(NEA_FIXTURE_V2, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON)
        assert r.is_settlement_faithful is False
        assert NEA_SOURCE_ROLE == "shadow_covariate"

    def test_constructing_a_faithful_nea_reading_is_rejected(self):
        with pytest.raises(ValueError, match="must be False"):
            NeaObsReading(
                source_id=NEA_SOURCE_ID, station_id="S24", station_name="x",
                distance_km=0.04, value_c=26.1, timestamp=datetime.now(UTC),
                is_settlement_faithful=True,
            )

    def test_fusion_adapter_carries_shadow_flag(self):
        r = parse_nea_payload(NEA_FIXTURE_V2, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON)
        kw = nea_obs_to_fusion_reading(r)
        assert kw["is_settlement_faithful"] is False
        assert kw["source_family"] == NEA_SOURCE_ID
        assert kw["station_id"] == "S24"
        assert kw["value"] == pytest.approx(26.1)


class TestRegistration:
    def test_singapore_resolves_shadow_source(self, monkeypatch):
        # Avoid a live network call: stub the fetch with the fixture's nearest reading.
        fixed = parse_nea_payload(NEA_FIXTURE_V2, settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON)
        monkeypatch.setattr(
            "src.data.nea_sg_obs.fetch_nea_reading",
            lambda *, settlement_lat, settlement_lon, **_: fixed,
        )
        src = nea_shadow_source_for_city(_singapore_city())
        assert src is not None
        assert src.source_id == NEA_SOURCE_ID
        assert src.station_id == "S24"
        assert src.is_settlement_faithful is False
        assert src.distance_km < 0.5

    def test_non_singapore_city_has_no_nea_source(self):
        nyc = SimpleNamespace(name="New York City", lat=40.78, lon=-73.97,
                              settlement_unit="F", settlement_source_type="wu_icao",
                              wu_station="KLGA")
        assert nea_shadow_source_for_city(nyc) is None


class TestFailSoft:
    def test_fetch_failure_returns_none(self, monkeypatch):
        import httpx

        def _http_err(*a, **k):
            raise httpx.ConnectError("refused")
        monkeypatch.setattr("src.data.nea_sg_obs.httpx.get", _http_err)
        assert fetch_nea_reading(settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON) is None

    def test_garbage_payload_returns_none(self, monkeypatch):
        class _Resp:
            status_code = 200
            def json(self):  # noqa: D401
                return {"unexpected": "shape"}
        monkeypatch.setattr("src.data.nea_sg_obs.httpx.get", lambda *a, **k: _Resp())
        assert fetch_nea_reading(settlement_lat=WSSS_LAT, settlement_lon=WSSS_LON) is None
