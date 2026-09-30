# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""Non-authoritative causal stage telemetry; no DB writes or trading actions."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Iterable, Mapping

_LOG=logging.getLogger("zeus.observation_reaction")
_FIELDS=frozenset({"city","target_date","metric","station_id","source_channel","input_identity",
    "response_received_at_ms","provider_observed_at_ms","world_committed_at_ms",
    "posterior_id","posterior_identity_hash","q_version","command_id","token_id",
    "event_id","venue_ack_at_ms","q_served_at_ms","posterior_ready_at_ms","wake_published"})

def emit_stage(stage: str, **fields: Any) -> dict[str, Any]:
    """Clock the actual stage, not a projected future completion."""
    record={"stage":stage,"recorded_at_ms":time.time_ns()//1_000_000,
            **{key:value for key,value in fields.items() if key in _FIELDS}}
    if stage=="Q_SERVED":record["q_served_at_ms"]=record["recorded_at_ms"]
    if stage=="POSTERIOR_READY":record["posterior_ready_at_ms"]=record["recorded_at_ms"]
    try:_LOG.info("OBSERVATION_REACTION_TRACE %s",json.dumps(record,sort_keys=True,allow_nan=False))
    except Exception:pass  # Telemetry cannot change data/serving/venue authority.
    return record

def emit_posterior_ready(conn: Any, posterior_id: int, *, wake_published: bool) -> None:
    try:
        row=conn.execute("SELECT city,target_date,temperature_metric,posterior_identity_hash,provenance_json "
                         "FROM forecast_posteriors WHERE posterior_id=?",(posterior_id,)).fetchone()
        if row is None:return
        prov=json.loads(row[4] or "{}")
        emit_stage("POSTERIOR_READY",city=row[0],target_date=row[1],metric=row[2],
            posterior_id=posterior_id,posterior_identity_hash=row[3],
            input_identity=prov.get("day0_current_temperature_state"),wake_published=wake_published)
    except Exception:pass

def emit_q_served(bundle: Any) -> None:
    try:
        emit_stage("Q_SERVED",city=bundle.city,target_date=bundle.target_date,
            metric=bundle.temperature_metric,posterior_id=bundle.posterior_id,
            posterior_identity_hash=bundle.posterior_identity_hash,
            q_version=bundle.posterior_identity_hash,
            input_identity=bundle.provenance_json.get("day0_current_temperature_state"))
    except Exception:pass

def emit_venue_ack(conn: Any, *, command_id: str, event_id: str, occurred_at: str) -> None:
    try:
        row=conn.execute("SELECT q_version,token_id FROM venue_commands WHERE command_id=?",(command_id,)).fetchone()
        if row is None:return
        clock=datetime.fromisoformat(occurred_at.replace("Z","+00:00"))
        if clock.tzinfo is None:return
        emit_stage("VENUE_ACK_OBSERVED",command_id=command_id,event_id=event_id,
            q_version=row[0],token_id=row[1],venue_ack_at_ms=int(clock.timestamp()*1000))
    except Exception:pass

def completed_trace(events: Iterable[Mapping[str, Any]], *, posterior_identity_hash: str) -> dict[str, Any]:
    """Strict lineage join. Missing action or unrelated q stays explicitly absent.

    Venue ACK telemetry is an observation, not a commit proof; callers auditing
    canonical finality additionally verify event_id against the TRADE ledger.
    """
    rows=list(events)
    ready=[r for r in rows if r.get("stage")=="POSTERIOR_READY" and r.get("posterior_identity_hash")==posterior_identity_hash]
    if not ready:return {"status":"POSTERIOR_NOT_PROVEN"}
    parent=min(ready,key=lambda r:r["posterior_ready_at_ms"])
    if not isinstance(parent.get("input_identity"), Mapping) or not parent["input_identity"]:
        return {"status":"INPUT_REVISION_NOT_PROVEN", "posterior_identity_hash":posterior_identity_hash}
    serves=[r for r in rows if r.get("stage")=="Q_SERVED" and r.get("posterior_identity_hash")==posterior_identity_hash
            and r.get("q_served_at_ms",0)>=parent["posterior_ready_at_ms"]]
    source=[r for r in rows if r.get("stage")=="SOURCE_COMMITTED"
            and r.get("city")==parent.get("city") and r.get("input_identity")==parent.get("input_identity")
            and r.get("world_committed_at_ms",2**63)<=parent["posterior_ready_at_ms"]]
    result={"status":"INCOMPLETE","posterior_identity_hash":posterior_identity_hash,
            "posterior_ready_at_ms":parent["posterior_ready_at_ms"],"q_served_at_ms":None,"venue_ack_at_ms":None}
    if not serves or not source:return result
    serve=min(serves,key=lambda r:r["q_served_at_ms"]);src=max(source,key=lambda r:r["world_committed_at_ms"])
    versions={posterior_identity_hash}
    # Reuse the existing typed Day0 binding, never fuzzy-match arbitrary hashes.
    from src.events.day0_authority import bind_day0_probability_semantics
    versions.add(bind_day0_probability_semantics(posterior_identity_hash))
    acknowledgements=[r for r in rows if r.get("stage")=="VENUE_ACK_OBSERVED" and r.get("q_version") in versions
                      and r.get("venue_ack_at_ms",0)>=serve["q_served_at_ms"]]
    result.update({key:src[key] for key in ("response_received_at_ms","world_committed_at_ms")})
    result["q_served_at_ms"]=serve["q_served_at_ms"]
    if acknowledgements:
        ack=min(acknowledgements,key=lambda r:r["venue_ack_at_ms"])
        result.update(status="OBSERVED_COMPLETE",venue_ack_at_ms=ack["venue_ack_at_ms"],command_id=ack["command_id"],event_id=ack.get("event_id"))
        result["receipt_to_ack_ms"]=ack["venue_ack_at_ms"]-src["response_received_at_ms"]
    return result
