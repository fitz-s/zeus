# Created: 2026-09-29
# Last reused/audited: 2026-10-01
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

def _public_route(provider="mgm_metar", station="LTAC"):
    from src.data.physical_current_sources import PhysicalCurrentSource, SourceRole
    from src.data.station_temperature_adapters import CHANNELS
    return PhysicalCurrentSource(provider, CHANNELS[provider], station, ("noaa",),
        "C", 60., None, {"provider_station": station}, SourceRole.FAST_ADMISSION)


def _public_fixture(name):
    import gzip
    body = gzip.decompress((ROOT/(name+".html.gz")).read_bytes())
    meta = json.loads((ROOT/(name+".meta.json")).read_text())
    return body, datetime.fromisoformat(meta["receipt_at"])


@pytest.mark.parametrize("provider,station,name", [
    ("mgm_metar", "LTAC", "mgm_public"),
    ("imd_olbs_metar", "VILK", "imd_olbs_public"),
])
def test_public_native_metar_real_response_reaches_current_reader(monkeypatch,provider,station,name):
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    route=_public_route(provider,station);body,receipt=_public_fixture(name)
    samples=parse_station_payload(route,body,received_at=receipt)
    assert samples and all(s.observed_at <= receipt for s in samples)
    s=samples[-1]
    assert valid_station_print(route,s.raw_report,observed_at=s.observed_at,value=s.value_native)
    monkeypatch.setattr("src.data.physical_current_sources.physical_current_sources_for_city",lambda _:(route,))
    from src.config import cities
    from zoneinfo import ZoneInfo
    city=next(c for c in cities if c.wu_station==station)
    local_day=s.observed_at.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    conn=sqlite3.connect(":memory:");ensure_table(conn)
    append_print(conn,city=city.name,station_id=station,source_channel=route.source_channel,
        publish_ts_utc=s.observed_at.isoformat(),value_native=s.value_native,unit="C",
        fetched_at_utc=receipt.isoformat(),raw_report=s.raw_report)
    assert read_day0_current_temperature_state(conn=conn,city=city,
        target_date=local_day,decision_time=receipt-timedelta(microseconds=1)) is None
    state=read_day0_current_temperature_state(conn=conn,city=city,
        target_date=local_day,decision_time=receipt)
    assert state is not None and state.source==route.source_channel and state.value_native==s.value_native
    conn.close()


def _mgm_body(rows, station="LTAC"):
    return ('<script id="__NEXT_DATA__">'+json.dumps({"props":{"pageProps":{"response":[
        {"istInfo":{"icao":station},"data":rows}]}}})+'</script>').encode()


def _mgm_row(raw="METAR LTAC 302220Z 05010KT CAVOK 12/10 Q1023",**changes):
    d={"stationIcaoCode":"LTAC","observationText":raw,
       "observationTimeNormal":"2026-09-30T22:20:00.500+00:00"}
    d.update(changes);return d


@pytest.mark.parametrize("change,error", [
    ({"stationIcaoCode":"LTFM"},"STATION_ID_MISMATCH"),
    ({"observationText":"METAR LTFM 302220Z 12/10 Q1023"},"STATION_CLOCK"),
    ({"observationTimeNormal":"2026-09-30T22:50:00+00:00"},"CLOCK_MISMATCH"),
    ({"observationTimeNormal":"2026-09-30T22:20:00"},"NAIVE"),
])
def test_mgm_identity_and_valid_clock_mismatch_reject(change,error):
    with pytest.raises(ValueError,match=error):
        parse_station_payload(_public_route(),_mgm_body([_mgm_row(**change)]),
            received_at=datetime(2026,9,30,23,tzinfo=timezone.utc))


def test_mgm_conflicting_same_clock_is_not_highest_value_wins():
    rows=[_mgm_row(),_mgm_row(raw="METAR LTAC 302220Z 05010KT CAVOK 13/10 Q1023")]
    with pytest.raises(ValueError,match="VERSION_CONFLICT"):
        parse_station_payload(_public_route(),_mgm_body(rows),received_at=datetime(2026,9,30,23,tzinfo=timezone.utc))


