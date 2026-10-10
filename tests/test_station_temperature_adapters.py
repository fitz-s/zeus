# Created: 2026-09-29
# Last reused/audited: 2026-10-07
# Lifecycle: created=2026-09-29; last_reviewed=2026-10-07; last_reused=2026-10-07
# Authority basis: docs/operations/current/finite_evidence_probability_symmetry/PLAN.md native sample boolean boundary; operator re-admission of Moscow UUWW 2026-10-06 (artifacts/fast_obs_audit/ROUND4.md); docs/reference/fast_obs_city_algorithm_matrix.md §5 G3a (KNMI key resolver); G5 page routes for every NOAA city (same doc §5, artifacts/fast_obs_audit/g5_noaa_page_daily_proof.json)
# Purpose: Pin station adapter parsing and registry source roles, including fast-admission proof law.
# Reuse: Run when physical_current_sources, station_temperature_adapters, or the registry JSON changes.
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


def _wrh_metadata_payload(station, *, unit="C", metadata=None):
    return {"UNITS": {"air_temp": {"C": "Celsius", "F": "Fahrenheit"}[unit], "elevation": "ft"},
            "STATION": [{"STID": station, "LATITUDE": "8.98330", "LONGITUDE": "-79.51670",
                         "ELEVATION": "43.0", "ELEV_DEM": "42.7", **(metadata or {}),
                         "OBSERVATIONS": {"date_time": ["2026-09-30T04:00:00+0000"],
                                          "air_temp_set_1": [28.5], "sea_level_pressure_set_1": [1010]}}]}


@pytest.mark.parametrize("unit", ["C", "F"])
@pytest.mark.parametrize("metadata", [None, {"LATITUDE": None, "ELEVATION": {"invalid": True}}])
def test_wrh_native_metadata_survives_existing_day0_json_and_sqlite(unit, metadata):
    import hashlib
    from src.state.schema.observation_prints_schema import ensure_table, append_print

    route = replace(_public_route("noaa_wrh", "MPMG"), unit=unit,
                    identity={"provider_station": "MPMG", "resolver_view": "all"})
    body = json.dumps(_wrh_metadata_payload("MPMG", unit=unit, metadata=metadata)).encode()
    sample, = parse_station_payload(route, body, received_at=NOW)
    record = json.loads(sample.raw_report)
    reference = record["station_reference"]
    assert reference["raw_fields"]["ELEVATION"] == (metadata or {}).get("ELEVATION", "43.0")
    assert reference["raw_units"]["elevation"] == "ft"
    assert reference["response_sha256"] == record["payload_sha256"] == hashlib.sha256(body).hexdigest()
    assert reference["hash_kind"] == "PARSER_INPUT_BYTES"
    assert reference["native_body_sha256"] is None
    assert reference["fetched_at_utc"] is None
    assert reference["source_issued_at_utc"] is None
    assert reference["height_role"] == reference["vertical_datum"] == "UNKNOWN"
    assert sample.value_native == 28.5 and sample.unit == unit
    assert valid_station_print(route, sample.raw_report, observed_at=sample.observed_at, value=28.5)
    conn = sqlite3.connect(":memory:")
    try:
        ensure_table(conn)
        append_print(conn, city="private fixture", station_id="MPMG", source_channel=route.source_channel,
                     publish_ts_utc=sample.observed_at.isoformat(), value_native=sample.value_native, unit=unit,
                     fetched_at_utc=sample.fetched_at.isoformat(), raw_report=sample.raw_report)
        conn.commit()
        persisted = json.loads(conn.execute("SELECT raw_report FROM observation_prints").fetchone()[0])
        assert persisted["station_reference"] == reference
        assert persisted["provider_observed_at_ms"] == int(sample.observed_at.timestamp() * 1000)
    finally:
        conn.close()


def test_wrh_batch_reference_binds_original_http_bytes_without_changing_temperature_digest(monkeypatch):
    import hashlib
    import httpx
    from src.data import station_temperature_adapters as adapters, noaa_wrh_timeseries as wrh

    route = next(r for r in load_physical_current_sources()[0] if r.provider == "noaa_wrh")
    payload = _wrh_metadata_payload(route.station_id, unit=route.unit)
    payload["STATION"].append(_wrh_metadata_payload("OTHER", unit=route.unit)["STATION"][0])
    payload["_wrh_response_sha256"] = payload["response_sha256"] = "provider cannot supply transport proof"
    body = json.dumps(payload, indent=3).encode()
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "private-fixture-token")
    adapters._WRH_BATCH_CACHE.clear()
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as client:
            batch, receipt = adapters._fetch_wrh_batch(route, client)
            assert batch == payload and json.loads(json.dumps(batch)) == payload
            assert batch.response_sha256 == hashlib.sha256(body).hexdigest()
            sample, = adapters.fetch_station_temperature(route, start=NOW-timedelta(hours=2), end=NOW, client=client)
        record = json.loads(sample.raw_report)
        subset = {"UNITS": payload["UNITS"], "STATION": payload["STATION"][:1]}
        assert record["payload_sha256"] == hashlib.sha256(json.dumps(subset).encode()).hexdigest()
        assert record["station_reference"]["response_sha256"] == hashlib.sha256(body).hexdigest()
        assert record["station_reference"]["native_body_sha256"] == hashlib.sha256(body).hexdigest()
        assert record["station_reference"]["hash_kind"] == "HTTP_RESPONSE_BODY_BYTES"
        assert record["station_reference"]["response_sha256"] != record["payload_sha256"]
        receipt = datetime.fromisoformat(record["station_reference"]["fetched_at_utc"])
        assert int(receipt.timestamp() * 1000) == record["received_at_ms"]
        assert receipt.microsecond % 1000 == 0
        assert record["station_reference"]["fetched_at_precision"] == "millisecond"
        assert "private-fixture-token" not in sample.raw_report
        assert len(record["station_reference"]["raw_fields"]) == len(payload["STATION"][0])-1
        assert sample.value_native == 28.5
    finally:
        adapters._WRH_BATCH_CACHE.clear()


