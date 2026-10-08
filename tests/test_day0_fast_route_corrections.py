# Created: 2026-10-08
# Last reused/audited: 2026-10-08
# Lifecycle: created=2026-10-08; last_reviewed=2026-10-08; last_reused=2026-10-08
# Authority basis: consult REQ-20261007-125708-2dc18b findings (d)/(f); fast-obs matrix gaps G1/G2/G8.
# Purpose: Pin the fast-route corrections independent of any Day0 operator: route kinds by provider,
#   native report finality, proxies never a Day0 fact (Tokyo 10-04 LOW), physical-only stations never
#   condition a seed (Helsinki FMI), settlement-integer fast-tail supersession (Tokyo/Toronto).
# Reuse: Private SQLite fixtures over the real city/registry config.
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc
TOKYO = SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", wu_station="RJTT", settlement_source_type="noaa",
                        settlement_unit="C")
HELSINKI = SimpleNamespace(name="Helsinki", timezone="Europe/Helsinki", wu_station="EFHK",
                           settlement_source_type="noaa", settlement_unit="C")


def test_route_kinds_are_typed_by_provider():
    from src.data.physical_current_sources import RouteKind, load_physical_current_sources

    kinds = {(s.provider, s.station_id): s.kind for s in load_physical_current_sources()[0]}
    assert {k for k, v in kinds.items() if v is RouteKind.NATIVE_REPORT} == {
        ("mgm_metar", "LTFM"), ("mgm_metar", "LTAC"), ("imd_olbs_metar", "VILK"), ("metaviatelecom_metar", "UUWW")}
    assert {k for k, v in kinds.items() if v is RouteKind.INSTRUMENT_PROXY} == {
        ("jma_amedas", "RJTT"), ("eccc_swob", "CYYZ")}
    assert kinds[("fmi_wfs", "EFHK")] is RouteKind.PHYSICAL
    assert kinds[("noaa_wrh", "RJTT")] is RouteKind.RESOLVER_PAGE


def test_native_reports_are_provisional_proxies_are_not_metar_content():
    from src.events.day0_authority import (
        DAY0_MONOTONE_SETTLEMENT_BOUND, DAY0_PROVISIONAL_CURRENT_SNAPSHOT, DAY0_UNKNOWN_FINALITY,
        day0_evidence_finality, day0_is_carrier_source, day0_is_native_report_source,
        day0_is_noaa_preliminary_source,
    )

    for channel, station in (("mgm_metar_temperature", "LTFM"), ("imd_olbs_metar_temperature", "VILK"),
                             ("metaviatelecom_metar_temperature", "UUWW")):
        assert day0_evidence_finality({"settlement_source": channel}) == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
        assert day0_evidence_finality({"settlement_source": channel,
                                       "evidence_finality": DAY0_MONOTONE_SETTLEMENT_BOUND}) == DAY0_PROVISIONAL_CURRENT_SNAPSHOT
        assert day0_is_noaa_preliminary_source(channel) and day0_is_carrier_source(channel)
        assert day0_is_native_report_source(channel, station=station)
        assert not day0_is_native_report_source(channel, station="RJTT")
    for channel in ("jma_amedas_temperature", "eccc_swob_temperature", "fmi_airport_temperature"):
        assert not day0_is_noaa_preliminary_source(channel) and not day0_is_carrier_source(channel)
        assert not day0_is_native_report_source(channel)
        assert day0_evidence_finality({"settlement_source": channel}) == DAY0_UNKNOWN_FINALITY
    assert day0_evidence_finality({"settlement_source": "noaa_wrh_rjtt"}) == DAY0_MONOTONE_SETTLEMENT_BOUND


def _jma_raw(at: datetime, value: float) -> str:
    return json.dumps({"observed_at": at.isoformat(), "provider_station": "44166", "source_channel": "jma_amedas_temperature",
                       "station_id": "RJTT", "unit": "C", "value_native": value})


def test_g2_instrument_proxy_is_never_a_day0_fact(monkeypatch):
    """Tokyo 2026-10-04 LOW: JMA 18.4 at 20:40Z (settled 19).  On live the settlement fact was 18.4 (R = 18).
    A proxy row is no Day0 fact at any instant: neither the settlement fact, the physical frontier, nor a seed."""
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact

    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tokyo": TOKYO})
    monkeypatch.setattr("src.config.cities_by_name", {"Tokyo": TOKYO}, raising=False)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_table(c)
    for at, v in ((datetime(2026, 10, 4, 20, 30, tzinfo=UTC), 18.9), (datetime(2026, 10, 4, 20, 40, tzinfo=UTC), 18.4)):
        append_print(c, city="Tokyo", station_id="RJTT", source_channel="jma_amedas_temperature",
                     publish_ts_utc=at.isoformat(), value_native=v, unit="C",
                     fetched_at_utc=(at + timedelta(minutes=7)).isoformat(), raw_report=_jma_raw(at, v))
    kwargs = dict(city="Tokyo", target_date="2026-10-05", temperature_metric="low",
                  decision_time=datetime(2026, 10, 4, 21, 0, tzinfo=UTC))
    assert _latest_authorized_day0_fact(c, require_settlement_channel=True, **kwargs) is None
    assert _latest_authorized_day0_fact(c, require_settlement_channel=False, **kwargs) is None
    assert _latest_authorized_day0_fact(c, require_settlement_channel=False, metar_content_only=True,
                                        **kwargs) is None


