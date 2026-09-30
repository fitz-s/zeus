# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority: INV-01/INV-06/INV-08/INV-37; REQ-20260929-170443-dd6d0b.
"""Live receipt clocks, civil days, and terminal remainder law antibodies."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import permutations
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from src.data.day0_fast_obs import FastObsPrefetch, MetarReport
from src.execution.order_truth_reducer import VenueOrderTruthReducer

UTC = timezone.utc

@pytest.mark.parametrize("state", ["EXPIRED", "CANCEL_CONFIRMED", "VENUE_WIPED"])
@pytest.mark.parametrize("size", [None, "5"])
def test_late_positive_fill_cannot_reopen_terminal_remainder(state, size):
    terminal = {"state": state, "remaining_size": "0", "matched_size": "0"}
    stale = {"state": "LIVE", "remaining_size": "5", "matched_size": "0"}
    for facts in permutations([terminal, stale]):
        truth = VenueOrderTruthReducer.reduce(order_facts=facts, trade_filled_size="2", command_size=size, open_order_present=True)
        assert truth.proof_class == "TERMINAL_PARTIAL"
        assert truth.remaining_size == Decimal("0")
        assert truth.matched_size == Decimal("2")


def test_positive_fill_without_terminal_evidence_keeps_remainder_unknown_or_open():
    for size, remainder in [("5", Decimal("3")), (None, None)]:
        truth = VenueOrderTruthReducer.reduce(trade_filled_size="2", command_size=size)
        assert truth.proof_class == "PARTIAL_WITH_REMAINDER"
        assert truth.remaining_size == remainder


def test_late_complete_trade_fill_remains_terminal_full():
    truth = VenueOrderTruthReducer.reduce(order_facts=[{"state": "EXPIRED", "remaining_size": "0", "matched_size": "0"}], trade_filled_size="5", command_size="5")
    assert truth.proof_class == "TERMINAL_FILLED"
    assert truth.matched_size == Decimal("5")


def _prefetch(t0, city, reports=()):
    return FastObsPrefetch(eligible=((city, object(), t0.astimezone(ZoneInfo(city.timezone)).date().isoformat()),), reports=reports, freshness_status="fresh_fetch", cache_age_s=0.0, decision_time=t0, ledger_reports=reports)


def test_live_receipt_advances_cut_but_not_historical_prefetch():
    t0 = datetime(2026, 9, 29, 12, tzinfo=UTC)
    t1 = t0 + timedelta(milliseconds=731)
    city = SimpleNamespace(name="fixture", timezone="UTC")
    report = MetarReport("TEST", t0-timedelta(minutes=1), None, 12.3, "METAR", "TEST", first_seen_at=t1, availability_basis="LOCAL_FIRST_SEEN_AFTER_COMPLETE_RESPONSE")
    before = _prefetch(t0, city, (report,))
    live = before.with_live_receipt(t1)
    assert live.decision_time == t1
    assert report.available_at <= live.decision_time
    assert report.available_at > before.decision_time
    assert before.decision_time == t0
    assert live.reports is before.reports
    assert live.ledger_reports is before.ledger_reports


def test_live_receipt_recomputes_city_day_across_midnight():
    t0 = datetime(2026, 9, 29, 20, 59, 59, 900000, tzinfo=UTC)
    city = SimpleNamespace(name="fixture", timezone="Europe/Helsinki")
    before = _prefetch(t0, city)
    live = before.with_live_receipt(t0+timedelta(milliseconds=200))
    assert before.eligible[0][2] == "2026-09-29"
    assert live.eligible[0][2] == "2026-09-30"


@pytest.mark.parametrize("bad", [datetime(2026,9,29), datetime(2026,9,28,tzinfo=UTC)])
def test_live_receipt_rejects_naive_or_regressing_clock(bad):
    before = _prefetch(datetime(2026,9,29,tzinfo=UTC), SimpleNamespace(name="fixture",timezone="UTC"))
    with pytest.raises(ValueError):
        before.with_live_receipt(bad)


def test_live_tick_stages_actual_post_fetch_receipt(monkeypatch):
    import src.ingest_main as ingest
    t0 = datetime(2026,9,29,12,tzinfo=UTC)
    t1 = t0+timedelta(milliseconds=731)
    times = iter([t0,t1])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return next(times)
    city = SimpleNamespace(name="fixture",timezone="UTC")
    pf = _prefetch(t0,city,(object(),))
    emitter = SimpleNamespace(ledger_report_keys_loaded=lambda:True,prefetch=lambda **_:pf,prefetched_events_evaluated=lambda _:False)
    staged = []
    monkeypatch.setattr(ingest,"datetime",Clock)
    monkeypatch.setattr("src.config.runtime_cities",lambda:[city])
    monkeypatch.setattr(ingest,"_day0_metar_emitter",lambda:emitter)
    monkeypatch.setattr(ingest,"_day0_priority_scopes",lambda:())
    monkeypatch.setattr(ingest,"_day0_source_family_admission",lambda _:None)
    monkeypatch.setattr(ingest,"_stage_day0_metar_commit",lambda prefetch,**kw:staged.append((prefetch,kw)))
    monkeypatch.setattr(ingest,"_commit_or_schedule_day0_metar",lambda **_: {"status":"COMMITTED"})
    assert ingest._day0_metar_source_clock_tick()["status"] == "COMMITTED"
    assert staged[0][0].decision_time == t1
    assert staged[0][1]["received_at"] == t1.isoformat()


@pytest.mark.parametrize("day,hours", [("2026-03-29",23),("2026-10-25",25)])
def test_fast_extreme_uses_civil_day_not_fixed_utc_duration(monkeypatch, day, hours):
    from src.config import cities_by_name
    from src.data.day0_fast_obs import latest_fast_station_extreme_c
    city = SimpleNamespace(name="clock-fixture", wu_station="TEST", settlement_unit="C",
                           settlement_source_type="noaa", timezone="Europe/Helsinki")
    monkeypatch.setitem(cities_by_name, city.name, city)
    seen = []
    class Conn:
        def execute(self, sql, params=()):
            if params: seen.append(params)
            return self
        def fetchone(self): return None
        def fetchall(self): return []
    assert latest_fast_station_extreme_c(Conn(), city=city.name, target_date=day,
        decision_time=datetime(2026,11,1,tzinfo=UTC), metric="high") is None
    start,end = map(datetime.fromisoformat,seen[0][3:5])
    assert (end-start).total_seconds() == hours*3600


def test_station_route_not_city_name_and_shared_budget(tmp_path):
    import json
    from src.data.physical_current_sources import (REGISTRY_PATH, load_physical_current_sources,
        physical_current_sources_for_city, physical_current_poll_seconds)
    alias = SimpleNamespace(name="arbitrary-label",wu_station="EFHK",settlement_unit="C",settlement_source_type="noaa")
    assert len(physical_current_sources_for_city(alias)) == 1
    assert physical_current_poll_seconds() == 11
    alias.wu_station = "EFHF"
    assert physical_current_sources_for_city(alias) == ()
    data = json.loads(REGISTRY_PATH.read_text())
    other = dict(data["sources"][0], station_id="TEST")
    data["sources"] = [data["sources"][0], other]
    path = tmp_path/"two_stations.json"
    path.write_text(json.dumps(data))
    routes, period = load_physical_current_sources(path)
    assert len(routes) == 2 and period == 22
    assert 2*len(routes)*86400/period <= 20000*0.8
    assert 2*len(routes)*300/period <= 600*0.8


@pytest.mark.parametrize("key,value", [("role","settlement"),("schema_version",2)])
def test_optional_source_registry_cannot_promote_itself(tmp_path,key,value):
    import json
    from src.data.physical_current_sources import REGISTRY_PATH,load_physical_current_sources
    data = json.loads(REGISTRY_PATH.read_text()); data[key] = value
    path=tmp_path/"bad.json"; path.write_text(json.dumps(data))
    with pytest.raises(ValueError): load_physical_current_sources(path)


@pytest.mark.parametrize("payload", ["[]", "null", '{"schema_version":1,"role":"physical_current_only","sources":[],"providers":{"fmi_wfs":{"budget_fraction":0.8,"requests_per_day":Infinity,"requests_per_five_minutes":600,"requests_per_poll":2}}}'])
def test_malformed_optional_registry_does_not_block_serving(tmp_path, monkeypatch, payload):
    import src.data.physical_current_sources as registry
    path = tmp_path / "invalid.json"
    path.write_text(payload)
    monkeypatch.setattr(registry, "REGISTRY_PATH", path)
    registry.load_physical_current_sources.cache_clear()
    try:
        assert registry.physical_current_sources_for_city(SimpleNamespace()) == ()
        assert registry.physical_current_poll_seconds() == 300.0
    finally:
        registry.load_physical_current_sources.cache_clear()


def test_awc_actual_receipt_is_not_provider_publication():
    from src.data.day0_fast_obs import parse_metar_api_payload
    receipt = datetime(2026,9,29,12,3,4,567000,tzinfo=UTC)
    rows = [{"icaoId":"TEST","obsTime":1790683200,"receiptTime":"2026-09-29T12:02:00Z",
             "temp":12.3,"metarType":"METAR","rawOb":"TEST 291200Z 00000KT 12/10"}]
    report = parse_metar_api_payload(rows,first_seen_at=receipt)[0]
    assert report.first_seen_at == receipt
    assert report.receipt_time == datetime(2026,9,29,12,2,tzinfo=UTC)


def test_alias_cities_share_one_provider_fetch_even_when_it_fails(monkeypatch):
    import src.ingest_main as ingest
    import src.data.fmi_airport_temperature as fmi
    cities = {name: SimpleNamespace(name=name, wu_station="EFHK", settlement_unit="C",
              settlement_source_type="noaa", timezone="Europe/Helsinki") for name in ("alias-a","alias-b")}
    calls = []
    def unavailable(**kwargs):
        calls.append(kwargs)
        raise ValueError("synthetic optional-source failure")
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: cities)
    monkeypatch.setattr(fmi,"fetch_temperature",unavailable)
    result=ingest._day0_fmi_temperature_tick()
    assert len(calls)==1
    assert len(result["reports"])==2
    assert all(r["status"]=="SOURCE_UNAVAILABLE" for r in result["reports"])


@pytest.mark.parametrize("module_name", ["src.execution.harvester", "src.ingest.harvester_truth_writer"])
@pytest.mark.parametrize("station,accepted", [("KBKF", True), (" kbkf:METAR ", True), ("KDEN", False), (None, False), ("", False)])
def test_both_settlement_writers_bind_the_actual_configured_station(module_name, station, accepted):
    import importlib
    from src.config import cities_by_name
    module = importlib.import_module(module_name)
    assert module._station_matches_city(station, cities_by_name["Denver"]) is accepted


@pytest.mark.parametrize("module_name", ["src.execution.harvester", "src.ingest.harvester_truth_writer"])
def test_both_settlement_writers_keep_hko_identity_separate_from_airport(module_name):
    import importlib
    from src.config import cities_by_name
    module = importlib.import_module(module_name)
    city = cities_by_name["Hong Kong"]
    assert module._station_matches_city("HKO", city)
    assert not module._station_matches_city("VHHH", city)
    assert not module._station_matches_city(None, city)


def test_faithfulness_import_failure_is_physical_evidence_not_zero_divergence(monkeypatch):
    import src.data.day0_oracle_anomaly as anomaly
    from src.data.day0_fast_obs import fast_obs_source_for_city, fast_obs_to_day0_observation, running_extremes_for_local_day
    monkeypatch.delattr(anomaly, "metar_margin_units_for_city")
    now=datetime(2026,9,29,12,tzinfo=UTC)
    city=SimpleNamespace(name="test-city",wu_station="TEST",settlement_source_type="noaa",settlement_unit="C",timezone="UTC")
    source=fast_obs_source_for_city(city,target_date="2026-09-29")
    assert source is not None and source.faithfulness_known is False
    report=MetarReport("TEST",now-timedelta(minutes=1),now,15.0,"METAR","TEST 291159Z 00000KT 15/10")
    extrema=running_extremes_for_local_day([report],city=city,target_date="2026-09-29",as_of=now)
    obs=fast_obs_to_day0_observation(city=city,extremes=extrema,metric="high",source=source)
    assert obs["source_authorized_status"]=="UNAUTHORIZED"
    assert obs["live_authority_status"]=="blocked"
    assert obs["current_observation_temp_c"]==15.0