def test_wrh_nonfinite_optional_metadata_is_unknown_in_standard_json_not_a_temperature_failure():
    route = replace(_public_route("noaa_wrh", "MPMG"),
                    identity={"provider_station": "MPMG", "resolver_view": "all"})
    body = json.dumps(_wrh_metadata_payload("MPMG", metadata={"ELEVATION": "1e999"})).encode().replace(b'"1e999"', b'1e999')
    sample, = parse_station_payload(route, body, received_at=NOW)
    record = json.loads(sample.raw_report)
    assert record["station_reference"]["raw_fields"]["ELEVATION"]["value_status"] == "UNKNOWN"
    json.dumps(record, allow_nan=False)
    assert sample.value_native == 28.5
    assert valid_station_print(route, sample.raw_report, observed_at=sample.observed_at, value=28.5)


def test_wrh_reference_receipt_adds_no_microsecond_nonce_to_existing_json():
    import hashlib

    route = replace(_public_route("noaa_wrh", "MPMG"),
                    identity={"provider_station": "MPMG", "resolver_view": "all"})
    body = json.dumps(_wrh_metadata_payload("MPMG")).encode()
    samples = [parse_station_payload(route, body, received_at=NOW.replace(microsecond=microsecond),
                                    source_response_sha256=hashlib.sha256(body).hexdigest())[0]
               for microsecond in (123100, 123900)]
    assert samples[0].fetched_at != samples[1].fetched_at
    assert samples[0].raw_report == samples[1].raw_report
    assert json.loads(samples[0].raw_report)["station_reference"]["fetched_at_utc"].endswith(".123+00:00")


def test_wrh_metadata_preserves_current_temperature_identity_with_legacy_json():
    from src.config import cities_by_name
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from zoneinfo import ZoneInfo

    route = next(r for r in load_physical_current_sources()[0] if r.provider == "noaa_wrh" and r.station_id == "KORD")
    body = json.dumps(_wrh_metadata_payload("KORD", unit=route.unit)).encode()
    sample, = parse_station_payload(route, body, received_at=NOW)
    record = json.loads(sample.raw_report)
    legacy_record = {key: value for key, value in record.items() if key != "station_reference"}
    day = sample.observed_at.astimezone(ZoneInfo(cities_by_name["Chicago"].timezone)).date().isoformat()
    states = []
    facts = []
    for raw in (json.dumps(legacy_record, sort_keys=True, allow_nan=False), sample.raw_report):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            ensure_table(conn)
            append_print(conn, city="Chicago", station_id="KORD", source_channel=route.source_channel,
                         publish_ts_utc=sample.observed_at.isoformat(), value_native=sample.value_native,
                         unit=route.unit, fetched_at_utc=NOW.isoformat(), raw_report=raw)
            state = read_day0_current_temperature_state(conn=conn, city=cities_by_name["Chicago"], target_date=day, decision_time=NOW)
            assert state is not None
            states.append(state)
            fact = _latest_authorized_day0_fact(conn, city="Chicago", target_date=day,
                                               temperature_metric="high", decision_time=NOW,
                                               require_settlement_channel=True)
            assert fact is not None
            facts.append(fact)
        finally:
            conn.close()
    assert states[0] == states[1]
    assert states[0].identity() == states[1].identity()
    assert {key: value for key, value in facts[0].items() if key != "raw_payload_sha256"} == {
        key: value for key, value in facts[1].items() if key != "raw_payload_sha256"
    }
    assert facts[0]["raw_payload_sha256"] != facts[1]["raw_payload_sha256"]

def _public_route(provider="mgm_metar", station="LTAC"):
    from src.data.physical_current_sources import PhysicalCurrentSource, SourceRole
    from src.data.station_temperature_adapters import CHANNELS
    return PhysicalCurrentSource(provider, CHANNELS[provider], station, ("noaa",),
        "C", 60., None, {"provider_station": station, "display_id": "219"}, SourceRole.FAST_ADMISSION)


def _public_fixture(name):
    import gzip
    body = gzip.decompress((ROOT/(name+".html.gz")).read_bytes())
    meta = json.loads((ROOT/(name+".meta.json")).read_text())
    return body, datetime.fromisoformat(meta["receipt_at"])


