# Created: 2026-10-06
# Last reused/audited: 2026-10-06
# Authority basis: finite_evidence_probability_symmetry/PLAN.md cloud-only physical exit repair.
"""Current WRH membership reaches the held adapter without a monotone event.

Real native product, writer, body replay and current source reader. Focused
monitor orchestration tests stop at the adapter seam; independent acceptance
owns the unmocked positive-q adapter/selector relationship.
"""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from src.config import cities_by_name
from src.contracts.exceptions import ObservationUnavailableError
from src.engine import monitor_refresh as monitor
from src.data.daily_obs_append import append_current_noaa_wrh_product
from src.state.portfolio import Position
from tests.test_noaa_wrh_settlement_product import _attached, _current_product, _live_schema_db_pair

UTC = timezone.utc
BASE = datetime(2026, 10, 6, 2, tzinfo=UTC)


@pytest.fixture
def current_owner(tmp_path, monkeypatch):
    from src.state import db
    from src.state.schema.observation_prints_schema import ensure_table
    forecasts, world = _live_schema_db_pair(tmp_path)
    monkeypatch.setattr(db, "ZEUS_FORECASTS_DB_PATH", forecasts)
    monkeypatch.setattr(db, "ZEUS_WORLD_DB_PATH", world)
    with sqlite3.connect(world) as wc:
        ensure_table(wc)
        wc.execute("""CREATE TABLE opportunity_events (
            event_id TEXT,event_type TEXT,entity_key TEXT,source TEXT,observed_at TEXT,
            available_at TEXT,received_at TEXT,causal_snapshot_id TEXT,payload_hash TEXT,
            idempotency_key TEXT,priority INTEGER,expires_at TEXT,payload_json TEXT,
            schema_version INTEGER,created_at TEXT)""")
        wc.execute("CREATE INDEX idx_opportunity_events_day0_family_extreme ON opportunity_events(event_type)")
        wc.commit()
    conn = _attached(forecasts, world)
    position = Position(trade_id="wrh-held", market_id="wrh-market", condition_id="0x"+"a"*64,
        city="Singapore", cluster="asia", target_date="2026-10-06", temperature_metric="high",
        bin_label="31°C", direction="buy_yes", token_id="held-yes", no_token_id="held-no",
        shares=10.0, chain_shares=10.0, chain_state="synced", entry_price=.4, size_usd=4.0,
        state="day0_window", env="live")
    def write(values, minute=0):
        now = BASE + timedelta(minutes=minute)
        product = _current_product(values=values, receipt=now.isoformat())
        append_current_noaa_wrh_product(conn, city=cities_by_name[position.city],
            target_date=position.target_date, product=product, as_of=now)
        conn.commit()
        return now, product
    try:
        yield conn, position, write
    finally:
        conn.close()


def _build(position, now):
    return monitor._build_current_global_day0_family_snapshot(position, trade_conn=object(),
        decision_time=now, cached_snapshots=(), deadline_monotonic=time.monotonic()+10,
        hwm_deadline_monotonic=time.monotonic()+10)


class ReachedAdapter(Exception):
    pass


def _observe_adapter(monkeypatch, *, blocked=False):
    from src.data import replacement_forecast_bundle_reader as reader
    from src.engine import event_reactor_adapter as adapter
    seen = []
    def prior(_conn, **kwargs):
        if blocked:
            return SimpleNamespace(ok=False,status="BLOCKED",reason_code="CARRIER_CUSTODY_INVALID")
        return SimpleNamespace(ok=False,status="MISSING",reason_code="MISSING",bundle=None)
    def prepare(event, **kwargs):
        seen.append((event,kwargs))
        raise ReachedAdapter
    monkeypatch.setattr(reader,"read_prior_complete_replacement_forecast_bundle",prior)
    monkeypatch.setattr(adapter,"_prepare_current_global_probability_family",prepare)
    return seen


@pytest.mark.parametrize("metric,value",[("high",29.0),("low",28.0)])
def test_qualified_current_wrh_without_monotone_event_reaches_current_adapter(current_owner,monkeypatch,metric,value):
    conn,position,write=current_owner
    position.temperature_metric=metric
    now,product=write((29.0,28.0))
    seen=_observe_adapter(monkeypatch)
    with pytest.raises(ReachedAdapter):
        _build(position,now)
    event,kwargs=seen[0]
    payload=json.loads(event.payload_json)
    assert payload['raw_value']==value
    assert payload['raw_report_identity']==product.response_sha256
    assert payload['evidence_finality']=='PROVISIONAL_CURRENT_SNAPSHOT'
    assert event.available_at==now.isoformat()==event.received_at
    assert kwargs['pinned_complete_bundle'] is None
    assert conn.execute('SELECT COUNT(*) FROM world.opportunity_events').fetchone()[0]==0