def test_mgm_future_or_nil_is_not_servable():
    stamp=datetime(2026,9,30,22,tzinfo=timezone.utc)
    assert parse_station_payload(_public_route(),_mgm_body([_mgm_row()]),received_at=stamp)==()
    assert parse_station_payload(_public_route(),_mgm_body([_mgm_row(raw="METAR LTAC 302220Z NIL")]),
        received_at=stamp+timedelta(hours=1))==()


def test_imd_public_form_contract_and_cache(monkeypatch):
    from src.data.station_temperature_adapters import _fetch_public_metar, _PUBLIC_METAR_CACHE
    route=_public_route('imd_olbs_metar','VILK');body,_=_public_fixture('imd_olbs_public')
    import httpx
    from urllib.parse import parse_qs
    calls=[];_PUBLIC_METAR_CACHE.clear()
    def handler(request):
        assert request.method=='POST','The public query requires POST'
        assert str(request.url)=='https://olbs.amsschennai.gov.in/nsweb/FlightBriefing/showopmetquery.php'
        assert parse_qs(request.content.decode())=={'icaos':['VILK'],'type':['metar']}
        calls.append(request);return httpx.Response(200,content=body)
    client=httpx.Client(transport=httpx.MockTransport(handler))
    first=_fetch_public_metar(route,client);second=_fetch_public_metar(route,client)
    assert first==second and len(calls)==1