@pytest.mark.parametrize("provider,station,name", [
    ("mgm_metar", "LTAC", "mgm_public"),
    ("metaviatelecom_metar", "UUWW", "metaviatelecom"),
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


def test_public_metar_route_cannot_inject_host_or_path():
    from src.data.station_temperature_adapters import fetch_station_temperature
    route=replace(_public_route("metaviatelecom_metar","UUWW"),identity={"provider_station":"UUWW","display_id":"../secret"})
    class Client:
        def get(self,*a,**k):raise AssertionError("network must not be reached")
        def stream(self,*a,**k):raise AssertionError("network must not be reached")
    with pytest.raises(ValueError,match="DISPLAY_ID_INVALID"):
        fetch_station_temperature(route,start=NOW,end=NOW,client=Client())


def test_moscow_fixed_plain_http_page_refuses_redirects_and_records_body_digest():
    import hashlib
    import httpx
    from src.data.station_temperature_adapters import _PUBLIC_METAR_CACHE, fetch_station_temperature
    route=_public_route("metaviatelecom_metar","UUWW");body,receipt=_public_fixture("metaviatelecom")
    calls=[]
    def page(request):
        calls.append(request)
        assert request.method=="GET" and str(request.url)=="http://display.meteocenter.ru/219"
        assert "cookie" not in request.headers and "authorization" not in request.headers
        return httpx.Response(200,content=body)
    _PUBLIC_METAR_CACHE.clear()
    try:
        sample,=fetch_station_temperature(route,start=receipt-timedelta(hours=2),end=receipt+timedelta(days=1),
                                          client=httpx.Client(transport=httpx.MockTransport(page)))
        assert len(calls)==1
        assert (sample.observed_at,sample.value_native)==(datetime(2026,9,30,22,30,tzinfo=timezone.utc),9.0)
        assert json.loads(sample.raw_report)["payload_sha256"]==hashlib.sha256(body).hexdigest()
        moved=[]
        def redirect(request):
            moved.append(request)
            return httpx.Response(302,headers={"Location":"http://elsewhere.invalid/219"},content=body)
        _PUBLIC_METAR_CACHE.clear()
        with pytest.raises(ValueError,match="TRANSPORT_DEFERRED"):
            fetch_station_temperature(route,start=NOW,end=NOW,client=httpx.Client(transport=httpx.MockTransport(redirect)))
        assert [str(r.url) for r in moved]==["http://display.meteocenter.ru/219"]
    finally:
        _PUBLIC_METAR_CACHE.clear()


def test_moscow_registry_display_id_is_structure_not_a_url(tmp_path):
    data=json.loads(REGISTRY_PATH.read_text())
    next(r for r in data["sources"] if r["provider"]=="metaviatelecom_metar")["identity"]["display_id"]="219/../x"
    path=tmp_path/"display.json";path.write_text(json.dumps(data))
    routes=load_physical_current_sources(path)[0]
    assert all(r.provider!="metaviatelecom_metar" for r in routes)
    assert any(r.provider=="mgm_metar" for r in routes)


def test_moscow_admission_binds_round4_artifact_verbatim():
    data=json.loads(REGISTRY_PATH.read_text())
    row=next(r for r in data["sources"] if r["provider"]=="metaviatelecom_metar")
    proof=row["value_identity_proof"]
    measured,=[r for r in json.loads((REGISTRY_PATH.parents[1]/proof["report_path"]).read_text())
               if r["station"]=="UUWW" and r["channel"]==proof["report_channel"]]
    assert (row["role"],row["station_id"],row["unit"])==("fast_admission","UUWW","C")
    assert (proof["n_pairs"],proof["n_exact"],proof["mismatches"])==(measured["n_pairs"],measured["n_exact"],[])==(4,4,[])
    lead=row["latency_evidence"]["first_proven_lead"]
    race,=[r for r in measured["races"] if r["observation"]==lead["observation"]]
    assert lead["observation"]=="2026-09-30T23:00:00+00:00"
    assert lead["candidate"]=={**race["candidate"],"channel":"metaviatelecom_metar"}
    assert lead["comparators"]==race["comparators"]
    pair,=[p for p in measured["pairs"] if p["time"]==lead["observation"]]
    assert pair["match"] and [lead["paired_value"]["candidate"]]==pair["candidate_raw"]
    assert [lead["paired_value"]["resolver"]]==pair["resolver_raw"]
    assert row["identity"]=={"provider_station":"UUWW","display_id":"219"}


def test_moscow_recorded_proof_bodies_still_reproduce_their_values():
    import gzip
    root = Path(__file__).parents[1] / "artifacts" / "fast_obs_audit"
    recorded = [s for s in json.loads(gzip.decompress((root / "round4_all_samples.json.gz").read_bytes()))
                if s["channel"] == "metaviatelecom_display"]
    assert recorded
    for sample in recorded:
        body = next(root.glob(f"*/{sample['sha256']}.body.gz"))
        parsed = parse_station_payload(_public_route("metaviatelecom_metar", "UUWW"),
            gzip.decompress(body.read_bytes()),
            received_at=datetime.fromisoformat(sample["receipt_at"]))
        assert [(s.observed_at, s.value_native) for s in parsed] == [
            (datetime.fromisoformat(sample["observed_at"]), sample["value"])]


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


@pytest.mark.parametrize("provider", ["jma_amedas", "imgw_synop"])
@pytest.mark.parametrize("value", [True, False, 0, -5.5, 18.25, float("nan"), float("inf"), float("-inf"), -999])
def test_native_station_temperature_bool_is_not_numeric_zero(provider, value):
    route = next(r for r in load_physical_current_sources()[0] if r.provider == provider)
    if provider == "jma_amedas":
        payload = {"20260930130000": {"temp": [value, 0]}}
    else:
        payload = {"id_stacji": route.identity["provider_station"],
                   "data_pomiaru": "2026-09-30", "godzina_pomiaru": "04",
                   "temperatura": value}
    body = json.dumps(payload).encode()
    samples = parse_station_payload(route, body, received_at=NOW)
    import math
    if isinstance(value, bool) or not math.isfinite(value) or value == -999:
        assert samples == ()
    else:
        sample, = samples
        assert sample.value_native == value and sample.unit == route.unit
        assert sample.observed_at == datetime(2026, 9, 30, 4, tzinfo=timezone.utc)
        assert sample.fetched_at == NOW
        assert valid_station_print(route, sample.raw_report,
                                   observed_at=sample.observed_at, value=value)
        import hashlib
        assert json.loads(sample.raw_report)["payload_sha256"] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize("metric", ["high", "low"])
def test_jma_boolean_row_does_not_replace_valid_extreme_twin(metric):
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "jma_amedas")
    body = json.dumps({
        "20260930130000": {"temp": [True, 0]},
        "20260930131000": {"temp": [False, 0]},
        "20260930132000": {"temp": [0, 0]},
        "20260930133000": {"temp": [-5.5, 0]},
    }).encode()
    samples = parse_station_payload(route, body, received_at=NOW)
    assert [s.value_native for s in samples] == [0.0, -5.5]
    aggregate = max if metric == "high" else min
    assert aggregate(s.value_native for s in samples) == (0.0 if metric == "high" else -5.5)


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
            assert row['role']=='canonical_resolver'
            assert row['source_channel']=='noaa_wrh_'+row['station_id'].lower()
            if row['unit']=='F':
                import gzip
                native_rows=json.loads(gzip.decompress((ROOT/'us_resolver_precision.json.gz').read_bytes()))
                n=sum(r['station']==row['station_id'] for r in native_rows)
                assert proof['n_pairs']==proof['n_exact']==n
            else:
                # degC: the daily page product against VERIFIED settlements (G5 artifact).
                daily=json.loads((REGISTRY_PATH.parents[1]/proof['report_path']).read_text())
                actual,=[c for c in daily['cities'] if c['station']==row['station_id']]
                assert (proof['n_pairs'],proof['n_exact'])==(actual['verified']['n_pairs'],actual['verified']['n_exact'])
                assert proof['chain_bin']['n_pairs']==actual['chain_bin']['n_pairs']
                assert proof['chain_bin']['n_exact']==actual['chain_bin']['n_exact']
            continue  # Existing native resolver, not an alternate-channel promotion.
        evidence_path=proof.get('report_path')
        evidence=json.loads((REGISTRY_PATH.parents[1]/evidence_path).read_text()) if evidence_path else report
        if isinstance(evidence,list):
            # report_channel: the audit's own label when it differs from the route provider.
            channel=proof.get('report_channel',proof['channel'])
            matches=[r for r in evidence if r['city']==proof['city'] and r['channel']==channel]
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
    routes = [r for r in load_physical_current_sources()[0] if r.provider == "noaa_wrh" and r.unit == "F"]
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


# ---------------------------------------------------------------------------
# G5: the NOAA settlement page polled intraday for every NOAA city
# ---------------------------------------------------------------------------

_WRH_METRIC_FIXTURE = ROOT / "wrh_metric_batch_eddm_rjtt.json"
_WRH_METRIC_RECEIPT = datetime(2026, 10, 7, 11, 38, 50, tzinfo=timezone.utc)


def _noaa_cities(unit):
    from src.config import cities_by_name
    return {c.wu_station: c for c in cities_by_name.values()
            if c.settlement_source_type == "noaa" and c.settlement_unit == unit}


def test_every_noaa_city_has_one_page_route_in_its_contract_view():
    from src.data.physical_current_sources import physical_current_sources_for_city
    routes = [r for r in load_physical_current_sources()[0] if r.provider == "noaa_wrh"]
    for unit in ("C", "F"):
        cities = _noaa_cities(unit)
        mine = {r.station_id: r for r in routes if r.unit == unit}
        assert set(mine) == set(cities) and len(mine) == len([r for r in routes if r.unit == unit])
        for station, city in cities.items():
            route = mine[station]
            assert route.settlement_authorized and route.current_path == "resolver"
            assert route.identity["resolver_view"] == city.settlement_page_view
            assert route.minimum_poll_seconds == 60
            assert route in physical_current_sources_for_city(city)
    assert len(_noaa_cities("C")) == 37 and len(_noaa_cities("F")) == 11


def test_resolver_route_does_not_de_admit_a_measured_fast_route():
    """The page route is the 'resolver' comparator, not an unmeasured new rival."""
    from src.data.physical_current_sources import SourceRole
    routes = {(r.provider, r.station_id): r for r in load_physical_current_sources()[0]}
    for provider, station in [("jma_amedas", "RJTT"), ("eccc_swob", "CYYZ"), ("metaviatelecom_metar", "UUWW"),
                              ("imd_olbs_metar", "VILK"), ("mgm_metar", "LTAC"), ("mgm_metar", "LTFM")]:
        assert routes[(provider, station)].role is SourceRole.FAST_ADMISSION
        assert ("noaa_wrh", station) in routes


def test_resolver_route_still_holds_a_fast_route_to_its_measured_resolver_lag(tmp_path, caplog):
    """Mapping the page route to 'resolver' keeps the speed law: a slower lead is omitted."""
    data = json.loads(REGISTRY_PATH.read_text())
    row = next(r for r in data["sources"] if r["provider"] == "jma_amedas")
    lead = row["latency_evidence"]["first_proven_lead"]
    resolver = next(c for c in lead["comparators"] if c["channel"] == "resolver")
    resolver["interval"]["lag_lower_ms"] = lead["candidate"]["lag_upper_ms"] - 1
    path = tmp_path / "slower.json"
    path.write_text(json.dumps(data))
    with caplog.at_level("ERROR"):
        routes = load_physical_current_sources(path)[0]
    assert ("jma_amedas", "RJTT") not in {(r.provider, r.station_id) for r in routes}
    assert ("noaa_wrh", "RJTT") in {(r.provider, r.station_id) for r in routes}
    assert "LEAD_NOT_FASTER:resolver" in caplog.text


@pytest.mark.parametrize("change,reason", [
    ({"resolver_view": "hourly_only"}, "view"), ({"unit": "K"}, "unit"),
    ({"provider_station": "RJAA"}, "native id"), ({"source_channel": "noaa_wrh_temperature"}, "channel"),
])
def test_metric_page_route_rejects_a_malformed_shape(tmp_path, change, reason):
    data = json.loads(REGISTRY_PATH.read_text())
    row = next(r for r in data["sources"] if r["provider"] == "noaa_wrh" and r["station_id"] == "RJTT")
    for key, value in change.items():
        (row["identity"] if key in {"resolver_view", "provider_station"} else row)[key] = value
    path = tmp_path / f"bad_{reason.replace(' ', '_')}.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="PHYSICAL_CURRENT_ADAPTER_INVALID"):
        load_physical_current_sources(path)


def test_page_route_without_counted_proof_cannot_claim_the_resolver_role(tmp_path):
    data = json.loads(REGISTRY_PATH.read_text())
    row = next(r for r in data["sources"] if r["provider"] == "noaa_wrh" and r["station_id"] == "EDDM")
    row["value_identity_proof"]["n_exact"] -= 1
    path = tmp_path / "unproven.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="CANONICAL_ROLE_INVALID"):
        load_physical_current_sources(path)


def _batch_client(calls, *, status=None):
    import httpx

    def handler(request):
        params = dict(request.url.params)
        calls.append(params)
        if status is not None:
            return httpx.Response(status, json={"SUMMARY": {"RESPONSE_MESSAGE": "Invalid request per token rules"}})
        unit = "F" if params.get("units", "").startswith("temp|F") else "C"
        return httpx.Response(200, json={"UNITS": {"air_temp": {"C": "Celsius", "F": "Fahrenheit"}[unit]},
                                         "STATION": []})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_metric_batch_is_one_request_per_minute_separate_from_fahrenheit(monkeypatch):
    from src.data import station_temperature_adapters as adapters
    from src.data import noaa_wrh_timeseries as wrh
    slots = []
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "test-token-not-persisted")
    monkeypatch.setattr(wrh, "_wait_for_request_slot", lambda: slots.append(1))
    routes = [r for r in load_physical_current_sources()[0] if r.provider == "noaa_wrh"]
    calls = []
    adapters._WRH_BATCH_CACHE.clear()
    try:
        client = _batch_client(calls)
        for route in routes:
            adapters._fetch_wrh_batch(route, client)
        for route in routes:  # The next round inside the minute is served from the cache.
            adapters._fetch_wrh_batch(route, client)
        assert len(calls) == 2 and len(slots) == 2
        metric, = [c for c in calls if "units" not in c]
        imperial, = [c for c in calls if "units" in c]
        assert set(metric["STID"].split(",")) == set(_noaa_cities("C"))
        assert set(imperial["STID"].split(",")) == set(_noaa_cities("F"))
        assert imperial["units"] == "temp|F,speed|kts,english"
        assert metric["recent"] == imperial["recent"] == "180"
        assert {key for key, (_, _, _, _) in adapters._WRH_BATCH_CACHE.items()} == {
            ("C", tuple(sorted(_noaa_cities("C"))), id(client)),
            ("F", tuple(sorted(_noaa_cities("F"))), id(client))}
    finally:
        adapters._WRH_BATCH_CACHE.clear()


