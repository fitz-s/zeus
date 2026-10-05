# Created: 2026-09-29
# Last reused/audited: 2026-10-05
"""Non-authoritative causal telemetry. No DB writes, serving gates, or trading actions.

Observation content is not an observation revision. Explicit ledger references
are preferred; an unresolved/repeated content identity must not be assigned to
the latest matching commit merely to make a latency sample look complete.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Iterable, Mapping
import uuid

_LOG = logging.getLogger('zeus.observation_reaction')
_FIELDS = frozenset({'city','target_date','metric','station_id','source_channel','input_identity',
    'response_received_at_ms','provider_observed_at_ms','world_committed_at_ms',
    'posterior_id','posterior_identity_hash','q_version','command_id','token_id',
    'event_id','venue_ack_at_ms','q_served_at_ms','posterior_ready_at_ms','wake_published',
    'observation_ref','input_reference_status','readiness_id','readiness_computed_at_utc',
    'wake_id','wake_received_at_ms','wake_published_at_ms','decision_id','decision_outcome',
    'decision_reason','decision_at_ms','command_at_ms'})


def _utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('NAIVE_OBSERVATION_REFERENCE_CLOCK')
    return parsed.astimezone(timezone.utc)


def observation_revision_reference(row: Mapping[str, Any]) -> dict[str, Any]:
    """One shared full WORLD print revision key for emitters and offline auditors."""
    value = Decimal(str(row['value_native']))
    if not value.is_finite() or int(row['id']) <= 0:
        raise ValueError('INVALID_OBSERVATION_REFERENCE_VALUE_OR_ID')
    result = {'database':'WORLD','table':'observation_prints','id':int(row['id']),
        'city':row['city'],'station_id':row['station_id'],'source_channel':row['source_channel'],
        'publish_ts_utc':_utc(row['publish_ts_utc']).isoformat(),
        'value_native':str(value.normalize()),'unit':row['unit'],
        'fetched_at_utc':_utc(row['fetched_at_utc']).isoformat(),
        'raw_report_sha256':hashlib.sha256(str(row.get('raw_report') or '').encode()).hexdigest()}
    result['identity'] = hashlib.sha256(json.dumps(result,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result


def emit_stage(stage: str, **fields: Any) -> dict[str, Any]:
    """Record an actual stage. Monotonic clocks are only comparable within one PID."""
    record = {'stage':stage,'recorded_at_ms':time.time_ns()//1_000_000,
        'trace_schema_version':2,'trace_event_id':uuid.uuid4().hex,
        'process_id':os.getpid(),'process_monotonic_ns':time.monotonic_ns(),
        **{key:value for key,value in fields.items() if key in _FIELDS}}
    if stage == 'Q_SERVED':record['q_served_at_ms']=record['recorded_at_ms']
    if stage == 'POSTERIOR_READY':record['posterior_ready_at_ms']=record['recorded_at_ms']
    if stage == 'WAKE_RECEIVED':record['wake_received_at_ms']=record['recorded_at_ms']
    if stage == 'WAKE_PUBLISHED':record['wake_published_at_ms']=record['recorded_at_ms']
    try:_LOG.info('OBSERVATION_REACTION_TRACE %s',json.dumps(record,sort_keys=True,allow_nan=False))
    except Exception:pass  # Telemetry cannot change data, serving, or venue authority.
    return record


def emit_observation_committed(row: Mapping[str, Any], *, world_committed_at_ms: int) -> None:
    """Call only after the owning WORLD transaction commits, with its actual row."""
    try:
        ref=observation_revision_reference(row)
        emit_stage('SOURCE_COMMITTED',city=row['city'],station_id=row['station_id'],
            source_channel=row['source_channel'],observation_ref=ref,input_reference_status='EXPLICIT_LEDGER_REVISION',
            input_identity={'source':row['source_channel'],'observed_at_utc':ref['publish_ts_utc'],
                            'value_native':row['value_native']},
            response_received_at_ms=int(_utc(row['fetched_at_utc']).timestamp()*1000),
            world_committed_at_ms=world_committed_at_ms)
    except Exception:pass


def _readiness_reference(conn: Any, row: Any, posterior_id: int) -> dict[str, Any]:
    """A readiness UPSERT is not a history; preserve its exact pointer at publication."""
    try:
        matches=[]
        for ready in conn.execute('SELECT readiness_id,computed_at,dependency_json FROM readiness_state '
            "WHERE city=? AND target_local_date=? AND temperature_metric=? AND status='READY'",tuple(row[:3])):
            dependencies=json.loads(ready[2] or '{}').get('dependencies',[])
            if any(d.get('role')=='soft_anchor_posterior' and d.get('posterior_id')==posterior_id for d in dependencies):
                matches.append(ready)
        if len(matches)==1:
            return {'readiness_id':matches[0][0],'readiness_computed_at_utc':matches[0][1]}
    except Exception:pass
    return {}


def _unique_print_reference(conn: Any, *, city: str, state: Any, computed_at: str) -> tuple[Any,str]:
    """Read-only optional diagnostic, never newest-row inference or another authority.

    Only the sanctioned attached WORLD is inspected. Ambiguous A->B->A content
    remains unresolved. The true future fix is to carry the consumed row reference
    from the reader; this query cannot invent it for ambiguous historical content.
    """
    try:
        if not isinstance(state,Mapping):return None,'NO_CURRENT_TEMPERATURE_CARRIER'
        attached={r[1] for r in conn.execute('PRAGMA database_list')}
        if 'world' not in attached:return None,'WORLD_NOT_ATTACHED'
        names=('id','city','station_id','source_channel','publish_ts_utc','value_native','unit','fetched_at_utc','raw_report')
        observed=_utc(state['observed_at_utc']);cutoff=_utc(computed_at)
        rows=conn.execute('SELECT '+','.join(names)+' FROM world.observation_prints '
            'WHERE city=? AND source_channel=? AND value_native=? '
            'AND julianday(publish_ts_utc)=julianday(?) AND julianday(fetched_at_utc)<=julianday(?)',
            (city,state['source'],float(state['value_native']),observed.isoformat(),cutoff.isoformat())).fetchall()
        rows=[dict(zip(names,r)) for r in rows if _utc(r[4])==observed and _utc(r[7])<=cutoff]
        if len(rows)==1:return observation_revision_reference(rows[0]),'UNIQUE_RETAINED_LEDGER_REVISION'
        return None,'AMBIGUOUS_INPUT_REVISION' if rows else 'NO_EXACT_INPUT_REVISION'
    except Exception:return None,'INPUT_REFERENCE_UNAVAILABLE'


def emit_posterior_ready(conn: Any, posterior_id: int, *, wake_published: bool) -> None:
    try:
        row=conn.execute('SELECT city,target_date,temperature_metric,posterior_identity_hash,provenance_json,computed_at '
                         'FROM forecast_posteriors WHERE posterior_id=?',(posterior_id,)).fetchone()
        if row is None:return
        prov=json.loads(row[4] or '{}');state=prov.get('day0_current_temperature_state')
        ref,status=_unique_print_reference(conn,city=row[0],state=state,computed_at=row[5])
        emit_stage('POSTERIOR_READY',city=row[0],target_date=row[1],metric=row[2],
            posterior_id=posterior_id,posterior_identity_hash=row[3],input_identity=state,
            observation_ref=ref,input_reference_status=status,wake_published=wake_published,
            **_readiness_reference(conn,row,posterior_id))
    except Exception:pass


def emit_q_served(bundle: Any) -> None:
    try:
        emit_stage('Q_SERVED',city=bundle.city,target_date=bundle.target_date,
            metric=bundle.temperature_metric,posterior_id=bundle.posterior_id,
            posterior_identity_hash=bundle.posterior_identity_hash,q_version=bundle.posterior_identity_hash,
            input_identity=bundle.provenance_json.get('day0_current_temperature_state'))
    except Exception:pass


def emit_venue_ack(conn: Any, *, command_id: str, event_id: str, occurred_at: str) -> None:
    try:
        row=conn.execute('SELECT q_version,token_id FROM venue_commands WHERE command_id=?',(command_id,)).fetchone()
        if row is None:return
        clock=_utc(occurred_at)
        emit_stage('VENUE_ACK_OBSERVED',command_id=command_id,event_id=event_id,
            q_version=row[0],token_id=row[1],venue_ack_at_ms=int(clock.timestamp()*1000))
    except Exception:pass


def emit_wake_published(*, wake_id: str, posterior_identity_hash: str | None) -> None:
    """Publisher hook: the only wake_id -> exact posterior label, recorded at publication."""
    try:
        if wake_id and posterior_identity_hash:
            emit_stage('WAKE_PUBLISHED',wake_id=wake_id,posterior_identity_hash=posterior_identity_hash)
    except Exception:pass


_RECEIVED: dict[str, None] = {}  # Bounded per-process receipt memory; a re-poll is not a receipt.


def emit_wake_received(*, wake_id: str, posterior_identity_hash: str | None = None) -> None:
    """Consumer hook. A queue/socket publication is not a receipt by the reactor.

    Records the first take per wake_id in this process. A consumer that cannot
    know the posterior carries only wake_id; joins resolve it through that
    wake's own WAKE_PUBLISHED, never a family's latest posterior.
    """
    try:
        if not wake_id or wake_id in _RECEIVED:return
        _RECEIVED[wake_id]=None
        if len(_RECEIVED)>4096:del _RECEIVED[next(iter(_RECEIVED))]
        emit_stage('WAKE_RECEIVED',wake_id=wake_id,posterior_identity_hash=posterior_identity_hash)
    except Exception:pass


def published_wake_hashes(events: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """wake_id -> posterior hash, only where the publisher named exactly one."""
    hashes: dict[str, set] = {}
    for r in events:
        if r.get('stage')=='WAKE_PUBLISHED' and r.get('wake_id') and r.get('posterior_identity_hash'):
            hashes.setdefault(r['wake_id'],set()).add(r['posterior_identity_hash'])
    return {w:next(iter(h)) for w,h in hashes.items() if len(h)==1}


def wake_posterior_hash(record: Mapping[str, Any], published: Mapping[str, str]) -> Any:
    """The consumer's own label, else its wake's publication label."""
    return record.get('posterior_identity_hash') or published.get(record.get('wake_id'))


def _ref_key(value: Any) -> str | None:
    if isinstance(value,Mapping):value=value.get('identity')
    return value if isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) else None


def completed_trace(events: Iterable[Mapping[str, Any]], *, posterior_identity_hash: str) -> dict[str, Any]:
    """Backward-compatible stage join, refusing ambiguous observation revisions.

    OBSERVED_COMPLETE retains its historical ACK-observed meaning. It does not
    certify all hops: inspect all_hops_observed and lineage_grade. Canonical ACK
    commit is always an additional TRADE event-id check for a production audit.
    """
    rows=list(events)
    ready=[r for r in rows if r.get('stage')=='POSTERIOR_READY' and r.get('posterior_identity_hash')==posterior_identity_hash]
    if not ready:return {'status':'POSTERIOR_NOT_PROVEN'}
    parent=min(ready,key=lambda r:r['posterior_ready_at_ms'])
    ref=_ref_key(parent.get('observation_ref'))
    if not ref and (not isinstance(parent.get('input_identity'),Mapping) or not parent['input_identity']):
        return {'status':'INPUT_REVISION_NOT_PROVEN','posterior_identity_hash':posterior_identity_hash}
    if len({_ref_key(r.get('observation_ref')) for r in ready if _ref_key(r.get('observation_ref'))})>1:
        return {'status':'POSTERIOR_INPUT_REVISION_CONFLICT','posterior_identity_hash':posterior_identity_hash}
    def same_source(record):
        if record.get('stage')!='SOURCE_COMMITTED' or record.get('city')!=parent.get('city'):
            return False
        if record.get('world_committed_at_ms',2**63)>parent['posterior_ready_at_ms']:
            return False
        if not ref:return record.get('input_identity')==parent.get('input_identity')
        if _ref_key(record.get('observation_ref'))==ref:return True
        # Backward-compatible source logs can bind to a *known full revision* by
        # exact source/station/content and its millisecond-quantized receipt.
        # No latest-source inference; any two distinct source commits still fail.
        full=parent.get('observation_ref')
        try:
            return (not record.get('observation_ref') and isinstance(full,Mapping)
                and record.get('station_id')==full['station_id']
                and record.get('source_channel',record.get('input_identity',{}).get('source'))==full['source_channel']
                and record.get('input_identity')==parent.get('input_identity')
                and record.get('response_received_at_ms')==int(_utc(full['fetched_at_utc']).timestamp()*1000))
        except (KeyError,TypeError,ValueError):return False
    source=[r for r in rows if same_source(r)]
    # Deduplicate rotated/repeated log lines, not separate commits of A -> B -> A.
    unique={json.dumps({k:r.get(k) for k in ('city','station_id','source_channel','input_identity','observation_ref',
        'response_received_at_ms','world_committed_at_ms')},sort_keys=True,default=str):r for r in source}
    result={'status':'INCOMPLETE','posterior_identity_hash':posterior_identity_hash,
        'posterior_ready_at_ms':parent['posterior_ready_at_ms'],'q_served_at_ms':None,'venue_ack_at_ms':None,
        'wake_received_at_ms':None,'readiness_id':parent.get('readiness_id'),
        'lineage_grade':'EXPLICIT_REVISION' if ref else 'LEGACY_CONTENT_ONLY','all_hops_observed':False}
    if len(unique)>1:
        result['status']='AMBIGUOUS_OBSERVATION_REVISION';return result
    if not unique:return result
    src=next(iter(unique.values()))
    if not isinstance(src.get('response_received_at_ms'),int) or src['response_received_at_ms']>src['world_committed_at_ms']:
        result['status']='SOURCE_CLOCK_ORDER_VIOLATION';return result
    serves=[r for r in rows if r.get('stage')=='Q_SERVED' and r.get('posterior_identity_hash')==posterior_identity_hash
        and r.get('q_served_at_ms',0)>=parent['posterior_ready_at_ms']]
    if not serves:return result
    serve=min(serves,key=lambda r:r['q_served_at_ms'])
    result.update({k:src[k] for k in ('response_received_at_ms','world_committed_at_ms')})
    result['q_served_at_ms']=serve['q_served_at_ms']
    published=published_wake_hashes(rows)
    wakes=[r for r in rows if r.get('stage')=='WAKE_RECEIVED' and wake_posterior_hash(r,published)==posterior_identity_hash
        and r.get('wake_id') and parent['posterior_ready_at_ms']<=r.get('wake_received_at_ms',-1)<=serve['q_served_at_ms']]
    if wakes:result['wake_received_at_ms']=min(r['wake_received_at_ms'] for r in wakes)
    versions={posterior_identity_hash}
    try:
        from src.events.day0_authority import bind_day0_probability_semantics
        versions.add(bind_day0_probability_semantics(posterior_identity_hash))
    except ImportError:pass  # A stripped offline audit must not invent a derived q alias.
    acknowledgements=[r for r in rows if r.get('stage')=='VENUE_ACK_OBSERVED' and r.get('q_version') in versions
        and r.get('venue_ack_at_ms',0)>=serve['q_served_at_ms']]
    if acknowledgements:
        ack=min(acknowledgements,key=lambda r:r['venue_ack_at_ms'])
        result.update(status='OBSERVED_COMPLETE',venue_ack_at_ms=ack['venue_ack_at_ms'],command_id=ack['command_id'],event_id=ack.get('event_id'))
        result['receipt_to_ack_ms']=ack['venue_ack_at_ms']-src['response_received_at_ms']
        result['all_hops_observed']=bool(ref and parent.get('readiness_id') and wakes)
    return result