def test_public_mgm_batch_never_exceeds_ten_stations(monkeypatch):
    from src.data.station_temperature_adapters import _fetch_public_metar,_PUBLIC_METAR_CACHE
    routes=tuple(_public_route(station="AA"+chr(65+i//26)+chr(65+i%26)) for i in range(21))
    monkeypatch.setattr("src.data.physical_current_sources.load_physical_current_sources",lambda:(routes,60.))
    import httpx
    _PUBLIC_METAR_CACHE.clear();calls=[]
    def handler(request):
        calls.append(request);assert request.url.copy_with(query=None)=="https://rasat.mgm.gov.tr/result"
        assert len(request.url.params.get_list("stations"))<=10
        return httpx.Response(200,content=b'<html></html>')
    client=httpx.Client(transport=httpx.MockTransport(handler))
    for route in routes:_fetch_public_metar(route,client)
    assert len(calls)==3


@pytest.mark.parametrize('station',['LTAC','LTFM'])
def test_mgm_admission_binds_exact_value_and_all_current_speed_paths(station):
    data=json.loads(REGISTRY_PATH.read_text())
    row=next(r for r in data['sources'] if r['provider']=='mgm_metar' and r['station_id']==station)
    proof=row['value_identity_proof']
    report=json.loads((REGISTRY_PATH.parents[1]/proof['report_path']).read_text())
    measured=next(r for r in report if r['station']==station)
    assert row['role']=='fast_admission' and measured['n_pairs']==measured['n_exact']>0
    assert measured['mismatches']==[]
    race=row['latency_evidence']['first_proven_lead']
    assert any(p['time']==race['observation'] and p['match'] for p in measured['pairs'])
    required=set(row['latency_evidence']['required_comparators'])
    assert {'awc','resolver'} <= required
    for channel in required:
        other=next(p for p in race['comparators'] if p['channel']==channel)
        assert other['interval']['observed_at']==race['observation']
        assert race['candidate']['lag_upper_ms']<other['interval']['lag_lower_ms']
    assert row['minimum_poll_seconds']>=60
    assert row['identity']['provider_station']==station


@pytest.mark.parametrize("provider", ["jma_amedas", "eccc_swob"])
def test_promoted_origins_have_extended_identity_and_measured_latency_lead(provider):
    """A historical fetch proves equality, not publication latency; require both artifacts."""
    data = json.loads(REGISTRY_PATH.read_text())
    row = next(r for r in data["sources"] if r["provider"] == provider)
    proof = row["value_identity_proof"]
    assert row["role"] == "fast_admission"
    assert proof["n_pairs"] >= 48 and proof["n_exact"] == proof["n_pairs"]
    assert proof["mismatches"] == []
    race = row["latency_evidence"]["first_proven_lead"]
    awc = next(c for c in race["comparators"] if c["channel"] == "awc")
    assert awc["verdict"] == "FASTER"
    assert race["candidate"]["observed_at"] == awc["interval"]["observed_at"]
    assert race["candidate"]["lag_upper_ms"] < awc["interval"]["lag_lower_ms"]


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


def test_systematic_mismatch_cannot_be_admitted(tmp_path):
    data = json.loads(REGISTRY_PATH.read_text())
    next(r for r in data["sources"] if r["provider"] == "dwd_cdc")["role"] = "fast_admission"
    path=tmp_path/"registry.json";path.write_text(json.dumps(data))
    assert all(r.provider != "dwd_cdc" for r in load_physical_current_sources(path)[0])


@pytest.mark.parametrize('provider',['fmi_wfs','imgw_synop','dwd_cdc','knmi_observations'])
def test_counts_only_proof_cannot_admit_any_provider(tmp_path, provider):
    data=json.loads(REGISTRY_PATH.read_text())
    row=next(r for r in data['sources'] if r['provider']==provider)
    row['role']='fast_admission'
    # Synthetic forged counts: equal, but bound to nothing and with no race.
    row['value_identity_proof']={'n_pairs':8,'n_exact':8,'mismatches':[]}
    path=tmp_path/'equal.json';path.write_text(json.dumps(data))
    assert all(r.provider != provider for r in load_physical_current_sources(path)[0])


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
            assert row['role']=='canonical_resolver' and row['unit']=='F'
            assert row['source_channel']=='noaa_wrh_'+row['station_id'].lower()
            continue  # Existing native resolver, not an alternate-channel promotion.
        evidence_path=proof.get('report_path')
        evidence=json.loads((REGISTRY_PATH.parents[1]/evidence_path).read_text()) if evidence_path else report
        if isinstance(evidence,list):
            matches=[r for r in evidence if r['city']==proof['city'] and r['channel']==proof['channel']]
            assert len(matches)==1
            actual=matches[0]
            proven=actual['n_pairs']>0 and actual['n_pairs']==actual['n_exact'] and not actual['mismatches']
            assert len({p['time'] for p in actual['pairs']})==actual['n_pairs']
        else:
            actual=measured[(row['station_id'],proof['channel'])]
            proven=actual['value_identity_proven']
        assert (proof['n_pairs'],proof['n_exact'])==(actual['n_pairs'],actual['n_exact'])
        assert (row['role']!='physical_only')==proven
    imgw=next(r for r in load_physical_current_sources()[0] if r.provider=='imgw_synop')
    assert not imgw.settlement_authorized  # Exact-time observed mismatch, not geography.


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
    def handler(request):
        calls.append({"params": dict(request.url.params)})
        return httpx.Response(200,json={"UNITS":{"air_temp":"Fahrenheit"},"STATION":[]})
    monkeypatch.setattr(wrh,"fetch_wrh_token",lambda:"test-token-not-persisted")
    adapters._WRH_BATCH_CACHE.clear(); client=httpx.Client(transport=httpx.MockTransport(handler))
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
    def handler(request):
        calls.append(1)
        return httpx.Response(429,headers={"Retry-After":"600"})
    monkeypatch.setattr(wrh,"fetch_wrh_token",lambda:"private-test-value")
    adapters._WRH_BATCH_CACHE.clear(); client=httpx.Client(transport=httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(ValueError,match="TRANSPORT_DEFERRED") as exc:
            adapters._fetch_wrh_batch(route,client)
        assert "private-test-value" not in str(exc.value)
    assert len(calls)==1
    adapters._WRH_BATCH_CACHE.clear()


@pytest.mark.parametrize("city_name,provider,value,unit", [
    ("Chicago","noaa_wrh",62.6,"F"),
    ("Lucknow","imd_olbs_metar",24.0,"C"),
    ("Ankara","mgm_metar",12.0,"C"),
    ("Istanbul","mgm_metar",19.0,"C"),
])
def test_native_temperature_ingest_reseeds_after_durable_world_commit(monkeypatch,tmp_path,city_name,provider,value,unit):
    import threading
    from src.config import cities_by_name
    from src.data import station_temperature_adapters as adapters
    from src.data import replacement_forecast_production as production
    from src.state import db, write_coordinator as coordinator
    from src.state.schema.observation_prints_schema import ensure_table
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    import src.ingest_main as ingest
    from zoneinfo import ZoneInfo
    city=cities_by_name[city_name]
    route=next(r for r in load_physical_current_sources()[0] if r.provider==provider and r.station_id==city.wu_station)
    now=datetime.now(timezone.utc);observed=now-timedelta(minutes=2)
    sample=adapters._sample(route,observed,value,now,'a'*64)
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
            assert row==(value,unit)
        calls.append(kw)
        return {'status':'FUSION_UPGRADE_TRIGGER'}
    monkeypatch.setattr(production,'_enqueue_fusion_upgrade_reseeds_if_needed',enqueue)
    monkeypatch.setattr(ingest,'_physical_current_pending_wakes',set())
    result=ingest._day0_current_temperature_source_tick(city,route)
    assert result['status']=='COMMITTED' and result['advanced']
    assert result['clock_trace']['input_identity']['value_native']==value
    assert len(calls)==1
    day=observed.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    with sqlite3.connect(path) as conn:
        state=read_day0_current_temperature_state(conn=conn,city=city,target_date=day,decision_time=datetime.now(timezone.utc))
    assert state and state.value_native==value
    requested_day=now.astimezone(ZoneInfo(city.timezone)).date().isoformat()
    assert set(calls[0]['scopes'])=={(city_name,requested_day,m) for m in ('high','low')}


# ---------------------------------------------------------------------------
# Bounded single-report public METAR parsing (review REQ-20261001-001836-85dd89)
# ---------------------------------------------------------------------------

_IMD_RECEIPT = datetime(2026, 10, 1, 0, 1, tzinfo=timezone.utc)


def _imd_parse(html):
    return parse_station_payload(_public_route('imd_olbs_metar', 'VILK'), html.encode(),
                                 received_at=_IMD_RECEIPT)


@pytest.mark.parametrize("html", [
    # NIL report followed by another station's report in a later element.
    '<p>METAR VILK 010000Z NIL</p><p>METAR VIDP 010000Z 00000KT CAVOK 40/20 Q1010=</p>',
    # Same, inside one text node.
    '<pre>METAR VILK 010000Z NIL METAR VIDP 010000Z 00000KT CAVOK 40/20 Q1010=</pre>',
    # Terminated NIL.
    '<pre>METAR VILK 010000Z NIL=</pre>',
    # The requested report lives only in script/style text.
    '<script>var r="METAR VILK 010000Z 00000KT CAVOK 40/20 Q1010=";</script>',
    '<style>/* METAR VILK 010000Z 00000KT CAVOK 40/20 Q1010= */</style>',
    # Truncated report without its terminator.
    '<pre>METAR VILK 010000Z 00000KT CAVOK 40/20 Q1010</pre>',
])
def test_imd_page_never_borrows_a_value_from_outside_one_report(html):
    assert _imd_parse(html) == ()


def test_imd_report_with_embedded_second_station_header_is_rejected():
    with pytest.raises(ValueError, match="EMBEDDED_REPORT"):
        _imd_parse('<pre>METAR VILK 010000Z 00000KT VIDP 010000Z 40/20 Q1010=</pre>')


def test_imd_single_report_value_and_clock_are_its_own():
    samples = _imd_parse('<pre>METAR VIDP 010000Z 00000KT CAVOK 40/20 Q1010=\n'
                         'METAR VILK 010000Z 26003KT 3000 BR 24/24 Q1010 NOSIG=</pre>')
    assert [(s.observed_at, s.value_native) for s in samples] == [
        (datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc), 24.0)]


def test_imd_recorded_proof_bodies_still_reproduce_their_values():
    import gzip
    root = Path(__file__).parents[1] / "artifacts" / "fast_obs_audit"
    recorded = [s for s in json.loads(gzip.decompress((root / "round4_all_samples.json.gz").read_bytes()))
                if s["channel"] == "imd_olbs_metar"]
    assert recorded
    for sample in recorded:
        body = next(root.glob(f"*/{sample['sha256']}.body.gz"))
        parsed = parse_station_payload(_public_route('imd_olbs_metar', 'VILK'),
            gzip.decompress(body.read_bytes()),
            received_at=datetime.fromisoformat(sample["receipt_at"]))
        assert (datetime.fromisoformat(sample["observed_at"]), sample["value"]) in [
            (s.observed_at, s.value_native) for s in parsed]


def test_mgm_raw_report_carrying_a_second_station_is_rejected():
    raw = "METAR LTAC 302220Z 05010KT LTFM 302220Z 40/10 Q1023"
    with pytest.raises(ValueError, match="EMBEDDED_REPORT"):
        parse_station_payload(_public_route(), _mgm_body([_mgm_row(raw=raw)]),
            received_at=datetime(2026, 9, 30, 23, tzinfo=timezone.utc))


@pytest.mark.parametrize("header,expected", [
    ("inf", 300.0), ("nan", 300.0), ("-inf", 300.0), ("not-a-date", 300.0),
    ("120", 120.0), ("99999999", 3600.0), (None, 300.0),
])
def test_retry_after_is_finite_and_bounded(header, expected):
    from src.data.station_temperature_adapters import _retry_after_seconds
    assert _retry_after_seconds(header, floor=60.0) == expected


def test_retry_after_http_date_is_relative_to_now():
    from email.utils import format_datetime
    from src.data.station_temperature_adapters import _retry_after_seconds
    when = datetime.now(timezone.utc) + timedelta(seconds=900)
    assert 800 <= _retry_after_seconds(format_datetime(when, usegmt=True), floor=60.0) <= 900


def test_oversized_body_is_refused_while_streaming(monkeypatch):
    import httpx
    from src.data import station_temperature_adapters as adapters
    monkeypatch.setattr(adapters, "_RESPONSE_BYTE_LIMIT", 1024)
    seen = []

    def chunks():
        for _ in range(64):
            seen.append(1)
            yield b"x" * 256

    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=chunks())))
    with pytest.raises(ValueError, match="TOO_LARGE"):
        adapters._bounded_body(client, "GET", "https://example.invalid/")
    assert len(seen) < 64  # refused before the whole body was buffered