def test_refused_metric_batch_is_typed_and_leaves_the_fahrenheit_batch_serving(monkeypatch):
    from src.data import station_temperature_adapters as adapters
    from src.data import noaa_wrh_timeseries as wrh
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "private-test-value")
    monkeypatch.setattr(wrh, "_wait_for_request_slot", lambda: None)
    routes = load_physical_current_sources()[0]
    metric = next(r for r in routes if r.provider == "noaa_wrh" and r.unit == "C")
    imperial = next(r for r in routes if r.provider == "noaa_wrh" and r.unit == "F")
    refused, served = [], []
    adapters._WRH_BATCH_CACHE.clear()
    try:
        refusing, serving = _batch_client(refused, status=403), _batch_client(served)
        for _ in range(2):
            with pytest.raises(ValueError, match="WRH_CURRENT_TRANSPORT_DEFERRED:WrhTokenRefused") as exc:
                adapters._fetch_wrh_batch(metric, refusing)
            assert "private-test-value" not in str(exc.value)
        assert len(refused) == 1  # Cached as an error for the retry floor, never re-requested.
        data, _ = adapters._fetch_wrh_batch(imperial, serving)
        assert data["UNITS"]["air_temp"] == "Fahrenheit" and len(served) == 1
        # And the reverse: a refused F batch leaves the C batch serving.
        adapters._WRH_BATCH_CACHE.clear(); refused.clear(); served.clear()
        with pytest.raises(ValueError, match="WrhTokenRefused"):
            adapters._fetch_wrh_batch(imperial, refusing)
        data, _ = adapters._fetch_wrh_batch(metric, serving)
        assert data["UNITS"]["air_temp"] == "Celsius" and len(served) == 1
    finally:
        adapters._WRH_BATCH_CACHE.clear()


