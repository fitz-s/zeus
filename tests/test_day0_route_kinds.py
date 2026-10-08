# Created: 2026-10-07
# Last reused/audited: 2026-10-08
# Lifecycle: created=2026-10-07; last_reviewed=2026-10-08; last_reused=2026-10-08
# Authority basis: consult REQ-20261007-125708-2dc18b (NO-GO) findings (d)/(f); coordinator D1 (typed route kinds).
# Purpose: Pin typed fast-route kinds through the real consumers. NATIVE_REPORT routes (MGM, IMD,
#   metaviatelecom) are AWC-equivalent METAR content, routine and SPECI, routed exactly as AWC.
#   INSTRUMENT_PROXY routes (JMA, SWOB) are no Day0 fact at any instant. Seed -> adapter binding for
#   ENTRY and HELD on all six fast-route cities; the consult's Tokyo JMA/AWC case; the archived
#   Istanbul SPECIs through parser -> ledger -> fact.
# Reuse: Private SQLite fixtures over the real city/registry config; calls the unmodified adapter.
from __future__ import annotations

import gzip
import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.state.schema.observation_prints_schema import append_print, ensure_table

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
TARGET = "2026-10-07"
# city -> (station, route channel, kind)
ROUTES = {
    "Istanbul": ("LTFM", "mgm_metar_temperature", "native"),
    "Ankara": ("LTAC", "mgm_metar_temperature", "native"),
    "Lucknow": ("VILK", "imd_olbs_metar_temperature", "native"),
    "Moscow": ("UUWW", "metaviatelecom_metar_temperature", "native"),
    "Tokyo": ("RJTT", "jma_amedas_temperature", "proxy"),
    "Toronto": ("CYYZ", "eccc_swob_temperature", "proxy"),
}
T_PAGE = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
T_AWC = datetime(2026, 10, 7, 6, 30, tzinfo=UTC)
T_ROUTE = datetime(2026, 10, 7, 6, 44, tzinfo=UTC)  # off the routine minutes: a SPECI instant
DECISION = datetime(2026, 10, 7, 6, 50, tzinfo=UTC)


class _KeepOpen(sqlite3.Connection):
    """The seed closes its world connection; the adapter reads the same private ledger next."""

    def close(self):
        pass


def _route(channel: str, station: str):
    from src.data.physical_current_sources import load_physical_current_sources

    return next(r for r in load_physical_current_sources()[0]
                if r.source_channel == channel and r.station_id == station)


def _route_raw(route, at: datetime, value: float) -> str:
    return json.dumps({"observed_at": at.isoformat(), "payload_sha256": "ab" * 32,
                       "provider_observed_at_ms": int(at.timestamp() * 1000), "provider_published_at_ms": None,
                       "provider_station": route.identity["provider_station"],
                       "received_at_ms": int(at.timestamp() * 1000) + 60000, "source_channel": route.source_channel,
                       "station_id": route.station_id, "unit": "C", "value_native": value}, sort_keys=True)


def _world() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", factory=_KeepOpen)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    conn.execute("CREATE TABLE observation_instants (city TEXT, target_date TEXT, source TEXT, station_id TEXT, "
                 "local_timestamp TEXT, utc_timestamp TEXT, imported_at TEXT, temp_unit TEXT, running_max REAL, "
                 "running_min REAL, authority TEXT, training_allowed INTEGER, causality_status TEXT, "
                 "source_role TEXT, raw_response TEXT)")
    return conn


def _page(conn, city, station, at, value):
    append_print(conn, city=city, station_id=station, source_channel=f"noaa_wrh_{station.lower()}",
                 publish_ts_utc=at.isoformat(), value_native=value, unit="C",
                 fetched_at_utc=(at + timedelta(minutes=15)).isoformat(),
                 raw_report=f"{station} {at:%d%H%M}Z 35005KT 9999 {round(value):02d}/10 Q1013")


def _awc(conn, city, station, at, value: int):
    append_print(conn, city=city, station_id=station, source_channel="aviationweather_metar",
                 publish_ts_utc=(at + timedelta(minutes=5)).isoformat(), value_native=value, unit="C",
                 fetched_at_utc=(at + timedelta(minutes=5, seconds=4)).isoformat(),
                 raw_report=f"{station} {at:%d%H%M}Z 35005KT 9999 FEW030 {value:02d}/10 Q1013")