def test_declared_oversize_is_refused_without_reading(monkeypatch):
    import httpx
    from src.data import station_temperature_adapters as adapters
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"Content-Length": str(adapters._RESPONSE_BYTE_LIMIT + 1)}, content=b"")))
    with pytest.raises(ValueError, match="TOO_LARGE"):
        adapters._bounded_body(client, "GET", "https://example.invalid/")


def test_shared_cache_lock_is_free_during_network_io():
    import httpx
    from src.data import station_temperature_adapters as adapters
    route = _public_route('imd_olbs_metar', 'VILK')
    adapters._PUBLIC_METAR_CACHE.clear()

    def handler(request):
        assert adapters._FETCH_CACHE_LOCK.acquire(blocking=False)
        adapters._FETCH_CACHE_LOCK.release()
        return httpx.Response(200, content=b"<pre></pre>")

    adapters._fetch_public_metar(route, httpx.Client(transport=httpx.MockTransport(handler)))
    adapters._PUBLIC_METAR_CACHE.clear()


def _ltac_registry_row():
    data = json.loads(REGISTRY_PATH.read_text())
    return data, next(r for r in data["sources"] if r["provider"] == "mgm_metar" and r["station_id"] == "LTAC")


def test_every_configured_role_satisfies_its_law():
    from src.data.physical_current_sources import SourceRole, fast_admission_defect
    data = json.loads(REGISTRY_PATH.read_text())
    by_station = {}
    for row in data["sources"]:
        by_station.setdefault(row["station_id"], set()).add(row["provider"])
    loaded = {(r.provider, r.station_id): r.role for r in load_physical_current_sources()[0]}
    for row in data["sources"]:
        assert loaded[(row["provider"], row["station_id"])] is SourceRole(row["role"])
        if row["role"] == "fast_admission":
            rivals = frozenset(by_station[row["station_id"]] - {row["provider"]})
            assert fast_admission_defect(row, rivals) is None, row["provider"]