def test_metric_view_parses_every_row_without_a_units_param():
    """The degC page shows every row: non-routine rows count, no hourly filter."""
    from src.data.noaa_wrh_timeseries import rows_from_payload
    payload = json.loads(_WRH_METRIC_FIXTURE.read_text())
    routes = {r.station_id: r for r in load_physical_current_sources()[0]
              if r.provider == "noaa_wrh" and r.unit == "C"}
    for station in ("EDDM", "RJTT"):
        stations = [s for s in payload["STATION"] if s["STID"] == station]
        body = json.dumps({"UNITS": payload["UNITS"], "STATION": stations}).encode()
        rows = rows_from_payload({"STATION": stations}, station)
        assert rows and not any(row.is_official_report for row in rows)  # all-view rows only
        samples = parse_station_payload(routes[station], body, received_at=_WRH_METRIC_RECEIPT)
        assert [(s.observed_at, s.value_native) for s in samples] == [(r.utc, r.air_temp) for r in rows]
        assert all(s.unit == "C" for s in samples)
    hourly = replace(routes["EDDM"], identity={**routes["EDDM"].identity, "resolver_view": "hourly"})
    eddm = [s for s in payload["STATION"] if s["STID"] == "EDDM"]
    body = json.dumps({"UNITS": payload["UNITS"], "STATION": eddm}).encode()
    assert parse_station_payload(hourly, body, received_at=_WRH_METRIC_RECEIPT) == ()
    with pytest.raises(ValueError, match="STATION_UNIT_OR_QC_INVALID"):
        parse_station_payload(routes["EDDM"], body.replace(b'"Celsius"', b'"Fahrenheit"'),
                              received_at=_WRH_METRIC_RECEIPT)