def _route_row(conn, city, route, at, value):
    append_print(conn, city=city, station_id=route.station_id, source_channel=route.source_channel,
                 publish_ts_utc=at.isoformat(), value_native=value, unit="C",
                 fetched_at_utc=(at + timedelta(minutes=2)).isoformat(), raw_report=_route_raw(route, at, value))


def _carrier(city: str, station: str, at: datetime, value: float, source: str):
    from tests.integration.test_w3_solve_seam_g3 import make_opportunity_event

    payload = {
        "city": city, "target_date": TARGET, "metric": "high", "temperature_metric": "high",
        "station_id": station, "configured_station_id": station, "settlement_source": source, "settlement_unit": "C",
        "observation_time": at.isoformat(), "observation_available_at": (at + timedelta(minutes=7)).isoformat(),
        "raw_value": value, "rounded_value": round(value), "high_so_far": value,
        "source_match_status": "MATCH", "local_date_status": "MATCH", "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS", "metric_match_status": "MATCH", "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED", "live_authority_status": "live",
        "raw_payload_sha256": "cd" * 32,
    }
    return make_opportunity_event(event_type="DAY0_EXTREME_UPDATED", entity_key=f"{city}|{TARGET}|high|{station}",
                                  source="test", observed_at=at.isoformat(),
                                  available_at=(at + timedelta(minutes=7)).isoformat(),
                                  received_at=(at + timedelta(minutes=7)).isoformat(), payload=payload,
                                  causal_snapshot_id=f"{city.lower()}-d1")


def _bind(conn, city, station, conditioning, *, held: bool, decision=DECISION):
    from src.engine import event_reactor_adapter as era

    return era._global_day0_execution_payload(
        _carrier(city, station, T_PAGE, 20.0, f"noaa_wrh_{station.lower()}"),
        family=SimpleNamespace(city=city, target_date=TARGET, metric="high"),
        resolution=SimpleNamespace(measurement_unit="C", station_id=station),
        conditioning=conditioning, observation_conn=conn, decision_time=decision, posterior_id=880001,
        allow_equivalent_conditioning_clock_advance=held,
    )


def _seed(monkeypatch, conn, city):
    from src.data import replacement_forecast_seed_discovery as seed

    monkeypatch.setattr(seed, "get_world_connection_read_only", lambda: conn)
    return seed._day0_observed_extreme_seed_payload(city=city, target_date=TARGET, metric="high",
                                                    computed_at=DECISION)


def _conditioning(seed_payload) -> dict:
    return {"active": True, "metric": "high", "observed_extreme_c": seed_payload["day0_observed_extreme_c"],
            "observation_time": seed_payload["day0_observed_extreme_observation_time"],
            "sample_count": seed_payload["day0_observed_extreme_sample_count"],
            "source": seed_payload["day0_observed_extreme_source"], "unit": "C"}


# ---------------------------------------------------------------- the consult's Tokyo case

def test_tokyo_jma_proxy_binding_is_not_substituted_by_awc():
    """Consult (d): JMA 24.6, page 24.6, AWC 25 at 06:00Z (all settle 25). A JMA-bound conditioning must
    bind; the adapter must not treat the JMA proxy and AWC as one physical frontier."""
    at = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
    conn = _world()
    _route_row(conn, "Tokyo", _route("jma_amedas_temperature", "RJTT"), at, 24.6)
    _page(conn, "Tokyo", "RJTT", at, 24.6)
    _awc(conn, "Tokyo", "RJTT", at, 25)
    conditioning = {"active": True, "metric": "high", "observation_time": at.isoformat(), "observed_extreme_c": 24.6,
                    "sample_count": 1, "source": "jma_amedas_temperature", "unit": "C"}
    for held in (False, True):
        payload = _bind(conn, "Tokyo", "RJTT", conditioning, held=held, decision=at + timedelta(minutes=20))
        assert payload["_edli_global_day0_binding"]["metric"] == "high"


# ---------------------------------------------------------------- six routes: seed -> adapter, ENTRY and HELD

