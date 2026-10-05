from __future__ import annotations
import argparse,collections,csv,hashlib,json,math,sqlite3,subprocess
from pathlib import Path
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
PIN='1dcf9d0a28a6d5641c25ff5d8cb68730b2852238'; UTC=timezone.utc
COMMAND_STATES=frozenset('INTENT_CREATED SNAPSHOT_BOUND SIGNED_PERSISTED POSTING POST_ACKED SUBMITTING ACKED UNKNOWN SUBMIT_UNKNOWN_SIDE_EFFECT PARTIAL FILLED CANCEL_PENDING CANCELLED EXPIRED REJECTED SUBMIT_REJECTED REVIEW_REQUIRED'.split())
PHASES=frozenset('pending_entry active day0_window pending_exit economically_closed settled voided admin_closed unknown'.split())
BUCKETS=('<6h','6-24h','24-48h','48-96h','>96h','UNKNOWN_ENDPOINT','NO_SUBMIT_WITNESS')
COMMAND_SQL='''SELECT c.*,s.market_end_at AS bound_market_end_at,s.condition_id AS bound_condition_id,s.selected_outcome_token_id AS bound_token,s.yes_token_id AS bound_yes_token,s.no_token_id AS bound_no_token,s.snapshot_id AS found_snapshot_id,s.event_slug,p.position_id AS found_position_id,p.phase AS position_phase,p.city AS position_city,p.target_date AS position_target_date,p.temperature_metric AS position_metric,p.condition_id AS position_condition_id FROM venue_commands c LEFT JOIN executable_market_snapshots s ON s.snapshot_id=c.snapshot_id LEFT JOIN position_current p ON p.position_id=c.position_id ORDER BY c.command_id'''
EVENT_SQL="""SELECT command_id,event_id,sequence_no,event_type,occurred_at,state_after,CASE WHEN json_valid(payload_json) THEN json_extract(payload_json,'$.reason') END AS reason FROM venue_command_events ORDER BY command_id,sequence_no"""
TRADE_SQL='SELECT trade_fact_id,command_id,trade_id,venue_order_id,state,filled_size,observed_at,local_sequence FROM venue_trade_facts WHERE command_id IS NOT NULL'
ORDER_SQL='SELECT fact_id,command_id,venue_order_id,state,matched_size,remaining_size,observed_at,local_sequence FROM venue_order_facts WHERE command_id IS NOT NULL'
def parse_time(v):
 if v is None or str(v).strip()=='':return None
 s=str(v).strip()
 try:n=float(s)
 except ValueError:
  try:
   d=datetime.fromisoformat(s.replace('Z','+00:00'));return d.astimezone(UTC) if d.tzinfo is not None else None
  except ValueError:return None
 if not math.isfinite(n):return None
 try:return datetime.fromtimestamp(n/1000 if abs(n)>=1e11 else n,UTC)
 except (ValueError,OverflowError,OSError):return None
def iso(d):return d.isoformat() if d is not None else None
def local_end(target,tz):return datetime.combine(date.fromisoformat(target)+timedelta(days=1),time.min,tzinfo=ZoneInfo(tz)).astimezone(UTC)
def bucket(h,has_submit=True):
 if not has_submit:return 'NO_SUBMIT_WITNESS'
 if h is None or not math.isfinite(h):return 'UNKNOWN_ENDPOINT'
 return '<6h' if h<6 else '6-24h' if h<=24 else '24-48h' if h<=48 else '48-96h' if h<=96 else '>96h'
def open_ro(p):
 c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN');return c
def rows(c,sql,args=()):return [dict(r) for r in c.execute(sql,args)]
def group(rs,k):
 d=collections.defaultdict(list)
 for r in rs:d[r[k]].append(r)
 return d
def fact_key(r,k):return (parse_time(r['observed_at']) or datetime.min.replace(tzinfo=UTC),int(r.get('local_sequence') or 0),int(r.get(k) or 0))
def number(v):
 try:
  n=float(v);return n if math.isfinite(n) else None
 except (ValueError,TypeError):return None