def test_recorded_metric_batch_reaches_the_day0_settlement_reduction(monkeypatch):
    """Fixture payload for two non-US stations -> adapter -> prints ledger -> Day0 readers."""
    import httpx
    from zoneinfo import ZoneInfo
    from src.config import cities_by_name
    from src.data import station_temperature_adapters as adapters
    from src.data import noaa_wrh_timeseries as wrh
    from src.data.day0_hourly_vectors import read_day0_current_temperature_state
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.state.schema.observation_prints_schema import ensure_table, append_print

    body = _WRH_METRIC_FIXTURE.read_bytes()
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, content=body)
    monkeypatch.setattr(wrh, "fetch_wrh_token", lambda: "test-token-not-persisted")
    monkeypatch.setattr(wrh, "_wait_for_request_slot", lambda: None)
    adapters._WRH_BATCH_CACHE.clear()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; ensure_table(conn)
    routes = {r.station_id: r for r in load_physical_current_sources()[0]
              if r.provider == "noaa_wrh" and r.unit == "C"}
    expected = {"Munich": ("EDDM", 23.0, 18.0), "Tokyo": ("RJTT", 21.0, 20.0)}
    try:
        for city_name, (station, _, _) in expected.items():
            route = routes[station]
            prints = adapters.fetch_station_temperature(
                route, start=_WRH_METRIC_RECEIPT - timedelta(hours=3), end=_WRH_METRIC_RECEIPT, client=client)
            assert prints
            for sample in prints:
                assert append_print(conn, city=city_name, station_id=station, source_channel=route.source_channel,
                                    publish_ts_utc=sample.observed_at.isoformat(), value_native=sample.value_native,
                                    unit="C", fetched_at_utc=sample.fetched_at.isoformat(),
                                    raw_report=sample.raw_report)
    finally:
        adapters._WRH_BATCH_CACHE.clear()
    assert len(calls) == 1 and "units" not in calls[0]
    decision = datetime.now(timezone.utc)
    for city_name, (station, high, low) in expected.items():
        city = cities_by_name[city_name]
        day = _WRH_METRIC_RECEIPT.astimezone(ZoneInfo(city.timezone)).date().isoformat()
        state = read_day0_current_temperature_state(conn=conn, city=city, target_date=day, decision_time=decision)
        assert state is not None and state.source == f"noaa_wrh_{station.lower()}"
        for metric, value in (("high", high), ("low", low)):
            fact = _latest_authorized_day0_fact(conn, city=city_name, target_date=day, temperature_metric=metric,
                                               decision_time=decision, require_settlement_channel=True)
            assert fact is not None and fact["observation_source"] == f"noaa_wrh_{station.lower()}"
            assert fact["observed_extreme_native"] == value
    conn.close()