@pytest.mark.parametrize("city", sorted(ROUTES))
def test_seed_conditioning_binds_at_the_adapter_for_entry_and_held(monkeypatch, city):
    """Page 20 at 06:00, AWC 21 at 06:30, the route 22 at 06:44 (a SPECI instant).  A native route is the
    frontier exactly as an AWC report there would be (margin law included); a proxy is no fact.  The seed's
    conditioning binds for ENTRY and HELD; a HELD clock advance binds where ENTRY refuses, as for AWC."""
    from src.engine import event_reactor_adapter as era

    station, channel, kind = ROUTES[city]
    route = _route(channel, station)
    worlds = {}
    for frontier in (channel, "aviationweather_metar"):
        conn = _world()
        _page(conn, city, station, T_PAGE, 20.0)
        _awc(conn, city, station, T_AWC, 21)
        if frontier == channel:
            _route_row(conn, city, route, T_ROUTE, 22.0)
        else:
            _awc(conn, city, station, T_ROUTE, 22)
        worlds[frontier] = conn
    seeds = {frontier: _seed(monkeypatch, conn, city) for frontier, conn in worlds.items()}
    awc_seed = seeds["aviationweather_metar"]
    route_seed = seeds[channel]
    assert awc_seed is not None and route_seed is not None
    if kind == "native":
        # Routed as AWC: the same value under the same margin law.  The AWC frontier clock is its
        # ledger publish clock (06:49, live behaviour); a native row carries the report's own clock.
        assert route_seed["day0_observed_extreme_c"] == awc_seed["day0_observed_extreme_c"]
        if awc_seed["day0_observed_extreme_source"] == "aviationweather_metar":
            assert route_seed["day0_observed_extreme_source"] == channel
            assert route_seed["day0_observed_extreme_observation_time"] == T_ROUTE.isoformat()
            assert awc_seed["day0_observed_extreme_observation_time"] == (T_ROUTE + timedelta(minutes=5)).isoformat()
        else:  # a margin that sinks AWC below the page (Lucknow 7.0) sinks the native report too
            assert route_seed == awc_seed
    else:
        # The proxy row is no fact: the seed is the METAR content without it.
        assert route_seed["day0_observed_extreme_source"] != channel
        assert route_seed["day0_observed_extreme_c"] == pytest.approx(21.0)
    for frontier, conn in worlds.items():
        conditioning = _conditioning(seeds[frontier])
        for held in (False, True):
            binding = _bind(conn, city, station, conditioning, held=held)["_edli_global_day0_binding"]
            assert binding["settlement_source"] == f"noaa_wrh_{station.lower()}"
            assert binding["probability_conditioning_identity"]["source"] == conditioning["source"]
        # One more report on the plateau advances the frontier clock: HELD binds, ENTRY refuses.
        later = T_ROUTE + timedelta(minutes=6)
        if frontier == channel and kind == "native":
            _route_row(conn, city, route, later, 21.0)
        else:
            _awc(conn, city, station, later, 21)
        decision = later + timedelta(minutes=8)
        if conditioning["source"] in {channel, "aviationweather_metar"}:
            with pytest.raises(ValueError, match="GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH"):
                _bind(conn, city, station, conditioning, held=False, decision=decision)
            held_binding = _bind(conn, city, station, conditioning, held=True,
                                 decision=decision)["_edli_global_day0_binding"]
            assert held_binding["conditioning_clock_role"] == "same_extreme_newer_observation_clock"
    # Revision routing: a native conditioning takes the AWC/Ogimet survival model, a proxy never does.
    from src.events.day0_authority import day0_is_noaa_preliminary_source

    assert era._day0_is_shared_provisional_carrier_source(channel) is (kind == "native")
    assert day0_is_noaa_preliminary_source(channel) is (kind == "native")


# ---------------------------------------------------------------- native SPECIs survive (consult (f))

