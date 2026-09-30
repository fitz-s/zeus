# Created: 2026-09-29
# Last reused/audited: 2026-09-30
# Authority: REQ-20260929-223929-bf51a2; recorded provider responses, 2026-09-30 UTC.
"""Real response shapes; wrong units/stations/time and false grade are rejected."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import pytest
from src.data.physical_current_sources import load_physical_current_sources, REGISTRY_PATH
from src.data.station_temperature_adapters import parse_station_payload, valid_station_print

ROOT = Path(__file__).parent / "fixtures" / "station_temperature"
NOW = datetime(2026, 9, 30, 5, tzinfo=timezone.utc)

@pytest.mark.parametrize("provider,fixture", [
    ("jma_amedas", "jma"), ("eccc_swob", "eccc"), ("imgw_synop", "imgw"),
    ("dwd_cdc", "dwd"), ("wu_station_current", "wu_current"),
    ("wu_station_history", "wu_history"), ("knmi_observations", "knmi"),
])
def test_recorded_provider_response_and_ledger_validation(provider, fixture):
    if provider == "knmi_observations":
        pytest.importorskip("netCDF4")
    route = next(r for r in load_physical_current_sources()[0] if r.provider == provider)
    samples = parse_station_payload(route, (ROOT/(fixture+".bin")).read_bytes(), received_at=NOW)
    assert samples and all(s.observed_at <= s.fetched_at == NOW for s in samples)
    sample = samples[-1]
    assert valid_station_print(route, sample.raw_report, observed_at=sample.observed_at, value=sample.temperature_c)
    assert not valid_station_print(replace(route, station_id="XXXX"), sample.raw_report,
                                   observed_at=sample.observed_at, value=sample.temperature_c)
    assert not valid_station_print(route, sample.raw_report, observed_at=sample.observed_at,
                                   value=sample.temperature_c+1)


@pytest.mark.parametrize("change", ["station", "unit", "quality"])
def test_eccc_rejects_foreign_or_bad_observation(change):
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "eccc_swob")
    body = (ROOT/"eccc.bin").read_text()
    if change == "station": body = body.replace('value="CYYZ"', 'value="CYTZ"')
    elif change == "unit": body = body.replace('uom="°C"', 'uom="K"')
    else: body = body.replace('name="qa_summary" uom="unitless" value="100"', 'name="qa_summary" uom="unitless" value="200"')
    with pytest.raises(ValueError): parse_station_payload(route, body.encode(), received_at=NOW)


def test_jma_bad_quality_and_future_values_cannot_enter():
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "jma_amedas")
    body = json.dumps({"20260930135000": {"temp": [18.8, 1]},
                       "20260930150000": {"temp": [19.0, 0]}}).encode()
    assert parse_station_payload(route, body, received_at=NOW) == ()


def test_systematic_mismatch_cannot_be_marked_settlement_grade(tmp_path):
    data = json.loads(REGISTRY_PATH.read_text())
    next(r for r in data["sources"] if r["provider"] == "dwd_cdc")["settlement_grade"] = True
    path=tmp_path/"registry.json";path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="NOT_PROVEN"): load_physical_current_sources(path)


@pytest.mark.parametrize('provider',['fmi_wfs','jma_amedas','eccc_swob','imgw_synop','dwd_cdc','knmi_observations'])
def test_same_value_rule_is_provider_independent(tmp_path, provider):
    data=json.loads(REGISTRY_PATH.read_text())
    row=next(r for r in data['sources'] if r['provider']==provider)
    row['settlement_grade']=True
    # Synthetic admission test only; this is not a measurement or production promotion.
    row['value_identity_proof']={'n_pairs':8,'n_exact':8,'mismatches':[]}
    path=tmp_path/'equal.json';path.write_text(json.dumps(data))
    route=next(r for r in load_physical_current_sources(path)[0] if r.provider==provider)
    assert route.settlement_grade is True
    row['value_identity_proof']['n_exact']=7
    bad=tmp_path/'different.json';bad.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='NOT_PROVEN'):load_physical_current_sources(bad)


def test_every_configured_promotion_matches_committed_pair_evidence():
    report=json.loads((REGISTRY_PATH.parents[1]/'artifacts/fast_obs_audit/source_identity_report.json').read_text())
    measured={(r['station'],r['channel']):r for r in report['comparisons']}
    for row in json.loads(REGISTRY_PATH.read_text())['sources']:
        proof=row['value_identity_proof']
        if row['provider'] == 'noaa_wrh':
            import gzip
            native_rows=json.loads(gzip.decompress((ROOT/'us_resolver_precision.json.gz').read_bytes()))
            n=sum(r['station']==row['station_id'] for r in native_rows)
            assert proof['n_pairs']==proof['n_exact']==n
            assert row['settlement_grade'] and row['unit']=='F'
            assert row['source_channel']=='noaa_wrh_'+row['station_id'].lower()
            continue  # Existing native resolver, not an alternate-channel promotion.
        actual=measured[(row['station_id'],proof['channel'])]
        assert (proof['n_pairs'],proof['n_exact'])==(actual['n_pairs'],actual['n_exact'])
        assert row['settlement_grade']==actual['value_identity_proven']
    imgw=next(r for r in load_physical_current_sources()[0] if r.provider=='imgw_synop')
    assert imgw.settlement_grade is False  # Exact-time observed mismatch, not geography.


def test_new_station_channel_reaches_causal_current_temperature_reader():
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    route=next(r for r in load_physical_current_sources()[0] if r.provider=="jma_amedas")
    sample=parse_station_payload(route,(ROOT/"jma.bin").read_bytes(),received_at=NOW)[-1]
    city=SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C",
                         settlement_source_type="noaa", wu_station="RJTT")
    conn=sqlite3.connect(":memory:");ensure_table(conn)
    append_print(conn,city=city.name,station_id=route.station_id,source_channel=route.source_channel,
        publish_ts_utc=sample.observed_at.isoformat(),value_native=sample.temperature_c,unit="C",
        fetched_at_utc=NOW.isoformat(),raw_report=sample.raw_report)
    assert read_day0_current_temperature_state(conn=conn,city=city,target_date="2026-09-30",decision_time=NOW-timedelta(seconds=1)) is None
    state=read_day0_current_temperature_state(conn=conn,city=city,target_date="2026-09-30",decision_time=NOW)
    assert state is not None and state.source==route.source_channel
    assert state.value_native==sample.temperature_c
    conn.close()


def test_settlement_grade_registry_channel_wins_same_time_tie():
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.state.schema.observation_prints_schema import ensure_table, append_print

    route=next(r for r in load_physical_current_sources()[0] if r.provider=="jma_amedas")
    sample=parse_station_payload(route,(ROOT/"jma.bin").read_bytes(),received_at=NOW)[-1]
    city=SimpleNamespace(name="Tokyo", timezone="Asia/Tokyo", settlement_unit="C",
                         settlement_source_type="noaa", wu_station="RJTT")
    conn=sqlite3.connect(":memory:");ensure_table(conn)
    append_print(
        conn, city=city.name, station_id="RJTT", source_channel="aviationweather_metar",
        publish_ts_utc=(sample.observed_at+timedelta(seconds=10)).isoformat(),
        value_native=19.0, unit="C",
        fetched_at_utc=(NOW-timedelta(seconds=10)).isoformat(),
        raw_report=f"METAR RJTT {sample.observed_at:%d%H%M}Z 01008KT 9999 19/18 Q1014",
    )
    append_print(
        conn, city=city.name, station_id=route.station_id,
        source_channel=route.source_channel,
        publish_ts_utc=sample.observed_at.isoformat(),
        value_native=sample.temperature_c, unit="C",
        fetched_at_utc=(NOW-timedelta(seconds=20)).isoformat(),
        raw_report=sample.raw_report,
    )
    state=read_day0_current_temperature_state(
        conn=conn, city=city, target_date="2026-09-30", decision_time=NOW
    )
    assert state is not None
    assert state.source == route.source_channel
    assert state.value_native == sample.temperature_c
    conn.close()


def test_noaa_resolver_row_outranks_same_time_physical_only_fmi():
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.fmi_airport_temperature import (
        DEFAULT_STATION, TEMPERATURE_PROPERTY,
    )
    from src.state.schema.observation_prints_schema import ensure_table, append_print

    observed=datetime(2026,9,30,3,20,tzinfo=timezone.utc)
    city=SimpleNamespace(name="Helsinki", timezone="Europe/Helsinki", settlement_unit="C",
                         settlement_source_type="noaa", wu_station="EFHK")
    conn=sqlite3.connect(":memory:");ensure_table(conn)
    fmi_raw=json.dumps({
        "fmisid":DEFAULT_STATION.fmisid,"wmo":DEFAULT_STATION.wmo,
        "station":DEFAULT_STATION.name,"property":TEMPERATURE_PROPERTY,
        "unit":"degC","availability":"local_fetch_only",
        "observed_at":observed.isoformat(),"value":"7.6",
    })
    append_print(
        conn,city="Helsinki",station_id="EFHK",
        source_channel="fmi_airport_temperature",
        publish_ts_utc=observed.isoformat(),value_native=7.6,unit="C",
        fetched_at_utc=(observed+timedelta(minutes=2)).isoformat(),raw_report=fmi_raw,
    )
    append_print(
        conn,city="Helsinki",station_id="EFHK",
        source_channel="noaa_wrh_efhk",
        publish_ts_utc=observed.isoformat(),value_native=7.0,unit="C",
        fetched_at_utc=(observed+timedelta(minutes=3)).isoformat(),raw_report="{}",
    )
    state=read_day0_current_temperature_state(
        conn=conn,city=city,target_date="2026-09-30",
        decision_time=observed+timedelta(minutes=4),
    )
    assert state is not None
    assert state.source == "noaa_wrh_efhk"
    assert state.value_native == 7.0
    conn.close()


@pytest.mark.parametrize("city", [
    "Atlanta", "Austin", "Chicago", "Dallas", "Denver", "Houston",
    "Los Angeles", "Miami", "NYC", "San Francisco", "Seattle",
])
def test_us_resolver_precision_is_native_for_every_city(city):
    import gzip
    from src.config import cities_by_name
    from src.data.station_temperature_adapters import native_sample_value
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    from zoneinfo import ZoneInfo

    cfg = cities_by_name[city]
    route = next(r for r in load_physical_current_sources()[0]
                 if r.provider == "noaa_wrh" and r.station_id == cfg.wu_station)
    pairs = [r for r in json.loads(gzip.decompress((ROOT/"us_resolver_precision.json.gz").read_bytes()))
             if r["city"] == city]
    assert len(pairs) >= 48
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; ensure_table(conn)
    for pair in pairs:
        observed = datetime.fromisoformat(pair["observed_at"])
        receipt = observed + timedelta(minutes=5)
        payload = {"UNITS":{"air_temp":"Fahrenheit"},"STATION":[{
            "STID":cfg.wu_station,"OBSERVATIONS":{
                "date_time":[observed.strftime("%Y-%m-%dT%H:%M:%S%z")],
                "air_temp_set_1":[pair["resolver_f"]],
                "sea_level_pressure_set_1":[1013 if pair["routine"] else None],
                "metar_set_1":[pair["resolver_raw"]],
            }}]}
        samples = parse_station_payload(route, json.dumps(payload).encode(), received_at=receipt)
        assert len(samples) == 1
        sample = samples[0]
        assert sample.unit == "F"
        assert native_sample_value(sample, "F") == pair["resolver_f"]
        assert valid_station_print(route, sample.raw_report, observed_at=observed, value=pair["resolver_f"])
        assert append_print(conn,city=city,station_id=cfg.wu_station,source_channel=route.source_channel,
            publish_ts_utc=observed.isoformat(),value_native=sample.value_native,unit="F",
            fetched_at_utc=receipt.isoformat(),raw_report=sample.raw_report)
    last = max(pairs,key=lambda r:r["observed_at"])
    observed = datetime.fromisoformat(last["observed_at"])
    at = observed + timedelta(minutes=6)
    day = observed.astimezone(ZoneInfo(cfg.timezone)).date().isoformat()
    state = read_day0_current_temperature_state(conn=conn,city=cfg,target_date=day,decision_time=at)
    assert state and state.source == route.source_channel
    assert state.value_native == last["resolver_f"]
    fact = _latest_authorized_day0_fact(conn,city=city,target_date=day,temperature_metric="high",
                                       decision_time=at,require_settlement_channel=True)
    assert fact is not None
    conn.close()


def test_us_speci_type_does_not_select_resolver_precision():
    import gzip
    pairs = json.loads(gzip.decompress((ROOT/"us_resolver_precision.json.gz").read_bytes()))
    chicago = next(p for p in pairs if p["city"] == "Chicago" and p["observed_at"] == "2026-09-30T15:30:00+00:00")
    from src.data.metar_temperature import metar_t_group_temperature_c
    assert metar_t_group_temperature_c(chicago["raw"]) == 16.7
    assert chicago["resolver_f"] == 62.6
    # Same report class, different upstream precision: no per-city or SPECI heuristic.
    precise_specials = [p for p in pairs if p["city"] == "Seattle" and p["awc_type"] == "SPECI"
                        and p["t_group_match"] and not p["body_match"]]
    assert precise_specials


def test_wrh_batch_shares_acquisition_across_registered_us_stations(monkeypatch):
    import httpx
    from src.data import station_temperature_adapters as adapters
    from src.data import noaa_wrh_timeseries as wrh
    routes = [r for r in load_physical_current_sources()[0] if r.provider == "noaa_wrh"]
    calls = []
    class Client:
        def get(self,url,**kwargs):
            calls.append(kwargs)
            return httpx.Response(200,json={"UNITS":{"air_temp":"Fahrenheit"},"STATION":[]},request=httpx.Request("GET",url))
    monkeypatch.setattr(wrh,"fetch_wrh_token",lambda:"test-token-not-persisted")
    adapters._WRH_BATCH_CACHE.clear(); client=Client()
    for route in routes:
        data, receipt = adapters._fetch_wrh_batch(route,client)
        assert receipt.tzinfo is not None and data["STATION"] == []
    assert len(calls) == 1
    assert set(calls[0]["params"]["STID"].split(",")) == {r.station_id for r in routes}
    adapters._WRH_BATCH_CACHE.clear()


def test_wrh_rate_limit_is_deferred_without_secret_in_error(monkeypatch):
    import httpx
    from src.data import station_temperature_adapters as adapters
    from src.data import noaa_wrh_timeseries as wrh
    route=next(r for r in load_physical_current_sources()[0] if r.provider=="noaa_wrh")
    calls=[]
    class Client:
        def get(self,url,**kwargs):
            calls.append(1)
            return httpx.Response(429,headers={"Retry-After":"600"},request=httpx.Request("GET",url,params={"token":"private-test-value"}))
    monkeypatch.setattr(wrh,"fetch_wrh_token",lambda:"private-test-value")
    adapters._WRH_BATCH_CACHE.clear(); client=Client()
    for _ in range(2):
        with pytest.raises(ValueError,match="TRANSPORT_DEFERRED") as exc:
            adapters._fetch_wrh_batch(route,client)
        assert "private-test-value" not in str(exc.value)
    assert len(calls)==1
    adapters._WRH_BATCH_CACHE.clear()


def test_native_fahrenheit_ingest_reseeds_after_durable_world_commit(monkeypatch,tmp_path):
    import threading
    from src.config import cities_by_name
    from src.data import station_temperature_adapters as adapters
    from src.data import replacement_forecast_production as production
    from src.state import db, write_coordinator as coordinator
    from src.state.schema.observation_prints_schema import ensure_table
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    import src.ingest_main as ingest
    from zoneinfo import ZoneInfo
    city=cities_by_name['Chicago']
    route=next(r for r in load_physical_current_sources()[0] if r.provider=='noaa_wrh' and r.station_id==city.wu_station)
    now=datetime.now(timezone.utc);observed=now-timedelta(minutes=2)
    sample=adapters._sample(route,observed,62.6,now,'a'*64)
    path=tmp_path/'world.sqlite'
    with sqlite3.connect(path) as conn:ensure_table(conn)
    class Lease:
        def __enter__(self):return self
        def __exit__(self,*_):return False
        def record_commit(self,**kw):assert kw['rows_changed']==1
    monkeypatch.setattr(adapters,'fetch_station_temperature',lambda *args,**kw:(sample,))
    monkeypatch.setattr(db,'world_write_mutex',lambda:threading.Lock())
    monkeypatch.setattr(db,'get_world_connection',lambda **kw:sqlite3.connect(path))
    monkeypatch.setattr(coordinator,'default_runtime_write_coordinator',lambda:SimpleNamespace(lease=lambda *a,**kw:Lease()))
    monkeypatch.setattr(production,'_replacement_forecast_live_materialization_queue_config',lambda:{})
    monkeypatch.setattr('src.data.physical_current_delivery.current_temperature_priority_families',lambda:{})
    calls=[]
    def enqueue(cfg,**kw):
        with sqlite3.connect(path) as check:
            row=check.execute('SELECT value_native,unit FROM observation_prints').fetchone()
            assert row==(62.6,'F')
        calls.append(kw)
        return {'status':'FUSION_UPGRADE_TRIGGER'}
    monkeypatch.setattr(production,'_enqueue_fusion_upgrade_reseeds_if_needed',enqueue)
    monkeypatch.setattr(ingest,'_physical_current_pending_wakes',set())
    result=ingest._day0_current_temperature_source_tick(city,route)
    assert result['status']=='COMMITTED' and result['advanced']
    assert result['clock_trace']['input_identity']['value_native']==62.6
    assert len(calls)==1
    day=observed.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    with sqlite3.connect(path) as conn:
        state=read_day0_current_temperature_state(conn=conn,city=city,target_date=day,decision_time=datetime.now(timezone.utc))
    assert state and state.value_native==62.6