def test_daily_product_row_and_intraday_prints_share_one_channel_without_double_counting():
    """daily_tick's prints for a clock already polled intraday are a repeat, not a second sample."""
    from src.data.daily_obs_append import _append_noaa_wrh_prints
    from src.data.noaa_wrh_timeseries import rows_from_payload
    from src.state.schema.observation_prints_schema import ensure_table, append_print
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    payload = json.loads(_WRH_METRIC_FIXTURE.read_text())
    stations = [s for s in payload["STATION"] if s["STID"] == "EDDM"]
    rows = rows_from_payload({"STATION": stations}, "EDDM")
    route = next(r for r in load_physical_current_sources()[0]
                 if r.provider == "noaa_wrh" and r.station_id == "EDDM")
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; ensure_table(conn)
    body = json.dumps({"UNITS": payload["UNITS"], "STATION": stations}).encode()
    for sample in parse_station_payload(route, body, received_at=_WRH_METRIC_RECEIPT):
        append_print(conn, city="Munich", station_id="EDDM", source_channel=route.source_channel,
                     publish_ts_utc=sample.observed_at.isoformat(), value_native=sample.value_native,
                     unit="C", fetched_at_utc=sample.fetched_at.isoformat(), raw_report=sample.raw_report)
    intraday = conn.execute("SELECT COUNT(*) FROM observation_prints").fetchone()[0]
    later = _WRH_METRIC_RECEIPT + timedelta(hours=14)
    written = _append_noaa_wrh_prints(conn, city_name="Munich", station="EDDM", unit="C", rows=rows,
                                      target_date_local=rows[0].utc.astimezone(
                                          __import__("zoneinfo").ZoneInfo("Europe/Berlin")).date(),
                                      view="all", fetch_utc=later)
    assert intraday == len(rows) and written == 0  # Same clock and value: suppressed.
    fact = _latest_authorized_day0_fact(conn, city="Munich", target_date="2026-10-07", temperature_metric="high",
                                       decision_time=later, require_settlement_channel=True)
    assert fact["sample_count"] == len(rows) and fact["observed_extreme_native"] == 23.0
    conn.close()


@pytest.mark.parametrize("city_name,provider,value,unit", [
    ("Chicago","noaa_wrh",62.6,"F"),
    ("Moscow","metaviatelecom_metar",8.0,"C"),
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
    worker=ingest._physical_current_reseed_thread
    if worker is not None:worker.join(10)
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
        # A canonical-resolver route is the "resolver" comparator itself.
        path = "resolver" if row["role"] == "canonical_resolver" else row["provider"]
        by_station.setdefault(row["station_id"], set()).add(path)
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
    ("neg_inf_upper", "LEAD_INTERVAL_INVALID"),
    ("nan_upper", "LEAD_INTERVAL_INVALID"),
    ("inverted_candidate", "LEAD_INTERVAL_INVALID"),
    ("inf_comparator_lower", "LEAD_COMPARATOR_MALFORMED"),
    ("comparator_missing_interval", "LEAD_COMPARATOR_MALFORMED"),
    ("comparator_wrong_channel", "LEAD_COMPARATOR_IDENTITY"),
    ("comparator_unbound_station", "LEAD_COMPARATOR_IDENTITY"),
    ("comparators_not_a_list", "MALFORMED:TypeError"),
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
    elif forge == "neg_inf_upper":
        lead["candidate"]["lag_upper_ms"] = -float("inf")
    elif forge == "nan_upper":
        lead["candidate"]["lag_upper_ms"] = float("nan")
    elif forge == "inverted_candidate":
        lead["candidate"]["lag_lower_ms"] = lead["candidate"]["lag_upper_ms"] + 1
    elif forge == "inf_comparator_lower":
        lead["comparators"][0]["interval"]["lag_lower_ms"] = float("inf")
    elif forge == "comparator_missing_interval":
        del lead["comparators"][0]["interval"]
    elif forge == "comparator_wrong_channel":
        lead["comparators"][0]["interval"]["channel"] = "resolver"
    elif forge == "comparator_unbound_station":
        del lead["comparators"][0]["interval"]["station"]
    elif forge == "comparators_not_a_list":
        lead["comparators"] = 7
    else:
        rivals = frozenset({"jma_amedas"})
    if forge == "comparators_not_a_list":
        # Raises inside the validator; the loader isolates it to this route.
        with pytest.raises(TypeError):
            fast_admission_defect(row, rivals)
    else:
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


def test_only_structurally_valid_routes_are_rivals(tmp_path, caplog):
    """An invalid proposed row is no current path; a valid same-station one is."""
    import copy
    data, row = _ltac_registry_row()
    unknown = {**copy.deepcopy(row), "provider": "unknown_adapter",
               "source_channel": "unknown_temperature"}
    data["sources"].append(unknown)
    path = tmp_path / "proposed.json"
    path.write_text(json.dumps(data))
    with caplog.at_level("ERROR"):
        routes = load_physical_current_sources(path)[0]
    assert ("mgm_metar", "LTAC") in {(r.provider, r.station_id) for r in routes}
    assert "unknown_adapter" not in {r.provider for r in routes}
    assert "LEAD_NOT_FASTER" not in caplog.text

    data, row = _ltac_registry_row()
    data["sources"].append({**copy.deepcopy(row), "provider": "jma_amedas",
                            "source_channel": "jma_amedas_temperature",
                            "role": "physical_only", "identity": {"provider_station": "44166"}})
    path = tmp_path / "incumbent.json"
    path.write_text(json.dumps(data))
    with caplog.at_level("ERROR"):
        routes = load_physical_current_sources(path)[0]
    assert ("mgm_metar", "LTAC") not in {(r.provider, r.station_id) for r in routes}
    assert ("jma_amedas", "LTAC") in {(r.provider, r.station_id) for r in routes}
    assert "LEAD_NOT_FASTER:jma_amedas" in caplog.text


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
        "jma_amedas", "eccc_swob", "metaviatelecom_metar", "imd_olbs_metar", "mgm_metar",
        "wu_station_history", "noaa_wrh"}
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


# ---------------------------------------------------------------------------
# KNMI key resolution (fast-obs gap G3a, 2026-10-07): env first, then the
# gitignored config/knmi_secret.json; the key never reaches a log or an error.
# ---------------------------------------------------------------------------

_KNMI_FAKE_KEY = "knmi-test-key-0123456789"


def _write_knmi_secret(root, payload):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "knmi_secret.json").write_text(json.dumps(payload))