def test_archived_istanbul_specis_survive_parser_ledger_and_fact():
    """The archived MGM body holds SPECI LTFM 300214Z 19, 300302Z 19, 301033Z 20.  A ledger of only
    those three reports gives an Istanbul HIGH physical fact of 20 at 10:33Z; the page stays the only
    settlement channel."""
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.data.station_temperature_adapters import native_sample_value, parse_station_payload

    body = gzip.decompress((ROOT / "artifacts/fast_obs_audit/round4_global/"
                            "1b6bdd050c6fc491a935654c80e77029a1ac67cf2055a201e40b622457a3b148.body.gz").read_bytes())
    route = _route("mgm_metar_temperature", "LTFM")
    prints = parse_station_payload(route, body, received_at=datetime(2026, 10, 1, tzinfo=UTC))
    specials = [p for p in prints if p.observed_at.minute not in (20, 50)]
    assert [(p.observed_at.strftime("%d%H%MZ"), p.value_native) for p in specials] == [
        ("300214Z", 19.0), ("300302Z", 19.0), ("301033Z", 20.0)]
    conn = _world()
    for sample in specials:
        assert append_print(conn, city="Istanbul", station_id="LTFM", source_channel=route.source_channel,
                            publish_ts_utc=sample.observed_at.isoformat(),
                            value_native=native_sample_value(sample, "C"), unit="C",
                            fetched_at_utc=(sample.observed_at + timedelta(minutes=2)).isoformat(),
                            raw_report=sample.raw_report)
    kwargs = dict(city="Istanbul", target_date="2026-09-30", temperature_metric="high",
                  decision_time=datetime(2026, 9, 30, 11, 0, tzinfo=UTC))
    for metar_content_only in (False, True):  # the adapter's physical fact and the seed's fact
        fact = _latest_authorized_day0_fact(conn, require_settlement_channel=False,
                                            metar_content_only=metar_content_only, **kwargs)
        assert fact["observation_source"] == "mgm_metar_temperature"
        assert float(fact["observed_extreme_native"]) == 20.0
        assert fact["observation_time"] == "2026-09-30T10:33:00+00:00"
    assert _latest_authorized_day0_fact(conn, require_settlement_channel=True, **kwargs) is None


def test_native_row_that_is_not_a_report_integer_is_no_fact():
    """A native route row must carry the report's integer (the AWC value of the same report)."""
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact

    route = _route("mgm_metar_temperature", "LTFM")
    conn = _world()
    _route_row(conn, "Istanbul", route, T_ROUTE, 22.4)
    assert _latest_authorized_day0_fact(conn, city="Istanbul", target_date=TARGET, temperature_metric="high",
                                        decision_time=DECISION, require_settlement_channel=False) is None


# ---------------------------------------------------------------- materialization routing parity

@pytest.mark.parametrize("city", sorted(ROUTES))
def test_materializer_routes_a_native_state_exactly_as_awc_with_missing_inputs(city):
    """With no current-state print and no hourly vectors, a native-sourced request ends exactly where an
    AWC-sourced one ends; a proxy-sourced (legacy) request stays outside the carrier region, as on live."""
    from src.data import replacement_forecast_materializer as m
    from tests.test_replacement_forecast_materializer import _request

    station, channel, kind = ROUTES[city]
    tz = {"Istanbul": "Europe/Istanbul", "Ankara": "Europe/Istanbul", "Lucknow": "Asia/Kolkata",
          "Moscow": "Europe/Moscow", "Tokyo": "Asia/Tokyo", "Toronto": "America/Toronto"}[city]

    def outcome(source):
        base = _request(day0_observed_extreme_c=21.0, day0_observed_extreme_source=source,
                        day0_observed_extreme_observation_time=T_ROUTE.isoformat(),
                        day0_observed_extreme_sample_count=3)
        request = replace(base, city=city, city_id=city, city_timezone=tz, target_date=date(2026, 10, 7),
                          computed_at=DECISION, expires_at=DECISION + timedelta(hours=2))
        conn = _world()
        try:
            m._day0_noaa_preliminary_carrier(conn, request, metric="high", future_members_c=(20.0, 22.0),
                                             bins=request.bins, path_error_sigma_c=1.0)
            built = "BUILT"
        except ValueError as exc:
            built = str(exc)
        return m._day0_carrier_extreme_c(request), built

    awc = outcome("aviationweather_metar")
    assert awc == (21.0, "DAY0_NOAA_PRELIMINARY_CARRIER_CURRENT_TEMPERATURE_STATE_MISSING")
    if kind == "native":
        assert outcome(channel) == awc
    else:
        assert outcome(channel) == (None, "DAY0_NOAA_PRELIMINARY_CARRIER_SOURCE_INVALID")