def fill_evidence(es,ts,os):
 lt={}
 for r in ts:
  k=(r['trade_id'],r['venue_order_id'])
  if k not in lt or fact_key(r,'trade_fact_id')>fact_key(lt[k],'trade_fact_id'):lt[k]=r
 confirmed=[r for r in lt.values() if r['state']=='CONFIRMED' and (number(r['filled_size']) or 0)>0]
 pending=any(r['state'] in ('MATCHED','MINED') and (number(r['filled_size']) or 0)>0 for r in lt.values())
 fill=any(e['event_type']=='FILL_CONFIRMED' for e in es);partial=any(e['event_type']=='PARTIAL_FILL_OBSERVED' for e in es);lo={}
 for r in os:
  k=r['venue_order_id']
  if k not in lo or fact_key(r,'fact_id')>fact_key(lo[k],'fact_id'):lo[k]=r
 positive=any((number(r['matched_size']) or 0)>0 for r in os)
 zero=bool(lo) and all(r['state'] in ('CANCEL_CONFIRMED','EXPIRED','VENUE_WIPED') and number(r['matched_size'])==0 for r in lo.values())
 conflict=zero and bool(confirmed or fill or pending or positive or partial)
 kind='CONFIRMED_TRADE_FACT' if confirmed else 'COMMAND_FILL_EVENT_ONLY' if fill else 'UNCONFIRMED_FILL_OBSERVATION' if pending or partial else 'POSITIVE_ORDER_MATCH_ONLY' if positive else 'TERMINAL_ZERO_MATCH_WITNESS' if zero else 'NO_POSITIVE_FILL_EVIDENCE'
 return dict(fill_evidence=kind,confirmed_trade_count=len(confirmed),confirmed_shares=sum(number(r['filled_size']) or 0 for r in confirmed),fill_event_present=fill,partial_fill_event_present=partial,terminal_zero_match_witness=zero,fill_evidence_conflict=conflict,latest_trade_states=sorted({r['state'] for r in lt.values()}),latest_order_states=sorted({r['state'] for r in lo.values()}))
