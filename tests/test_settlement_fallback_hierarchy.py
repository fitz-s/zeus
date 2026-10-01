# Created: 2026-09-29
# Last reused/audited: 2026-10-01
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
)

CITY=SimpleNamespace(name="fixture",wu_station="KBKF",settlement_source_type="noaa",
                     settlement_page_view="hourly",settlement_unit="F")
DAY="2026-09-27"

def database():
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE data_coverage(data_table TEXT,city TEXT,data_source TEXT,target_date TEXT,sub_key TEXT,status TEXT,reason TEXT,fetched_at TEXT)")
    conn.execute("CREATE TABLE observations(id INTEGER,city TEXT,target_date TEXT,source TEXT,high_temp REAL,low_temp REAL,unit TEXT,station_id TEXT,authority TEXT,fetched_at TEXT,high_local_time TEXT,low_local_time TEXT,high_provenance_metadata TEXT)")
    return conn

@pytest.mark.parametrize("reason", ["NETWORK_ERROR","AUTH_ERROR","OUTSIDE_LANE_REQUEST_WINDOW","GUARD_REJECTED"])
def test_local_failure_is_never_upstream_absence(reason):
    conn=database();after=fallback_deadline(DAY)+timedelta(minutes=1)
    conn.execute("INSERT INTO data_coverage VALUES('observations','fixture','noaa_wrh_kbkf',?,'','FAILED',?,?)",(DAY,reason,after.isoformat()))
    assert noaa_absence_witness(conn,CITY,DAY,as_of=after) is None
    assert observation_selection(conn,CITY,DAY,"wu_icao_history",as_of=after) is None
    assert observation_selection(conn,CITY,DAY,"ogimet_metar_kbkf",as_of=after) is None
    conn.close()

def test_exact_et_deadline_and_durable_wu_witness():
    conn=database();deadline=fallback_deadline(DAY)
    assert deadline.isoformat()=="2026-09-29T03:59:00+00:00"
    assert fallback_deadline("2026-11-01").hour==4
    conn.execute("INSERT INTO data_coverage VALUES('observations','fixture','noaa_wrh_kbkf',?,'','FAILED',?,?)",(DAY,EMPTY_AFTER_DEADLINE,deadline.isoformat()))
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
    conn.execute("INSERT INTO data_coverage VALUES('observations','fixture','noaa_wrh_kbkf',?,'','FAILED',?,?)",(DAY,EMPTY_AFTER_DEADLINE,deadline.isoformat()))
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
    conn.execute("INSERT INTO data_coverage VALUES('observations','fixture','noaa_wrh_kbkf',?,'','FAILED',?,?,NULL)",
                 (DAY,EMPTY_AFTER_DEADLINE,clock.isoformat()))
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
        "target_date":DAY,"page_view":"hourly","absence_observed_at":stamp.isoformat()}
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
