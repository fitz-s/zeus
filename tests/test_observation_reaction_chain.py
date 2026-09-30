# Created: 2026-09-29
# Last reused/audited: 2026-09-29
# Authority: REQ-20260929-223929-bf51a2; isolated canonical materializer/reader integration.
"""Controlled forecast inputs, real Day0 integration and posterior persistence.

This harness is not production evidence: external forecasts and venue responses
are fixtures. The posterior calculation and semantic readiness checks are real.
"""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
import logging
import time
from types import SimpleNamespace
import pytest

from tests import test_replacement_forecast_materializer as fixtures
from tests import test_replacement_forecast_bundle_reader as reader_fixtures
from src.data.day0_hourly_vectors import Day0HourlyVector, day0_source_clock_ensemble_member_models
from src.data.replacement_forecast_materializer import materialize_replacement_forecast_live
from src.data.replacement_forecast_bundle_reader import read_replacement_forecast_bundle
from src.runtime.observation_reaction_trace import (
    completed_trace, emit_posterior_ready, emit_stage, emit_venue_ack,
)

_materializer_unit_source_surface = fixtures._materializer_unit_source_surface


def test_observation_revision_materializes_then_serves(monkeypatch,caplog,_materializer_unit_source_surface):
    caplog.set_level(logging.INFO,logger="zeus.observation_reaction")
    conn=fixtures._conn()
    fixtures._install_live_fusion(monkeypatch,snapshot_id=1)
    reader_fixtures._insert_ensemble_snapshot(conn,snapshot_id=1,
        source_cycle_time=fixtures._dt(0),available_at=fixtures._dt(2))
    now=fixtures._dt(18,10)
    request=fixtures._request(computed_at=now,expires_at=datetime(2026,6,7,2,tzinfo=timezone.utc),
        day0_observed_extreme_c=31.0,day0_observed_extreme_source="aviationweather_metar",
        day0_observed_extreme_observation_time=fixtures._dt(18,5).isoformat())
    conn.execute("CREATE TABLE observation_prints(id INTEGER PRIMARY KEY,city TEXT,station_id TEXT,source_channel TEXT,publish_ts_utc TEXT,value_native REAL,unit TEXT,fetched_at_utc TEXT,raw_report TEXT)")
    response_received_at_ms=time.time_ns()//1_000_000
    conn.execute("INSERT INTO observation_prints VALUES(1,'Shanghai','ZSPD','aviationweather_metar',?,30,'C',?,?)",
        (fixtures._dt(18,5).isoformat(),fixtures._dt(18,5).isoformat(),"METAR ZSPD 061805Z 30/20 T03000200"))
    conn.commit()
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
    started=time.monotonic_ns()
    result=materialize_replacement_forecast_live(conn,request)
    assert result.ok, result
    conn.commit()
    materialized=time.monotonic_ns()
    row=conn.execute("SELECT * FROM forecast_posteriors WHERE posterior_id=?",(result.posterior_id,)).fetchone()
    provenance=json.loads(row["provenance_json"])
    assert provenance.get("day0_current_temperature_state"),provenance
    emit_posterior_ready(conn,result.posterior_id,wake_published=False)
    from src.data.replacement_forecast_readiness import ReplacementForecastReadinessDecision
    cert=conn.execute("SELECT * FROM readiness_state WHERE readiness_id=?",(result.readiness_id,)).fetchone()
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

    # Non-production venue observation: exercise the real telemetry join without
    # a network side effect. The command uses the exact q_version the live
    # venue-command row would carry.
    conn.execute("CREATE TABLE venue_commands(command_id TEXT PRIMARY KEY,q_version TEXT,token_id TEXT)")
    conn.execute("INSERT INTO venue_commands VALUES(?,?,?)",
        ("fixture-command",row["posterior_identity_hash"],"fixture-token"))
    ack_at=datetime.now(timezone.utc)
    emit_venue_ack(conn,command_id="fixture-command",event_id="fixture-submit-acked",
                   occurred_at=ack_at.isoformat())

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

    measured={"posterior_id":result.posterior_id,
        "receipt_to_world_ms":world_committed_at_ms-response_received_at_ms,
        "world_to_posterior_ms":trace["posterior_ready_at_ms"]-world_committed_at_ms,
        "materialize_ms":(materialized-started)/1e6,
        "posterior_to_q_ms":trace["q_served_at_ms"]-trace["posterior_ready_at_ms"],
        "serve_ms":(q_served_monotonic-materialized)/1e6,
        "q_to_ack_ms":trace["venue_ack_at_ms"]-trace["q_served_at_ms"],
        "receipt_to_ack_ms":trace["receipt_to_ack_ms"],"q":dict(read.bundle.q)}
    print("MEASURED_HARNESS",json.dumps(measured,sort_keys=True))
    conn.close()