def audit(root,out,ref):
 cfgbytes=subprocess.check_output(['git','-C',str(root),'show',ref+':config/cities.json']);tzs={c['name']:c['timezone'] for c in json.loads(cfgbytes)['cities']};started=datetime.now(UTC)
 c=open_ro(root/'state/zeus_trades.db')
 try:
  cs=rows(c,COMMAND_SQL);captured=datetime.now(UTC);es=group(rows(c,EVENT_SQL),'command_id');ts=group(rows(c,TRADE_SQL),'command_id');os=group(rows(c,ORDER_SQL),'command_id');rs=rows(c,'SELECT * FROM review_work_items');ss=rows(c,'SELECT command_id,state,condition_id,token_amounts_json FROM settlement_commands');pes={}
  print('read commands/events/facts',len(cs),flush=True)
  for pid in {x['found_position_id'] for x in cs if x['found_position_id']}:
   rr=rows(c,'SELECT event_id,event_type,phase_after,sequence_no FROM position_events WHERE position_id=? ORDER BY sequence_no DESC LIMIT 1',(pid,));pes[pid]=rr[0] if rr else None
 finally:c.rollback();c.close()
 finished=datetime.now(UTC);metadata=collections.defaultdict(set);merror=None;fat=None
 try:
  fc=open_ro(root/'state/zeus-forecasts.db')
  try:
   for r in fc.execute('SELECT condition_id,token_id,city,target_date,temperature_metric FROM market_events'):metadata[(r['condition_id'],r['token_id'])].add((r['city'],r['target_date'],r['temperature_metric']))
   fat=iso(datetime.now(UTC))
  finally:fc.rollback();fc.close()
 except sqlite3.Error as exc:merror=f'{type(exc).__name__}: {exc}'
 rsub=collections.defaultdict(list);rfam=collections.defaultdict(list)
 for r in rs:
  rsub[(r['owner_domain'],r['owner_table'],r['subject_id'])].append(r)
  if r['family_city'] and r['family_target_date'] and r['family_temperature_metric']:rfam[(r['family_city'],r['family_target_date'],r['family_temperature_metric'])].append(r)
 assets=collections.defaultdict(list);invalid=[]
 for s in ss:
  try:
   toks=json.loads(s['token_amounts_json'] or '{}')
   if not isinstance(toks,dict):raise ValueError('not object')
   for tok in toks:assets[(s['condition_id'],str(tok))].append(s)
  except (ValueError,TypeError):invalid.append(s['command_id'])
 result=[]
 for c in cs:
  cid,pid=c['command_id'],c['position_id'];ee=es.get(cid,[]);reqs=[parse_time(e['occurred_at']) for e in ee if e['event_type'] in ('SUBMIT_REQUESTED','POSTING')];acks=[parse_time(e['occurred_at']) for e in ee if e['event_type'] in ('SUBMIT_ACKED','POST_ACKED')];submit=min((t for t in reqs if t is not None),default=None);ack=min((t for t in acks if t is not None),default=None);errs=[]
  if any(t is None for t in reqs+acks):errs.append('UNPARSEABLE_SUBMIT_OR_ACK_EVENT_TIME')
  if submit and ack and ack<submit:errs.append('ACK_BEFORE_SUBMIT')
  bound=bool(c['found_snapshot_id']) and str(c['token_id'])==str(c['bound_token'])
  if c['found_snapshot_id'] and not bound:errs.append('COMMAND_SNAPSHOT_TOKEN_MISMATCH')
  if not c['found_snapshot_id']:errs.append('BOUND_SNAPSHOT_MISSING')
  end=parse_time(c['bound_market_end_at']) if bound else None
  vh=(end-submit).total_seconds()/3600 if end is not None and submit is not None else None;vah=(end-ack).total_seconds()/3600 if end is not None and ack is not None else None
  city,target,metric=c['position_city'],c['position_target_date'],c['position_metric'];identity='position_current' if city and target and metric else 'UNKNOWN';mapped=metadata.get((c['bound_condition_id'],c['bound_yes_token']),set())
  if identity=='UNKNOWN' and bound and len(mapped)==1:city,target,metric=next(iter(mapped));identity='forecast.market_events_exact_condition_yes_token'
  elif len(mapped)>1:errs.append('AMBIGUOUS_TOPOLOGY_METADATA')
  elif len(mapped)==1 and identity!='UNKNOWN' and (city,target,metric) not in mapped:errs.append('POSITION_TOPOLOGY_IDENTITY_CONFLICT');identity='CONFLICT'
  endpoint=None
  if identity not in ('UNKNOWN','CONFLICT') and city in tzs:
   try:endpoint=local_end(target,tzs[city])
   except (ValueError,KeyError):errs.append('INVALID_LOCAL_DAY_IDENTITY')
  lh=(endpoint-submit).total_seconds()/3600 if endpoint is not None and submit is not None else None;lah=(endpoint-ack).total_seconds()/3600 if endpoint is not None and ack is not None else None
  fe=fill_evidence(ee,ts.get(cid,[]),os.get(cid,[]));submission='VENUE_ACKNOWLEDGED' if ack else 'FILL_WITNESSED_WITHOUT_ACK_EVENT' if fe['confirmed_trade_count'] or fe['fill_event_present'] else 'SUBMIT_REQUESTED_UNACKNOWLEDGED' if submit else 'NO_SUBMIT_WITNESS'
  direct=rsub[('trade','venue_commands',cid)]+rsub[('trade','position_current',pid)];family=rfam.get((city,target,metric),[]);tokenreviews=rsub.get(('trade','token_suppression',c['token_id']),[]);settlements=assets.get((c['bound_condition_id'] or c['position_condition_id'],str(c['token_id'])),[]);pe=pes.get(c['found_position_id'])
  if pe and pe['phase_after']!=c['position_phase']:errs.append('POSITION_PROJECTION_EVENT_PHASE_MISMATCH')
  if ee and ee[-1]['state_after']!=c['state']:errs.append('COMMAND_PROJECTION_EVENT_STATE_MISMATCH')
  if c['state'] not in COMMAND_STATES:errs.append('UNRECOGNIZED_COMMAND_STATE')
  if c['position_phase'] and c['position_phase'] not in PHASES:errs.append('UNRECOGNIZED_POSITION_PHASE')
  result.append(dict(command_id=cid,intent_kind=c['intent_kind'],state=c['state'],command_created_at=c['created_at'],command_updated_at=c['updated_at'],submit_at=iso(submit),venue_ack_at=iso(ack),submission_evidence=submission,venue_order_id=c['venue_order_id'],position_id=pid,snapshot_id=c['snapshot_id'],token_id=c['token_id'],condition_id=c['bound_condition_id'],market_end_at=iso(end),raw_market_end_at=c['bound_market_end_at'],city=city,target_date=target,temperature_metric=metric,identity_source=identity,city_timezone=tzs.get(city),local_end_exclusive=iso(endpoint),lead_venue_hours=vh,lead_local_hours=lh,lead_venue_at_ack_hours=vah,lead_local_at_ack_hours=lah,venue_bucket=bucket(vh,submit is not None),local_bucket=bucket(lh,submit is not None),submit_after_venue_endpoint=bool(vh is not None and vh<0),submit_after_local_endpoint=bool(lh is not None and lh<0),position_phase=c['position_phase'] or 'NO_POSITION_RECORD',latest_position_event=pe,command_event_types=[e['event_type'] for e in ee],command_event_reasons=[e['reason'] for e in ee if e['reason']],direct_review_states=sorted({r['status'] for r in direct}),direct_review_ids=[r['work_id'] for r in direct],open_family_review_ids=[r['work_id'] for r in family if r['status']=='OPEN'],open_token_review_ids=[r['work_id'] for r in tokenreviews if r['status']=='OPEN'],asset_settlement_states=sorted({s['state'] for s in settlements}),asset_settlement_ids=[s['command_id'] for s in settlements],evidence_flags=errs,**fe))
 assert len(result)==len(cs)==len({r['command_id'] for r in result})
 tables=[]
 for clock in ('venue','local'):
  for intent in sorted({r['intent_kind'] for r in result}):
   for b in BUCKETS:
    rr=[r for r in result if r['intent_kind']==intent and r[clock+'_bucket']==b];tables.append(dict(clock=clock,intent=intent,bucket=b,total=len(rr),states=dict(sorted(collections.Counter(r['state'] for r in rr).items())),venue_acknowledged=sum(r['submission_evidence']=='VENUE_ACKNOWLEDGED' for r in rr),confirmed_fill_fact=sum(r['confirmed_trade_count']>0 for r in rr)))
   assert sum(t['total'] for t in tables if t['clock']==clock and t['intent']==intent)==sum(r['intent_kind']==intent for r in result)
 summary=dict(source_ref=ref,configuration_sha256=hashlib.sha256(cfgbytes).hexdigest(),read_started_at=iso(started),trade_snapshot_established_at=iso(captured),trade_read_finished_at=iso(finished),forecast_metadata_read_at=fat,metadata_error=merror,snapshot_note='TRADE is one consistent read transaction; FORECAST metadata is a later sequential immutable-identity annotation, not a cross-DB atomic snapshot.',local_endpoint='exclusive midnight after target_date in pinned city timezone; 1 microsecond later than datetime.time.max',expected_grain='one row per venue_commands.command_id, both ENTRY and EXIT; no P&L allocation',commands=len(result),buckets=tables,command_counts=dict(sorted(collections.Counter(r['intent_kind']+'/'+r['state'] for r in result).items())),submission_counts=dict(sorted(collections.Counter(r['submission_evidence'] for r in result).items())),fill_evidence_counts=dict(sorted(collections.Counter(r['fill_evidence'] for r in result).items())),order_position_phase_counts=dict(sorted(collections.Counter(r['position_phase'] for r in result).items())),metadata_counts=dict(sorted(collections.Counter(r['identity_source'] for r in result).items())),evidence_flag_counts=dict(sorted(collections.Counter(f for r in result for f in r['evidence_flags']).items())),fill_evidence_conflicts=sum(r['fill_evidence_conflict'] for r in result),review_row_counts=dict(sorted(collections.Counter(r['owner_table']+'/'+r['status'] for r in rs).items())),settlement_row_counts=dict(sorted(collections.Counter(s['state'] for s in ss).items())),invalid_settlement_maps=invalid,witnesses=[r for r in result if r['command_id'] in ('0e9c7e5c66684838','acb475b4949a4730')])
 out.mkdir(parents=True,exist_ok=True);p=out/'order_lifecycles.csv'
 with p.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(result[0]) if result else ['command_id']);w.writeheader()
  for r in result:w.writerow({k:json.dumps(v,separators=(',',':')) if isinstance(v,(list,dict)) else v for k,v in r.items()})
 summary['csv_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(out/'read_only_queries.sql').write_text(COMMAND_SQL+';\n'+EVENT_SQL+';\n'+TRADE_SQL+';\n'+ORDER_SQL+';\n');return summary
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--root',type=Path,default=Path('.'));a.add_argument('--out',type=Path,required=True);a.add_argument('--source-ref',default=PIN);x=a.parse_args();s=audit(x.root.resolve(),x.out.resolve(),x.source_ref);print(json.dumps({k:v for k,v in s.items() if k!='witnesses'},separators=(',',':')))