def test_g1_physical_only_dense_station_never_conditions_a_seed(monkeypatch):
    """Helsinki 2026-10-07: FMI 11.2 beat AWC 11.0 in the physical MAX and became an UNKNOWN-finality seed
    source (11 fused_normal_direct posteriors).  Named exception to byte-identity: the seed's fact is METAR
    content; the adapter's physical fact is unchanged."""
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact

    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Helsinki": HELSINKI})
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_table(c)
    hel = ZoneInfo("Europe/Helsinki")
    metar_at = datetime(2026, 10, 7, 11, 50, tzinfo=hel).astimezone(UTC)
    fmi_at = datetime(2026, 10, 7, 12, 10, tzinfo=hel).astimezone(UTC)
    append_print(c, city="Helsinki", station_id="EFHK", source_channel="aviationweather_metar",
                 publish_ts_utc=(metar_at + timedelta(minutes=1)).isoformat(), value_native=11, unit="C",
                 fetched_at_utc=(metar_at + timedelta(minutes=2)).isoformat(),
                 raw_report=f"METAR EFHK {metar_at:%d%H%M}Z 27012KT 9999 SCT025 11/06 Q1009")
    fmi_raw = json.dumps({"fmisid": "100968", "wmo": "2974", "station": "Vantaa Helsinki-Vantaan lentoasema",
                          "property": "https://opendata.fmi.fi/meta?observableProperty=observation&param=temperature&language=eng",
                          "unit": "degC", "availability": "local_fetch_only", "observed_at": fmi_at.isoformat(),
                          "value": 11.2})
    append_print(c, city="Helsinki", station_id="EFHK", source_channel="fmi_airport_temperature",
                 publish_ts_utc=fmi_at.isoformat(), value_native=11.2, unit="C",
                 fetched_at_utc=(fmi_at + timedelta(minutes=2)).isoformat(), raw_report=fmi_raw)
    kwargs = dict(city="Helsinki", target_date="2026-10-07", temperature_metric="high",
                  decision_time=fmi_at + timedelta(minutes=10))
    physical = _latest_authorized_day0_fact(c, require_settlement_channel=False, **kwargs)
    assert physical["observation_source"] == "fmi_airport_temperature"
    metar = _latest_authorized_day0_fact(c, require_settlement_channel=False, metar_content_only=True, **kwargs)
    assert metar["observation_source"] == "aviationweather_metar"
    assert float(metar["observed_extreme_native"]) == 11.0


def test_g8_supersession_compares_settlement_integers():
    from src.data.day0_fast_obs import fast_extreme_supersedes_settlement

    # Tokyo 2026-10-07 05:00Z: route 24.6 vs AWC 25.0 at the same instant: both settle 25.
    assert not fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=25.0, settlement_extreme_c=24.6, city=TOKYO)
    assert fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=26.0, settlement_extreme_c=24.6, city=TOKYO)
    # Toronto 05:00Z: SWOB 12.5 vs AWC 13.0: both settle 13 (half-up).
    assert not fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=13.0, settlement_extreme_c=12.5, city=TOKYO)
    assert not fast_extreme_supersedes_settlement(metric="low", fast_extreme_c=12.6, settlement_extreme_c=13.4, city=TOKYO)
    # Fahrenheit: 13.1 C = 55.6 F settles 56 < 57 (KORD page 57.2 F advances a LOW); 13.9 C = 57.0 F does not.
    kord = SimpleNamespace(name="Chicago", timezone="America/Chicago", wu_station="KORD",
                           settlement_source_type="noaa", settlement_unit="F")
    assert fast_extreme_supersedes_settlement(metric="low", fast_extreme_c=13.1, settlement_extreme_c=14.0, city=kord)
    assert not fast_extreme_supersedes_settlement(metric="low", fast_extreme_c=13.9, settlement_extreme_c=14.0, city=kord)
    # The raw comparison is kept without a city.
    assert fast_extreme_supersedes_settlement(metric="high", fast_extreme_c=25.0, settlement_extreme_c=24.6)
    assert not fast_extreme_supersedes_settlement(metric="mean", fast_extreme_c=25.0, settlement_extreme_c=24.6)