def test_knmi_key_env_wins_over_secret_file(tmp_path):
    from src.data.station_temperature_adapters import resolve_knmi_api_key
    _write_knmi_secret(tmp_path, {"knmi_api_key": "from-file"})
    assert resolve_knmi_api_key(environ={"KNMI_API_KEY": " from-env "}, root=tmp_path) == "from-env"


def test_knmi_key_falls_back_to_secret_file(tmp_path):
    from src.data.station_temperature_adapters import resolve_knmi_api_key
    _write_knmi_secret(tmp_path, {"knmi_api_key": "from-file"})
    assert resolve_knmi_api_key(environ={"KNMI_API_KEY": "  "}, root=tmp_path) == "from-file"


@pytest.mark.parametrize("content", [None, "{not json", "[]", '{"knmi_api_key": ""}', '{"other": "x"}'])
def test_knmi_key_absent_or_malformed_secret_is_none(tmp_path, content):
    from src.data.station_temperature_adapters import resolve_knmi_api_key
    if content is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "knmi_secret.json").write_text(content)
    assert resolve_knmi_api_key(environ={}, root=tmp_path) is None


def test_knmi_fetch_uses_resolved_key_and_never_logs_it(monkeypatch, caplog):
    import httpx
    import logging
    from src.data import station_temperature_adapters as adapters
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "knmi_observations")
    monkeypatch.setattr(adapters, "resolve_knmi_api_key", lambda: _KNMI_FAKE_KEY)
    seen = []

    def handler(request):
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        adapters.fetch_station_temperature(route, start=NOW - timedelta(hours=2), end=NOW, client=client)
    assert seen == [_KNMI_FAKE_KEY]
    assert _KNMI_FAKE_KEY not in str(exc.value)
    assert _KNMI_FAKE_KEY not in caplog.text


def test_knmi_ingest_failure_log_carries_no_key(monkeypatch, caplog):
    import httpx
    import logging
    import src.ingest_main as ingest
    from src.config import cities_by_name
    from src.data import station_temperature_adapters as adapters
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "knmi_observations")

    def leaky(*_args, **_kwargs):
        raise ValueError("KNMI rejected " + _KNMI_FAKE_KEY)

    monkeypatch.setattr(adapters, "fetch_station_temperature", leaky)
    caplog.set_level(logging.DEBUG)
    result = ingest._day0_current_temperature_source_tick(cities_by_name["Amsterdam"], route)
    assert result == {"status": "SOURCE_UNAVAILABLE"}
    assert "PHYSICAL_CURRENT_FETCH_FAILED station=EHAM error=ValueError" in caplog.text
    assert _KNMI_FAKE_KEY not in caplog.text


def test_knmi_missing_key_error_names_no_secret(monkeypatch):
    from src.data import station_temperature_adapters as adapters
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "knmi_observations")
    monkeypatch.setattr(adapters, "resolve_knmi_api_key", lambda: None)
    with pytest.raises(ValueError, match="^KNMI_API_KEY_UNAVAILABLE$"):
        adapters.fetch_station_temperature(route, start=NOW - timedelta(hours=2), end=NOW)


def test_knmi_grid_is_physical_only_dense_not_settlement_instants():
    """KNMI publishes a 10-minute grid (:00/:10/...); EHAM METARs are :25/:55.
    The route shares no instant with the settlement METAR, so it stays
    physical-only and never authorizes a settlement fact."""
    from src.data.physical_current_sources import SourceRole
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "knmi_observations")
    assert route.station_id == "EHAM" and route.role is SourceRole.PHYSICAL_ONLY
    assert not route.settlement_authorized


def test_knmi_presigned_download_keeps_its_signature(monkeypatch):
    """The temporary download URL is S3-presigned. httpx replaces a URL's query
    with ``params`` even when empty, which stripped the signature and made every
    live EHAM download 403 (2026-10-07). The signed query must reach S3 intact."""
    import httpx
    pytest.importorskip("netCDF4")
    from src.data import station_temperature_adapters as adapters
    route = next(r for r in load_physical_current_sources()[0] if r.provider == "knmi_observations")
    monkeypatch.setattr(adapters, "resolve_knmi_api_key", lambda: _KNMI_FAKE_KEY)
    name = "KMDS__OPER_P___10M_OBS_L2_202609301700.nc"
    signed = ("https://knmi-kdp-datasets-eu-west-1.s3.eu-west-1.amazonaws.com/"
              f"10-minute-in-situ-meteorological-observations/1.0/{name}"
              "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=deadbeef")
    downloads = []

    def handler(request):
        if request.url.host == "api.dataplatform.knmi.nl":
            if str(request.url.path).endswith("/url"):
                return httpx.Response(200, json={"temporaryDownloadUrl": signed})
            return httpx.Response(200, json={"files": [{"filename": name}]})
        downloads.append(str(request.url))
        if "X-Amz-Signature=deadbeef" not in str(request.url):
            return httpx.Response(403)
        return httpx.Response(200, content=(ROOT / "knmi.bin").read_bytes())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    samples = adapters.fetch_station_temperature(
        route, start=NOW - timedelta(days=30), end=NOW + timedelta(days=30), client=client)
    assert downloads and "X-Amz-Signature=deadbeef" in downloads[0]
    assert samples