def _legacy_event(conn, position, *, value=32.0):
    from src.events.opportunity_event import Day0ExtremeUpdatedPayload, make_day0_extreme_updated_event
    from src.events.day0_authority import DAY0_LIVE_AUTHORITY_MATCHES
    event=make_day0_extreme_updated_event(entity_key='Singapore|2026-10-06|high|WSSS',
        source='day0_extreme_updated_trigger', observed_at='2026-10-06T01:00:00+00:00',
        received_at=BASE.isoformat(), payload=Day0ExtremeUpdatedPayload(
            city=position.city,target_date=position.target_date,metric='high',settlement_source='noaa_wrh_wsss',
            station_id='WSSS',observation_time='2026-10-06T01:00:00+00:00',observation_available_at=BASE.isoformat(),
            raw_value=value,rounded_value=int(value),high_so_far=value,settlement_source_type='noaa',
            evidence_finality='MONOTONE_SETTLEMENT_BOUND', **DAY0_LIVE_AUTHORITY_MATCHES))
    fields=('event_id','event_type','entity_key','source','observed_at','available_at','received_at',
            'causal_snapshot_id','payload_hash','idempotency_key','priority','expires_at','payload_json','schema_version','created_at')
    conn.execute('INSERT INTO world.opportunity_events VALUES ('+','.join('?' for _ in fields)+')',tuple(getattr(event,k) for k in fields))
    conn.commit()
    return event


def test_downward_current_correction_cannot_overlay_old_pinned_carrier(current_owner,monkeypatch):
    from src.data import replacement_forecast_bundle_reader as reader
    conn,position,write=current_owner
    write((32.0,28.0))
    old=_legacy_event(conn,position)
    now,product=write((29.0,28.0),1)
    seen=_observe_adapter(monkeypatch)
    overlay=[]
    old_provenance={'day0_provisional_observation':{'active':True,'source':'noaa_wrh_wsss',
        'metric':'high','unit':'C','observation_time':'2026-10-06T01:00:00+00:00','observed_extreme_c':32.0}}
    def prior(_conn,**kwargs):
        if 'consumable' not in kwargs:
            return SimpleNamespace(ok=False,status='BLOCKED',reason_code='REPLACEMENT_RAW_INPUT_HWM_REQUIRED_RETRYABLE')
        overlay.append(kwargs['consumable'](old_provenance))
        return SimpleNamespace(ok=False,status='MISSING',reason_code='MISSING',bundle=None)
    monkeypatch.setattr(reader,'read_prior_complete_replacement_forecast_bundle',prior)
    with pytest.raises(ReachedAdapter):
        _build(position,now)
    assert overlay==[False]
    event,kwargs=seen[0]
    assert event.event_id!=old.event_id
    assert json.loads(event.payload_json)['raw_value']==29.0
    assert kwargs['pinned_complete_bundle'] is None
    assert conn.execute('SELECT COUNT(*) FROM world.opportunity_events').fetchone()[0]==1


@pytest.mark.parametrize('failure',['empty','unknown','invalid_owner'])
def test_unknown_or_empty_current_owner_never_resurrects_old_event(current_owner,monkeypatch,failure):
    conn,position,write=current_owner
    write((32.0,28.0))
    _legacy_event(conn,position)
    now=BASE+timedelta(minutes=1)
    if failure=='empty':
        write((),1)
    elif failure=='unknown':
        conn.execute('DROP TABLE observations')
        conn.commit()
    else:
        conn.execute("UPDATE observations SET authority='QUARANTINED'")
        conn.commit()
    seen=_observe_adapter(monkeypatch)
    with pytest.raises(ObservationUnavailableError,match='WRH_CURRENT_SNAPSHOT_UNAVAILABLE'):
        _build(position,now)
    assert seen==[]
    assert conn.execute('SELECT COUNT(*) FROM world.opportunity_events').fetchone()[0]==1


def test_current_owner_does_not_bypass_blocked_carrier_reader(current_owner,monkeypatch):
    _,position,write=current_owner
    now,_=write((29.0,28.0))
    seen=_observe_adapter(monkeypatch,blocked=True)
    with pytest.raises(ValueError,match='GLOBAL_HELD_PINNED_COMPLETE_POSTERIOR_BLOCKED:CARRIER_CUSTODY_INVALID'):
        _build(position,now)
    assert seen==[]


def test_current_wrh_carrier_identity_is_stable_across_semantic_noop(current_owner):
    conn,position,write=current_owner
    now,_=write((29.0,28.0))
    first=monitor._current_wrh_monitor_observation_carrier(conn,position,now=now)
    later,_=write((29.0,28.0),1)
    repeated=monitor._current_wrh_monitor_observation_carrier(conn,position,now=later)
    assert repeated.event_id==first.event_id
    assert repeated.payload_hash==first.payload_hash
    assert repeated.created_at==later.isoformat()
    assert repeated.received_at==BASE.isoformat()
    assert conn.execute('SELECT COUNT(*) FROM world.opportunity_events').fetchone()[0]==0


def test_source_revision_during_carrier_read_requires_redecision(current_owner,monkeypatch):
    from src.data import replacement_forecast_current_target_plan as reader
    conn,position,write=current_owner
    write((32.0,28.0))
    now=BASE+timedelta(minutes=1)
    real=reader._latest_authorized_day0_fact
    def correct_then_read(*args,**kwargs):
        write((29.0,28.0),1)
        return real(*args,**kwargs)
    monkeypatch.setattr(reader,'_latest_authorized_day0_fact',correct_then_read)
    with pytest.raises(ObservationUnavailableError,match='SUPERSEDED_DURING_READ'):
        monitor._current_wrh_monitor_observation_carrier(conn,position,now=now)


def test_proven_absent_owner_retains_existing_event_route(current_owner,monkeypatch):
    conn,position,_=current_owner
    old=_legacy_event(conn,position)
    seen=_observe_adapter(monkeypatch)
    with pytest.raises(ReachedAdapter):
        _build(position,BASE)
    assert seen[0][0]==old
