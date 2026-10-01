# Created: 2026-09-29
# Last reused/audited: 2026-10-01 (versioned absence proof)
# Authority: all 48 NOAA market descriptions captured for 2026-09-29; REQ-20260929-223929-bf51a2.
"""An unavailable local service is not an unavailable settlement product."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import importlib
import json
import sqlite3
import pytest
from src.data.settlement_observation_selection import (
    observation_selection, noaa_absence_witness, fallback_deadline, EMPTY_AFTER_DEADLINE,
    PROOF_VERSION, PRODUCT,
)

CITY=SimpleNamespace(name="fixture",wu_station="KBKF",settlement_source_type="noaa",
                     settlement_page_view="hourly",settlement_unit="F")
DAY="2026-09-27"

def database():
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE data_coverage(data_table TEXT,city TEXT,data_source TEXT,target_date TEXT,sub_key TEXT,status TEXT,reason TEXT,fetched_at TEXT,evidence_json TEXT)")
    conn.execute("CREATE TABLE observations(id INTEGER,city TEXT,target_date TEXT,source TEXT,high_temp REAL,low_temp REAL,unit TEXT,station_id TEXT,authority TEXT,fetched_at TEXT,high_local_time TEXT,low_local_time TEXT,high_provenance_metadata TEXT)")
    return conn

def proof(clock, **override):
    return json.dumps({"proof_version": PROOF_VERSION, "product": PRODUCT, "station_id": "KBKF",
        "page_view": "hourly", "unit": "F", "unit_label": "Fahrenheit", "target_date": DAY,
        "request_url": "https://example/timeseries?token=REDACTED", "response_sha256": "a" * 64,
        "received_at": clock.isoformat(), **override})

def absence(conn, clock, evidence="current"):
    evidence = proof(clock) if evidence == "current" else evidence
    conn.execute("INSERT INTO data_coverage(data_table,city,data_source,target_date,sub_key,status,reason,fetched_at,evidence_json) "
                 "VALUES('observations','fixture','noaa_wrh_kbkf',?,'','FAILED',?,?,?)",
                 (DAY, EMPTY_AFTER_DEADLINE, clock.isoformat(), evidence))

@pytest.mark.parametrize("reason", ["NETWORK_ERROR","AUTH_ERROR","OUTSIDE_LANE_REQUEST_WINDOW","GUARD_REJECTED"])
def test_local_failure_is_never_upstream_absence(reason):
    conn=database();after=fallback_deadline(DAY)+timedelta(minutes=1)
    conn.execute("INSERT INTO data_coverage VALUES('observations','fixture','noaa_wrh_kbkf',?,'','FAILED',?,?,NULL)",(DAY,reason,after.isoformat()))
    assert noaa_absence_witness(conn,CITY,DAY,as_of=after) is None
    assert observation_selection(conn,CITY,DAY,"wu_icao_history",as_of=after) is None
    assert observation_selection(conn,CITY,DAY,"ogimet_metar_kbkf",as_of=after) is None
    conn.close()

def test_exact_et_deadline_and_durable_wu_witness():
    conn=database();deadline=fallback_deadline(DAY)
    assert deadline.isoformat()=="2026-09-29T03:59:00+00:00"
    assert fallback_deadline("2026-11-01").hour==4
    absence(conn, deadline)
    assert noaa_absence_witness(conn,CITY,DAY,as_of=deadline-timedelta(milliseconds=1)) is None
    witness=noaa_absence_witness(conn,CITY,DAY,as_of=deadline)
    assert witness is not None
    conn.execute("DELETE FROM data_coverage")
    row={"high_provenance_metadata":json.dumps({"resolver_fallback":witness})}
    assert observation_selection(conn,CITY,DAY,"wu_icao_history",row=row,as_of=deadline)[1]["selected"]=="FALLBACK_WU"
    assert observation_selection(conn,CITY,DAY,"noaa_wrh_kden",as_of=deadline) is None
    conn.close()

@pytest.mark.parametrize("module",["src.ingest.harvester_truth_writer","src.execution.harvester"])
def test_both_harvesters_refuse_mirror_and_preserve_fallback_provenance(module):
    conn=database();deadline=fallback_deadline(DAY)
    row=(1,"fixture",DAY,"ogimet_metar_kbkf",70,50,"F","KBKF","VERIFIED",deadline.isoformat(),DAY+"T14:00:00-06:00",DAY+"T06:00:00-06:00",None)
    conn.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",row)
    lookup=importlib.import_module(module)._lookup_settlement_obs
    assert lookup(conn,CITY,DAY) is None
    conn.execute("UPDATE observations SET source='wu_icao_history'")
    assert lookup(conn,CITY,DAY) is None
    absence(conn, deadline)
    result=lookup(conn,CITY,DAY)
    assert result["resolver_selection"]["selected"]=="FALLBACK_WU"
    assert result["data_version"]=="wu_icao_contract_fallback_v1"
    conn.close()


def test_fallback_http_precedes_cross_database_writer_lease(monkeypatch):
    from contextlib import contextmanager
    from src.data import settlement_observation_selection as selection
    from src.data import daily_obs_append as daily
    from src.state import db
    from src import config
    conn=database();clock=fallback_deadline(DAY)+timedelta(minutes=1)
    city=SimpleNamespace(**{**vars(CITY),"country_code":"US","timezone":"America/Denver"})
    monkeypatch.setitem(config.cities_by_name,city.name,city)
    conn.execute("ALTER TABLE data_coverage ADD COLUMN retry_after TEXT")
    absence(conn, clock)
    events=[]
    @contextmanager
    def read(**_):
        events.append("read_open");yield conn;events.append("read_close")
    @contextmanager
    def write(**_):
        events.append("writer_open");yield conn;events.append("writer_close")
    def fetch(*args,**kwargs):
        assert events[-1]=="read_close"
        assert kwargs["require_station_identity"]
        events.append("http")
        return daily.WuDailyFetchResult(payload={DAY:(70.0,50.0)})
    def append(*args,**kwargs):
        assert events[-1]=="writer_open"
        assert kwargs["prefetched_result"].payload[DAY]==(70.0,50.0)
        assert kwargs["resolver_fallback"]["station_id"]=="KBKF"
        return {"inserted":1}
    monkeypatch.setattr(db,"get_forecasts_connection_with_world_read_only",read)
    monkeypatch.setattr(db,"get_forecasts_connection_with_world",write)
    monkeypatch.setattr(daily,"_fetch_wu_icao_daily_highs_lows",fetch)
    monkeypatch.setattr(daily,"append_wu_city",append)
    assert selection.collect_due_noaa_fallbacks(now=clock)["written"]==1
    assert events==["read_open","read_close","http","writer_open","writer_close"]
    conn.close()


def test_prefetched_wu_fallback_writes_canonical_atom_pair_without_http(monkeypatch,tmp_path):
    from zoneinfo import ZoneInfo
    from src.data import daily_obs_append as daily
    from src.data.settlement_observation_selection import RULE
    from tests.test_noaa_wrh_settlement_product import _observations_conn
    from src.config import cities_by_name
    conn=_observations_conn(tmp_path)
    city=cities_by_name["Denver"]
    day=datetime.fromisoformat(DAY).date()
    stamp=fallback_deadline(DAY)
    witness={"rule":RULE,"absence_basis":EMPTY_AFTER_DEADLINE,"station_id":"KBKF",
        "target_date":DAY,"page_view":"hourly","absence_observed_at":stamp.isoformat(),
        "absence_proof":json.loads(proof(stamp))}
    received=daily.WuDailyFetchResult(payload={DAY:(70.0,50.0)},extreme_times={DAY:(
        datetime(2026,9,27,14,tzinfo=ZoneInfo(city.timezone)),
        datetime(2026,9,27,6,tzinfo=ZoneInfo(city.timezone)))})
    monkeypatch.setattr(daily,"_fetch_wu_icao_daily_highs_lows",lambda *_a,**_k:pytest.fail("HTTP under writer scope"))
    result=daily.append_wu_city("Denver",[day],conn,rebuild_run_id="fallback-fixture",
        resolver_fallback=witness,prefetched_result=received)
    assert result["inserted"]==1,result
    row=conn.execute("SELECT * FROM observations WHERE city='Denver' AND source='wu_icao_history'").fetchone()
    assert json.loads(row["high_provenance_metadata"])["resolver_fallback"]==witness
    assert row["high_local_time"].startswith(DAY+"T14:")
    assert observation_selection(conn,city,DAY,"wu_icao_history",row=row)[1]["selected"]=="FALLBACK_WU"
    conn.close()


_PRIMARY = ("noaa_wrh_kbkf", "KBKF", "VERIFIED")


def _primary_database(*, drop=None, station="KBKF", authority="VERIFIED"):
    columns = ["id INTEGER", "city TEXT", "target_date TEXT", "source TEXT", "high_temp REAL",
               "low_temp REAL", "unit TEXT", "station_id TEXT", "authority TEXT", "fetched_at TEXT"]
    columns = [c for c in columns if c.split()[0] != drop]
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE data_coverage(data_table TEXT,city TEXT,data_source TEXT,target_date TEXT,sub_key TEXT,status TEXT,reason TEXT,fetched_at TEXT)")
    conn.execute(f"CREATE TABLE observations({','.join(columns)})")
    row = {"id": 1, "city": "fixture", "target_date": DAY, "source": "noaa_wrh_kbkf",
           "high_temp": 70, "low_temp": 50, "unit": "F", "station_id": station,
           "authority": authority, "fetched_at": "2026-09-28T12:00:00+00:00"}
    names = [c.split()[0] for c in columns]
    conn.execute(f"INSERT INTO observations({','.join(names)}) VALUES ({','.join('?' for _ in names)})",
                 [row[n] for n in names])
    return conn


@pytest.mark.parametrize("module", ["src.ingest.harvester_truth_writer", "src.execution.harvester"])
@pytest.mark.parametrize("fixture", [
    {"drop": "station_id"}, {"drop": "authority"},
    {"station": ""}, {"station": None}, {"station": "KDEN"}, {"station": "KBKFX"},
    {"authority": "UNVERIFIED"}, {"authority": None},
])
def test_both_harvesters_require_station_and_authority(module, fixture):
    lookup = importlib.import_module(module)._lookup_settlement_obs
    assert lookup(_primary_database(), CITY, DAY)["source"] == "noaa_wrh_kbkf"
    assert lookup(_primary_database(**fixture), CITY, DAY) is None


@pytest.mark.parametrize("evidence", [
    None,  # pre-proof writer: reason only
    "not json",
    proof(fallback_deadline(DAY), proof_version="noaa_wrh_absence_proof_v0"),
    proof(fallback_deadline(DAY), station_id="KDEN"),
    proof(fallback_deadline(DAY), unit="C"),
    proof(fallback_deadline(DAY), response_sha256="short"),
    proof(fallback_deadline(DAY) - timedelta(minutes=1)),
])
def test_absence_without_a_current_bound_proof_authorizes_no_fallback(evidence):
    conn=database();clock=fallback_deadline(DAY)+timedelta(minutes=1)
    absence(conn, fallback_deadline(DAY), evidence)
    assert noaa_absence_witness(conn,CITY,DAY,as_of=clock) is None
    assert observation_selection(conn,CITY,DAY,"wu_icao_history",as_of=clock) is None
    # An atom carrying a witness without the proof is equally inert.
    stale={"rule":"noaa_wrh_then_wu_next_day_2359_ET_v1","absence_basis":EMPTY_AFTER_DEADLINE,
           "station_id":"KBKF","target_date":DAY,"page_view":"hourly",
           "absence_observed_at":fallback_deadline(DAY).isoformat()}
    row={"high_provenance_metadata":json.dumps({"resolver_fallback":stale})}
    assert observation_selection(conn,CITY,DAY,"wu_icao_history",row=row,as_of=clock) is None
    conn.close()


def test_legacy_reason_only_row_is_revalidated_before_fallback(tmp_path, monkeypatch):
    """Pre-fix reason-only row -> restart -> no fallback until a re-fetch re-mints proof."""
    from datetime import date
    from src.data import settlement_observation_selection as selection
    from src.data import daily_obs_append as appender
    from tests.test_noaa_wrh_settlement_product import (
        _FrozenAfterDeadline, _attached, _empty_product_body, _live_schema_db_pair, _stub_wrh_http,
    )
    monkeypatch.setattr(selection, "datetime", _FrozenAfterDeadline)
    city = __import__("src.config", fromlist=["cities_by_name"]).cities_by_name["Houston"]
    day = date(2026, 9, 11)
    forecasts, world = _live_schema_db_pair(tmp_path)
    legacy = sqlite3.connect(world)
    legacy.execute(
        "INSERT INTO data_coverage(data_table,city,data_source,target_date,sub_key,status,reason,fetched_at,retry_after) "
        "VALUES('observations','Houston','noaa_wrh_khou','2026-09-11','','FAILED',?,?,?)",
        (EMPTY_AFTER_DEADLINE, "2026-09-12T04:00:00+00:00", "2026-09-12T05:00:00+00:00"))
    legacy.commit(); legacy.close()
    conn = _attached(forecasts, world)  # restart: a fresh process reads the durable row
    try:
        assert noaa_absence_witness(conn, city, "2026-09-11") is None
        assert observation_selection(conn, city, "2026-09-11", "wu_icao_history") is None
        # The legacy row is retry-ready FAILED debt, so catch-up re-fetches it.
        offered = []
        def collect(name, dates, *a, **k):
            offered.append((name, list(dates))); return {"inserted": 0, "guard_rejected": 0}
        monkeypatch.setattr(appender, "append_noaa_wrh_city", collect)
        appender.catch_up_missing(conn, days_back=3650)
        assert ("Houston", [day]) in offered
        monkeypatch.undo()
        monkeypatch.setattr(selection, "datetime", _FrozenAfterDeadline)
        _stub_wrh_http(monkeypatch, [_empty_product_body()])
        appender.append_noaa_wrh_city("Houston", [day], conn,
            now_utc=datetime(2026, 9, 13, 4, 10, tzinfo=timezone.utc))
        # Coverage rows carry the real wall clock; read them as of now.
        monkeypatch.undo()
        now = datetime.now(timezone.utc)
        witness = noaa_absence_witness(conn, city, "2026-09-11", as_of=now)
        assert witness is not None and witness["absence_proof"]["proof_version"] == PROOF_VERSION
        assert observation_selection(conn, city, "2026-09-11", "wu_icao_history",
                                     as_of=now)[1]["selected"] == "FALLBACK_WU"
    finally:
        conn.close()