@pytest.mark.parametrize("forge,reason", [
    ("counts_only", "PROOF_IDENTITY"),
    ("wrong_unit", "PROOF_IDENTITY"),
    ("wrong_station", "PROOF_IDENTITY"),
    ("missing_comparator", "LEAD_NOT_FASTER:resolver"),
    ("unpaired_lead", "LEAD_UNPAIRED"),
    ("lead_value_mismatch", "LEAD_VALUE_MISMATCH"),
    ("overlapping_comparator", "LEAD_NOT_FASTER:awc"),
    ("rival_registry_route", "LEAD_NOT_FASTER:jma_amedas"),
])
def test_invalid_fast_admission_is_omitted_and_incumbents_keep_serving(tmp_path, caplog, forge, reason):
    from src.data.physical_current_sources import fast_admission_defect
    data, row = _ltac_registry_row()
    lead = row["latency_evidence"]["first_proven_lead"]
    rivals = frozenset()
    if forge == "counts_only":
        row["value_identity_proof"] = {"n_pairs": 50, "n_exact": 50, "mismatches": []}
    elif forge == "wrong_unit":
        row["value_identity_proof"]["unit"] = "F"
    elif forge == "wrong_station":
        row["value_identity_proof"]["station"] = "LTFM"
    elif forge == "missing_comparator":
        lead["comparators"] = [c for c in lead["comparators"] if c["channel"] != "resolver"]
    elif forge == "unpaired_lead":
        del lead["paired_value"]
    elif forge == "lead_value_mismatch":
        lead["paired_value"]["candidate"]["value"] = 12.0
    elif forge == "overlapping_comparator":
        awc = next(c for c in lead["comparators"] if c["channel"] == "awc")
        awc["interval"]["lag_lower_ms"] = lead["candidate"]["lag_upper_ms"] - 1
    else:
        rivals = frozenset({"jma_amedas"})
    assert fast_admission_defect(row, rivals) == reason
    if forge == "rival_registry_route":
        return
    path = tmp_path / "forged.json"
    path.write_text(json.dumps(data))
    with caplog.at_level("ERROR"):
        routes = load_physical_current_sources(path)[0]
    assert {(r.provider, r.station_id) for r in routes if r.provider == "mgm_metar"} == {("mgm_metar", "LTFM")}
    assert any(r.provider == "noaa_wrh" for r in routes)
    assert "PHYSICAL_CURRENT_FAST_ADMISSION_OMITTED" in caplog.text and reason in caplog.text


