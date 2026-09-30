# Created: 2026-09-29
# Last reused/audited: 2026-09-30
# Authority: REQ-20260929-223929-bf51a2; isolated canonical materializer/reader integration.
"""Controlled forecast inputs, real Day0 integration and posterior persistence.

This harness is not production evidence: external forecasts and venue responses
are fixtures. The posterior calculation and semantic readiness checks are real.
"""
from dataclasses import asdict, replace
from contextlib import nullcontext
from decimal import Decimal
import sqlite3
from datetime import datetime,timedelta,timezone
import json
import logging
import time
from types import SimpleNamespace
import pytest

from tests import test_replacement_forecast_materializer as fixtures
from tests import test_replacement_forecast_bundle_reader as reader_fixtures
from tests import test_exit_safety as exit_fixtures
from src.data.day0_hourly_vectors import Day0HourlyVector, day0_source_clock_ensemble_member_models
from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle
from src.runtime.observation_reaction_trace import (
    completed_trace, emit_posterior_ready, emit_stage, emit_venue_ack,
)

_materializer_unit_source_surface = fixtures._materializer_unit_source_surface
trade_schema = exit_fixtures.conn


@pytest.mark.parametrize('incumbent_without_carrier',[False,True])
def test_observation_revision_materializes_then_serves(monkeypatch,caplog,tmp_path,trade_schema,_materializer_unit_source_surface,incumbent_without_carrier):
    import scripts.materialize_replacement_forecast_live as cli
    import src.main as main  # Already resident in a warm trading process.
    from src.data import replacement_fusion_upgrade_trigger as delivery
    from src.runtime import reactor_wake
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.venue_command_repo import get_command, list_events
    from src.state.schema.observation_prints_schema import append_print, ensure_table

    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    forecast_path, world_path, trade_path = (tmp_path/name for name in ('forecast.db','world.db','trade.db'))
    seed=fixtures._conn()
    conn=sqlite3.connect(forecast_path);conn.row_factory=sqlite3.Row
    seed.backup(conn);seed.close()
    trade=sqlite3.connect(trade_path);trade.row_factory=sqlite3.Row
    trade_schema.backup(trade)
    world=sqlite3.connect(world_path);ensure_table(world);world.commit()
    # Production-like ownership: writes use distinct WORLD/FORECAST/TRADE files.
    # Only WORLD is attached read-only while preparing a posterior.
    conn.execute('ATTACH DATABASE ? AS world',(f'file:{world_path}?mode=ro',))
    exit_fixtures._enable_exit_submit_prereqs(trade,monkeypatch)
    snapshot_id=exit_fixtures._ensure_snapshot(trade,snapshot_id='source-reaction-book',
        orderbook_top_bid='0.74',orderbook_top_ask='0.75')
    trade.execute("INSERT INTO position_current(position_id,phase,market_id,city,target_date,temperature_metric,chain_state,chain_shares,chain_cost_basis_usd,updated_at) VALUES('source-reaction-held','active','condition-test','Shanghai','2026-06-07','high','synced',5,2,'2026-06-06T18:00:00Z')")
    trade.commit()
    source_models=('ecmwf_ifs9','gfs','icon')
    fixtures._install_live_fusion(monkeypatch,snapshot_id=1,
        current_serving={model:{'raw_model_forecast_id':101+index,'served_via':'single_runs'}
                         for index,model in enumerate(source_models)})
    for index,model in enumerate(source_models):
        conn.execute('''INSERT INTO raw_model_forecasts
            (raw_model_forecast_id,model,city,target_date,metric,source_cycle_time,
             source_available_at,captured_at,lead_days,forecast_value_c,endpoint,recorded_at,coverage_status)
            VALUES(?,?,'Shanghai','2026-06-07','high',?,?,?,1,25.0,'single_runs',?,'COVERED')''',
            (101+index,model,fixtures._dt(0).isoformat(),fixtures._dt(3).isoformat(),
             fixtures._dt(3).isoformat(),fixtures._dt(3).isoformat()))
    reader_fixtures._insert_ensemble_snapshot(conn,snapshot_id=1,
        source_cycle_time=fixtures._dt(0),available_at=fixtures._dt(2))
    if incumbent_without_carrier:
        incumbent=materialize_replacement_forecast_live(conn,fixtures._request())
        assert incumbent.ok,incumbent
        conn.commit()
        prior=conn.execute('SELECT provenance_json FROM forecast_posteriors WHERE posterior_id=?',(incumbent.posterior_id,)).fetchone()
        assert not json.loads(prior[0]).get('day0_current_temperature_state')
    now=fixtures._dt(18,10)
    request=fixtures._request(computed_at=now,expires_at=datetime(2026,6,7,2,tzinfo=timezone.utc),
        day0_observed_extreme_c=31.0,day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=fixtures._dt(18,5).isoformat())
    response_received_at_ms=time.time_ns()//1_000_000
    append_print(world,city='Shanghai',station_id='ZSPD',source_channel='aviationweather_metar',
        publish_ts_utc=fixtures._dt(18,5).isoformat(),value_native=30.0,unit='C',
        fetched_at_utc=fixtures._dt(18,5).isoformat(),raw_report='METAR ZSPD 061805Z 30/20 T03000200')
    world.commit()
    world_committed_at_ms=time.time_ns()//1_000_000
    input_identity={"source":"aviationweather_metar",
        "observed_at_utc":fixtures._dt(18,5).isoformat(),"value_native":30.0}
    emit_stage("SOURCE_COMMITTED",city="Shanghai",station_id="ZSPD",
        source_channel="aviationweather_metar",input_identity=input_identity,
        response_received_at_ms=response_received_at_ms,
        world_committed_at_ms=world_committed_at_ms)
    vector=Day0HourlyVector(model="ecmwf_ifs",city="Shanghai",target_date="2026-06-07",
        timezone_name="Asia/Shanghai",captured_at=fixtures._dt(18,8).isoformat(),
        times=tuple(f"2026-06-07T{hour:02d}:00" for hour in range(24)),
        temps_c=tuple(29.0 if hour<12 else 31.0 for hour in range(24)))
    def meta(model,ensemble=False):
        return json.dumps({"provider_source_cycle_time_utc":fixtures._dt(6).isoformat(),
            "provider_source_available_at_utc":fixtures._dt(7).isoformat(),
            "fetch_finished_at":fixtures._dt(18,9).isoformat(),
            "request_hash":"same-ensemble" if ensemble else model,
            "provider_run_id":"same-ensemble" if ensemble else model})
    providers=[replace(vector,source_run_meta_json=meta("ecmwf_ifs")),
               replace(vector,model="icon_global",source_run_meta_json=meta("icon_global"))]
    ensemble=[replace(vector,model=m,source_run_meta_json=meta(m,True)) for m in day0_source_clock_ensemble_member_models()]
    monkeypatch.setattr("src.data.day0_hourly_vectors.day0_hourly_models_for_city",lambda _:[v.model for v in providers])
    monkeypatch.setattr("src.data.day0_hourly_vectors.read_freshest_day0_hourly_vectors",
        lambda **kw:ensemble if len(kw.get("expected_models") or ())==51 else providers)
    def encode(value):
        return value.isoformat() if hasattr(value,'isoformat') else str(value)
    (tmp_path/'anchor.json').write_bytes(request.openmeteo_raw_payload_bytes)
    (tmp_path/'precision.json').write_text(json.dumps(asdict(request.openmeteo_precision_guard.metadata),default=encode))
    fields=('city','city_id','city_timezone','temperature_metric','baseline_source_run_id','baseline_data_version',
            'baseline_source_available_at','openmeteo_source_run_id','openmeteo_source_available_at','source_cycle_time',
            'computed_at','expires_at','target_date','day0_observed_extreme_c','day0_observed_extreme_source',
            'day0_observed_extreme_observation_time')
    payload={key:getattr(request,key) for key in fields}
    payload.update(openmeteo_payload_json='anchor.json',precision_metadata_json='precision.json',bins=[asdict(b) for b in request.bins])
    request_path=tmp_path/'request.json';request_path.write_text(json.dumps(payload,default=encode))
    wake_path=tmp_path/'wake.json'
    publish=reactor_wake.publish_reactor_wake
    def notify_after_ready(**kwargs):
        assert not conn.in_transaction
        assert any('"stage": "POSTERIOR_READY"' in record.getMessage() for record in caplog.records)
        return publish(**kwargs,path=wake_path)
    monkeypatch.setattr(reactor_wake,'publish_reactor_wake',notify_after_ready)
    monkeypatch.setattr('src.state.db.get_world_connection_read_only',
        lambda: sqlite3.connect(f'file:{world_path}?mode=ro',uri=True))
    offered=delivery.scope_capture_offers_larger_provider_set(conn,city='Shanghai',target_date='2026-06-07',
        metric='high',decision_time=now,changed_sources=('day0_current_temperature_state',))
    assert offered['is_upgrade'],offered
    assert offered['changed_input_revisions']['day0_current_temperature_state']==input_identity
    conn.commit()  # Fixture source runs must be visible before CLI index/bootstrap.
    started=time.monotonic_ns()
    with reactor_wake.reactor_wake_listener_socket(path=wake_path) as listener:
        assert listener is not None
        listener.settimeout(2)
        code,response=cli._materialize(request_path,commit=True,init_schema=False,conn=conn,
            schema_ready=True,writer_lock=nullcontext)
        assert code==0,response
        materialized=time.monotonic_ns()
        assert listener.recv(1)==b'\x01'
        wake_received_at_ms=time.time_ns()//1_000_000
        wake=reactor_wake.read_reactor_wake(path=wake_path)
        assert wake is not None and wake.reason=='forecast_posterior_advanced'
    assert wake.forecast_families == (('Shanghai','2026-06-07','high'),)
    # Exercise the production held-family selector against a separate read-only
    # TRADE connection; an ended local date does not erase money at risk.
    monkeypatch.setattr('src.state.db.get_trade_connection_read_only',
        lambda: sqlite3.connect(f'file:{trade_path}?mode=ro',uri=True))
    assert main._forecast_wake_held_families(wake.forecast_families)==frozenset(wake.forecast_families)
    row=conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(response['posterior_id'],)).fetchone()
    provenance=json.loads(row['provenance_json'])
    assert provenance['day0_current_temperature_state']==input_identity
    consumed=delivery.scope_capture_offers_larger_provider_set(conn,city='Shanghai',target_date='2026-06-07',
        metric='high',decision_time=now,changed_sources=('day0_current_temperature_state',))
    assert not consumed['is_upgrade'],consumed
    from src.data.replacement_forecast_readiness import ReplacementForecastReadinessDecision
    cert=conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",(response['readiness_id'],)).fetchone()
    ready=ReplacementForecastReadinessDecision(
        readiness_id=cert["readiness_id"],status=cert["status"],
        reason_codes=tuple(json.loads(cert["reason_codes_json"])),
        dependency_json=json.loads(cert["dependency_json"]),
        provenance_json=json.loads(cert["provenance_json"]),
        expires_at=datetime.fromisoformat(cert["expires_at"]))
    read=read_replacement_forecast_bundle(conn,
        baseline_bundle=reader_fixtures._BaselineBundle(reader_fixtures._Evidence("b0-run")),
        readiness=ready,city="Shanghai",target_date="2026-06-07",temperature_metric="high",
        decision_time=now,current_bin_topology_hash=row["bin_topology_hash"])
    assert read.ok, read
    assert read.bundle.q == pytest.approx(json.loads(row["q_json"]))
    assert abs(sum(read.bundle.q.values())-1)<1e-9
    q_served_monotonic=time.monotonic_ns()

    # The scenario requests a reduce-only SELL after serving the revised q;
    # global-auction selection policy is deliberately outside this harness.
    # Only the venue client is fake; command/envelope/collateral checks,
    # intent-before-side-effect ordering and ACK journal are production code.
    submitted=[]
    class FakeVenue:
        def bind_submission_envelope(self,envelope): self.envelope=envelope
        def bind_signed_submission_identity_persister(self,persister): self.persister=persister
        def get_collateral_payload(self): return exit_fixtures._fresh_exit_collateral_payload()
        def place_limit_order(self,**kwargs):
            cmd=trade.execute('SELECT command_id,state,q_version FROM venue_commands ORDER BY created_at DESC LIMIT 1').fetchone()
            assert cmd is not None and cmd['state']=='SUBMITTING'
            assert cmd['q_version']==read.bundle.posterior_identity_hash
            submitted.append(kwargs)
            return exit_fixtures._fake_submit_result(self.envelope,order_id='fixture-venue-order')
    monkeypatch.setattr('src.data.polymarket_client.PolymarketClient',FakeVenue)
    try:
        order=execute_exit_order(create_exit_order_intent(
            trade_id='source-reaction-held',token_id=exit_fixtures.YES_TOKEN,shares=5,
            current_price=0.75,best_bid=0.74,exact_limit_price=0.75,submit_order_type='GTC',
            executable_snapshot_id=snapshot_id,
            executable_snapshot_hash=exit_fixtures._snapshot_hash(trade,snapshot_id),
            executable_snapshot_min_tick_size=Decimal('0.01'),executable_snapshot_min_order_size=Decimal('0.01'),
            executable_snapshot_neg_risk=False),conn=trade,decision_id='source-reaction-decision',
            q_version=read.bundle.posterior_identity_hash)
        assert submitted,order
        trade.commit()
        command=get_command(trade,order.command_id)
        assert command['state']=='ACKED',order
        journal=list_events(trade,order.command_id)
        assert any(event['event_type']=='SUBMIT_ACKED' for event in journal)
    finally:
        exit_fixtures._clear_exit_submit_prereqs()

    events=[]
    for record in caplog.records:
        message=record.getMessage()
        prefix="OBSERVATION_REACTION_TRACE "
        if message.startswith(prefix):
            events.append(json.loads(message[len(prefix):]))
    trace=completed_trace(events,posterior_identity_hash=row["posterior_identity_hash"])
    assert trace["status"]=="OBSERVED_COMPLETE",trace
    assert isinstance(trace["q_served_at_ms"],int)
    assert isinstance(trace["venue_ack_at_ms"],int)
    assert trace["q_served_at_ms"]>=trace["posterior_ready_at_ms"]>=trace["world_committed_at_ms"]
    assert trace["venue_ack_at_ms"]>=trace["q_served_at_ms"]

    assert trace['command_id']==order.command_id
    assert any(event['event_id']==trace['event_id'] for event in journal)
    measured={"posterior_id":response['posterior_id'],
        "receipt_to_world_ms":world_committed_at_ms-response_received_at_ms,
        "world_to_posterior_ms":trace["posterior_ready_at_ms"]-world_committed_at_ms,
        "materialize_ms":(materialized-started)/1e6,
        "incumbent_without_carrier":incumbent_without_carrier,
        "posterior_to_wake_ms":wake_received_at_ms-trace['posterior_ready_at_ms'],
        "wake_to_q_ms":trace['q_served_at_ms']-wake_received_at_ms,
        "posterior_to_q_ms":trace["q_served_at_ms"]-trace["posterior_ready_at_ms"],
        "serve_ms":(q_served_monotonic-materialized)/1e6,
        "q_to_ack_ms":trace["venue_ack_at_ms"]-trace["q_served_at_ms"],
        "receipt_to_ack_ms":trace["receipt_to_ack_ms"],"q":dict(read.bundle.q)}
    print("MEASURED_HARNESS",json.dumps(measured,sort_keys=True))
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='observation_prints'").fetchone()[0]==0
    assert world.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='forecast_posteriors'").fetchone()[0]==0
    conn.close();world.close();trade.close()