def test_canonical_role_cannot_be_claimed_by_a_transport(tmp_path):
    data, row = _ltac_registry_row()
    row["role"] = "canonical_resolver"
    path = tmp_path / "claimed.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="CANONICAL_ROLE_INVALID"):
        load_physical_current_sources(path)


def test_target_plan_authorizes_only_settlement_roles():
    from src.data.physical_current_sources import SourceRole
    routes = load_physical_current_sources()[0]
    assert {r.provider for r in routes if r.settlement_authorized} == {
        "jma_amedas", "eccc_swob", "imd_olbs_metar", "mgm_metar", "wu_station_history", "noaa_wrh"}
    assert all(r.role is SourceRole.PHYSICAL_ONLY for r in routes
               if r.provider in {"fmi_wfs", "imgw_synop", "dwd_cdc", "knmi_observations", "wu_station_current"})


def test_audit_reducer_rounds_through_settlement_semantics():
    import importlib.util
    from src.config import cities_by_name
    path = REGISTRY_PATH.parents[1] / "artifacts/fast_obs_audit/compare_sources.py"
    spec = importlib.util.spec_from_file_location("compare_sources_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hko, toronto = cities_by_name["Hong Kong"], cities_by_name["Toronto"]
    assert module.contract_value(28.7, "C", hko) == 28  # oracle_truncate, not half-up
    assert module.contract_value(-0.5, "C", toronto) == 0  # WMO half-up toward +inf
    assert module.contract_value(-1.5, "C", toronto) == -1
    assert module.contract_value(18.5, "C", toronto) == 19
