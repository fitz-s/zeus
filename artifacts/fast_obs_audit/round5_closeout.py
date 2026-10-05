"""Round 5 read-only closeout collector. Standard library; no network or daemon imports.

This is an audit command, not a repair or trading command. It refuses production
output paths, opens each database mode=ro/query_only, never ATTACHes, and reports
separate snapshot times. No credentials, settings, or arbitrary SQL are accepted.
Run --help for the two explicit input modes. Missing proof remains a named residual.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
CUTOFF = datetime(2026, 9, 21, tzinfo=UTC)
BASE = '8fd9248238a4590a2e6087f9a1ecf80dc09b84fd'
CONFLICT_IDS = frozenset(('086d130a613546f2', '37c227a8a0f24596',
    '37e80adb681a416b', '79e6322ae8344a92', '19a58c0a03d8416d', '5c25a7b16af841b6'))
PHASE_IDS = frozenset(('0e0ac1edba2e4619', 'cad0050955ae4cf7', '59fc7867387b4f04'))
JSON_FIELDS = frozenset(('latest_position_event', 'command_event_types', 'command_event_reasons',
    'direct_review_states', 'direct_review_ids', 'open_family_review_ids', 'open_token_review_ids',
    'asset_settlement_states', 'asset_settlement_ids', 'evidence_flags', 'latest_trade_states', 'latest_order_states'))
SECRET_KEY = re.compile(r'(?i)^(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|secret|mnemonic|signed_order)$')


def instant(value: Any, *, local_log: bool = False) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00').replace(',', '.'))
    if result.tzinfo is None:
        if not local_log:
            raise ValueError('NAIVE_UTC_INPUT')
        zone = ZoneInfo('America/Chicago')
        first, second = result.replace(tzinfo=zone, fold=0), result.replace(tzinfo=zone, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError('AMBIGUOUS_OR_NONEXISTENT_LOCAL_LOG_TIME')
        result = first
    return result.astimezone(UTC)


def millis(value: Any) -> int:
    delta = instant(value) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86400000 + delta.seconds * 1000 + delta.microseconds // 1000


def decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('NONFINITE_NUMBER')
    return result


def truth(value: Any) -> bool:
    return value is True or str(value).lower() == 'true'


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if SECRET_KEY.fullmatch(str(k)) else scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, bytes):
        return {'bytes': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
    if isinstance(value, str):
        if value[:1] in ('{', '['):
            try:
                return scrub(json.loads(value))
            except (ValueError, TypeError):
                pass
        return re.sub(r'(?i)([?&](?:api_?key|token|signature|x-amz-signature)=)[^&\s"<>]+', r'\1[REDACTED]', value)
    return value


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(scrub(value), indent=2, ensure_ascii=False, allow_nan=False) + '\n')


@contextmanager
def open_ro(path: Path):
    conn = sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=ro', uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
        conn.execute('BEGIN')
        conn.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()  # establish read snapshot
        yield conn
    finally:
        conn.rollback()
        conn.close()


def identifier(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError('INVALID_SQL_IDENTIFIER')
    return '"' + value + '"'


def columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute('PRAGMA table_info(' + identifier(table) + ')')}


def table_rows(conn, table: str, *, where: str = '', args=()) -> list[dict]:
    return [dict(row) for row in conn.execute('SELECT * FROM ' + identifier(table) + where, args)]


def observation_ref(row: dict) -> dict:
    """Use the one pure telemetry reference constructor, not a second hash law."""
    import importlib.util
    from functools import lru_cache
    return _reference_constructor()(row)


from functools import lru_cache
@lru_cache(maxsize=1)
def _reference_constructor():
    import importlib.util
    path=Path(__file__).resolve().parents[2]/'src/runtime/observation_reaction_trace.py'
    spec=importlib.util.spec_from_file_location('_round5_pure_trace',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.observation_revision_reference


def stats(values: list[float]) -> dict:
    ordered = sorted(v for v in values if math.isfinite(v) and v >= 0)
    def q(p):
        if not ordered:
            return None
        x = (len(ordered) - 1) * p
        lo = int(x); hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (x - lo)
    return {'n': len(ordered), 'p50_ms': q(.50), 'p90_ms': q(.90), 'p99_ms': q(.99),
            'method': 'linear empirical quantile; conditional on proved lineage',
            'discarded_negative_or_nonfinite': len(values) - len(ordered)}


def flags(row: dict) -> list[str]:
    value = row.get('evidence_flags', [])
    if isinstance(value, str):
        try: value = json.loads(value)
        except ValueError: return ['MALFORMED_AUDIT_FLAGS']
    return list(value or [])


def class_command(row: dict) -> str:
    if truth(row.get('fill_evidence_conflict')):
        return 'FILL_VS_TERMINAL_ZERO_CONFLICT'
    try: shares = decimal(row.get('confirmed_shares') or 0)
    except (ValueError, InvalidOperation): return 'INVALID_FILL_QUANTITY'
    state = str(row.get('state') or '')
    if shares > 0:
        order_states=row.get('latest_order_states') or []
        if isinstance(order_states,str):
            try:order_states=json.loads(order_states)
            except ValueError:order_states=[]
        terminal=bool(order_states) and set(order_states)<= {'CANCEL_CONFIRMED','EXPIRED','VENUE_WIPED'}
        return ('CONFIRMED_EXECUTION_VENUE_TERMINAL_REMAINDER' if terminal
                else 'CONFIRMED_EXECUTION_REMAINDER_REQUIRES_PROOF')
    if row.get('fill_evidence') == 'COMMAND_FILL_EVENT_ONLY':
        return 'FILL_EVENT_WITHOUT_CONFIRMED_TRADE_FACT'
    if row.get('fill_evidence') == 'TERMINAL_ZERO_MATCH_WITNESS':
        return 'VENUE_TERMINAL_NO_POSITIVE_FILL_EVIDENCE'
    if row.get('submission_evidence') == 'NO_SUBMIT_WITNESS':
        return 'NO_SUBMIT_WITNESS_NOT_PROOF_OF_NO_SIDE_EFFECT'
    return 'COMMAND_' + state + '_VENUE_OR_FILL_PROOF_INCOMPLETE'


def summarize_commands(rows: list[dict]) -> dict:
    ids = [r['command_id'] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('DUPLICATE_COMMAND_ID')
    selected = [r for r in rows if r['command_id'] in CONFLICT_IDS | PHASE_IDS or flags(r)
                or truth(r.get('fill_evidence_conflict')) or r['command_id'].startswith('adopted_exit_')]
    post, invalid = [], []
    for row in rows:
        try:
            if instant(row['command_created_at']) >= CUTOFF: post.append(row)
        except (ValueError, TypeError, KeyError): invalid.append(row['command_id'])
    return {'commands': len(rows), 'grain': 'one command, not one position',
        'command_counts': dict(Counter(r.get('intent_kind', '') + '/' + str(r.get('state')) for r in rows)),
        'evidence_classes': dict(Counter(class_command(r) for r in rows)),
        'position_phase_counts_at_command_grain': dict(Counter(r.get('position_phase', '') for r in rows)),
        'distinct_bound_positions': len({r['position_id'] for r in rows if r.get('position_id') and r.get('position_phase') != 'NO_POSITION_RECORD'}),
        'flags': dict(Counter(f for r in rows for f in flags(r))),
        'fill_evidence_conflicts': sum(truth(r.get('fill_evidence_conflict')) for r in rows),
        'flagged_rows': selected, 'post_cutoff': CUTOFF.isoformat(), 'post_cutoff_commands': len(post),
        'post_cutoff_flagged_rows': [r for r in selected if r in post],
        'post_cutoff_confirmed_fill_voided_rows': [r for r in post if r.get('position_phase') == 'voided' and decimal(r.get('confirmed_shares') or 0) > 0],
        'unparseable_command_dates': invalid,
        'cash_closure': 'REQUIRES_SCOPED_BUY_SELL_FEES_TRANSFER_REDEMPTION_LEDGER; position shares are residual, not acquired volume'}


def snapshot(repo: Path, out: Path) -> dict:
    src = repo / 'artifacts/fast_obs_audit/round5_inputs'
    with gzip.open(src / 'order_lifecycles.csv.gz', 'rt', newline='') as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for key in JSON_FIELDS:
            if row.get(key):
                try: row[key] = json.loads(row[key])
                except ValueError: pass
        row['audit_evidence_class'] = class_command(row)
    expected = json.loads((src / 'summary.json').read_text())
    if len(rows) != expected['commands']:
        raise ValueError('ARCHIVE_COUNT_MISMATCH')
    result = summarize_commands(rows)
    result['source_ref'] = expected['source_ref']
    result['snapshot_at'] = expected['trade_snapshot_established_at']
    result['execution_kind'] = 'REANALYSIS_OF_OPERATOR_COMMITTED_EXTRACT_NOT_NEW_LIVE_QUERY'
    with gzip.open(out / 'every_command.jsonl.gz', 'wt') as stream:
        for row in rows: stream.write(json.dumps(scrub(row), sort_keys=True) + '\n')
    prints = json.loads((src / 'live_route_prints_24h.json').read_text())
    roster = json.loads((repo / 'config/cities.json').read_text())['cities']
    registry = json.loads((repo / 'config/physical_current_sources.json').read_text())['sources']
    indexed = {(r['station'], r['channel']): r for r in prints}
    census = []
    for city in roster:
        station = city.get('wu_station') or ('HKO' if city['settlement_source_type'] == 'hko' else '')
        routes = []
        for route in registry:
            if route['station_id'] != station: continue
            evidence = indexed.get((station, route['source_channel']))
            routes.append({'provider': route['provider'], 'channel': route['source_channel'], 'configured_role': route['role'],
                'writer_evidence': evidence, 'disposition': 'WRITE_PRESENT' if evidence else 'NO_PRINT_IN_EXTRACT'})
        census.append({'city': city['name'], 'station': station, 'resolver': city['settlement_source_type'],
            'unit': city['unit'], 'resolver_view': city.get('settlement_page_view', 'all'),
            'routes': routes, 'all_print_channels': [r for r in prints if r['station'] == station],
            'event_transport': 'kma_amo_raw_metar' if station in ('RKSI', 'RKPK') else None})
    result['route_census'] = census
    first = min(instant(r['first']) for r in prints)
    last = max(instant(r['last']) for r in prints)
    result['print_extract_span'] = {'earliest': first.isoformat(), 'latest': last.isoformat(),
        'hours': (last - first).total_seconds() / 3600, 'declared_24h_is_demonstrated': (last-first) <= timedelta(hours=24)}
    dump(out / 'snapshot_closeout.json', result)
    return result


def latest_facts(rows: list[dict], keys: tuple[str, ...], id_field: str) -> list[dict]:
    latest = {}
    for row in rows:
        key = tuple(row.get(k) for k in keys)
        rank = (instant(row['observed_at']), int(row.get('local_sequence') or 0), int(row.get(id_field) or 0))
        if key not in latest or rank > latest[key][0]: latest[key] = (rank, row)
    return [value[1] for value in latest.values()]


def live_command_rows(conn) -> tuple[list[dict], dict]:
    required = ('venue_commands','venue_command_events','venue_trade_facts','venue_order_facts','position_current','position_events','executable_market_snapshots')
    missing = [t for t in required if not columns(conn,t)]
    if missing: raise ValueError('MISSING_REQUIRED_TABLES:' + ','.join(missing))
    commands = table_rows(conn,'venue_commands')
    positions = {r['position_id']:r for r in table_rows(conn,'position_current')}
    snapshot_fields = [k for k in ('snapshot_id','condition_id','selected_outcome_token_id') if k in columns(conn,'executable_market_snapshots')]
    bindings = {r['snapshot_id']:dict(r) for r in conn.execute('SELECT '+','.join(snapshot_fields)+' FROM executable_market_snapshots')}
    snapshots = set(bindings)
    groups = {}
    for table in ('venue_command_events','venue_trade_facts','venue_order_facts'):
        g = defaultdict(list)
        for r in table_rows(conn,table): g[r.get('command_id')].append(r)
        groups[table] = g
    pe = {}
    for pid in positions:
        r=conn.execute('SELECT event_id,event_type,phase_after,sequence_no FROM position_events WHERE position_id=? ORDER BY sequence_no DESC LIMIT 1',(pid,)).fetchone()
        if r is not None: pe[pid]=dict(r)
    result = []
    for c in commands:
        cid = c['command_id']; pos = positions.get(c['position_id']); errors = []
        tf = latest_facts(groups['venue_trade_facts'][cid], ('trade_id','venue_order_id'), 'trade_fact_id')
        of = latest_facts(groups['venue_order_facts'][cid], ('venue_order_id',), 'fact_id')
        positive = [r for r in tf if r['state']=='CONFIRMED' and decimal(r.get('filled_size') or 0)>0]
        zero = bool(of) and all(r['state'] in ('CANCEL_CONFIRMED','EXPIRED','VENUE_WIPED') and r.get('matched_size') is not None and decimal(r['matched_size'])==0 for r in of)
        ce = groups['venue_command_events'][cid]
        event_fill = any(r['event_type']=='FILL_CONFIRMED' for r in ce)
        other_positive = any(decimal(r.get('matched_size') or 0)>0 for r in groups['venue_order_facts'][cid]) or any(r['event_type']=='PARTIAL_FILL_OBSERVED' for r in ce) or any(r['state'] in ('MATCHED','MINED') and decimal(r.get('filled_size') or 0)>0 for r in tf)
        if c['snapshot_id'] not in snapshots: errors.append('BOUND_SNAPSHOT_MISSING')
        last = pe.get(c['position_id'])
        if pos and last and pos['phase'] != last['phase_after']: errors.append('POSITION_PROJECTION_EVENT_PHASE_MISMATCH')
        if pos and positive and pos['phase']=='voided': errors.append('CONFIRMED_FILL_VOIDED_POSITION')
        row = dict(c, command_created_at=c['created_at'], position_phase=pos['phase'] if pos else 'NO_POSITION_RECORD',
            confirmed_shares=str(sum((decimal(r['filled_size']) for r in positive),Decimal(0))),
            confirmed_trade_count=len(positive), fill_evidence_conflict=bool(zero and (positive or event_fill or other_positive)),
            fill_evidence='CONFIRMED_TRADE_FACT' if positive else 'COMMAND_FILL_EVENT_ONLY' if event_fill else 'TERMINAL_ZERO_MATCH_WITNESS' if zero else 'NO_POSITIVE_FILL_EVIDENCE',
            evidence_flags=errors, latest_position_event=last,
            latest_order_states=sorted({r['state'] for r in of}),latest_trade_states=sorted({r['state'] for r in tf}),
            terminal_zero_match_witness=zero,fill_event_present=event_fill)
        result.append(row)
    selected = {r['command_id'] for r in result if flags(r) or r['fill_evidence_conflict'] or r['command_id'] in CONFLICT_IDS|PHASE_IDS}
    pids = {c['position_id'] for c in commands if c['command_id'] in selected}
    related = [c for c in commands if c['position_id'] in pids]
    selected.update(c['command_id'] for c in related)
    details = {'commands': related, 'positions': [positions[p] for p in pids if p in positions],
        'position_events': table_rows(conn,'position_events',where=' WHERE position_id IN ('+','.join('?' for _ in pids)+')',args=tuple(pids)) if pids else []}
    for table, group in groups.items(): details[table] = [r for cid in selected for r in group[cid]]
    # A venue market id is not necessarily a condition id. Resolve the exact
    # bound snapshot before attributing a settlement or reporting absence.
    conditions = {str(bindings.get(r['snapshot_id'],{}).get('condition_id') or '') for r in related} - {''}
    details['bound_snapshot_identities'] = [bindings[r['snapshot_id']] for r in related if r['snapshot_id'] in bindings]
    details['settlement_binding_residuals'] = [r['command_id'] for r in related if not bindings.get(r['snapshot_id'],{}).get('condition_id')]
    for table in ('settlement_commands','review_work_items','position_lots','execution_fact','outcome_fact'):
        cols = columns(conn,table)
        if not cols:
            details[table] = {'residual':'TABLE_UNAVAILABLE'}; continue
        fields = [f for f in ('position_id','subject_id','command_id','condition_id') if f in cols]
        args, clauses = [], []
        for field in fields:
            vals = pids if field in ('position_id','subject_id') else conditions if field=='condition_id' else selected
            if vals:
                clauses.append(identifier(field)+' IN ('+','.join('?' for _ in vals)+')'); args.extend(sorted(vals))
        details[table] = table_rows(conn,table,where=' WHERE '+' OR '.join(clauses),args=tuple(args)) if clauses else {'residual':'NO_SUPPORTED_EXACT_IDENTITY_COLUMN','columns':sorted(cols)}
    return result, details


def read_logs(root: Path, start: datetime, end: datetime) -> tuple[list[dict], list[dict]]:
    events, coverage = [], []
    for path in sorted((root/'logs').glob('zeus*.log*')):
        if not path.is_file() or (path.suffix not in ('.log','.gz') and not re.search(r'\.log\.\d+$',path.name)): continue
        before = path.stat(); n=0; errors=0; last=None
        opener = gzip.open if path.suffix=='.gz' else open
        try:
            with opener(path,'rt',encoding='utf-8',errors='replace') as stream:
                for line_no,line in enumerate(stream,1):
                    if 'OBSERVATION_REACTION_TRACE ' not in line: continue
                    try:
                        obj = json.JSONDecoder().raw_decode(line.split('OBSERVATION_REACTION_TRACE ',1)[1])[0]
                        stamp = datetime.fromtimestamp(int(obj['recorded_at_ms'])/1000,UTC)
                        if start<=stamp<end:
                            obj['_log_file']=str(path.relative_to(root));obj['_log_line']=line_no
                            events.append(obj);n+=1
                        last=stamp.isoformat()
                    except (ValueError,KeyError,TypeError,OverflowError): errors+=1
        except (OSError,EOFError) as exc:
            coverage.append({'path':str(path.relative_to(root)),'error':type(exc).__name__});continue
        after=path.stat()
        coverage.append({'path':str(path.relative_to(root)),'trace_rows_in_window':n,'malformed_trace_rows':errors,
            'last_trace_time':last,'size_before':before.st_size,'size_after':after.st_size,
            'changed_during_read':(before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns)})
    return events,coverage


def reconstruct_legacy_references(conn, observations: list[dict], events: list[dict]) -> dict:
    """Resolve legacy content hints only by exact native row evidence and uniqueness.

    SOURCE matches the full source/station/value/valid instant and its exact
    millisecond-quantized receipt. POSTERIOR also searches retained earlier
    revisions, so an A->B->A sequence is ambiguous rather than silently choosing A2.
    This is not a nearest-time join. Explicit references remain preferable.
    """
    counts=Counter()
    for event in events:
        if event.get('observation_ref'):continue
        identity=event.get('input_identity')
        if not isinstance(identity,dict):continue
        try:
            city=str(event['city']);source=str(identity['source'])
            observed=instant(identity['observed_at_utc']);value=decimal(identity['value_native'])
        except (KeyError,ValueError,TypeError,InvalidOperation):
            counts['INVALID_LEGACY_INPUT_IDENTITY']+=1;continue
        if event.get('stage')=='SOURCE_COMMITTED':
            candidates=[r for r in observations if r['city']==city
                and r['station_id']==event.get('station_id') and r['source_channel']==source
                and instant(r['publish_ts_utc'])==observed and decimal(r['value_native'])==value
                and millis(r['fetched_at_utc'])==event.get('response_received_at_ms')]
        elif event.get('stage')=='POSTERIOR_READY':
            cutoff=event.get('posterior_ready_at_ms',event.get('recorded_at_ms'))
            if not isinstance(cutoff,int):counts['INVALID_READY_CLOCK']+=1;continue
            stamp=datetime.fromtimestamp(cutoff/1000,UTC).isoformat()
            candidates=table_rows(conn,'observation_prints',where=' WHERE city=? AND source_channel=? AND value_native=? AND julianday(publish_ts_utc)=julianday(?) AND julianday(fetched_at_utc)<=julianday(?)',args=(city,source,float(value),observed.isoformat(),stamp))
            candidates=[r for r in candidates if instant(r['publish_ts_utc'])==observed and decimal(r['value_native'])==value and millis(r['fetched_at_utc'])<=cutoff]
        else:continue
        unique={observation_ref(r)['identity']:r for r in candidates}
        if len(unique)==1:
            event['observation_ref']=observation_ref(next(iter(unique.values())))
            event['lineage_evidence']='RECONSTRUCTED_EXACT_UNIQUE_LEDGER_REVISION'
            counts['RECONSTRUCTED_EXACT_UNIQUE_LEDGER_REVISION']+=1
        else:
            reason='AMBIGUOUS_LEGACY_REVISION' if unique else 'NO_EXACT_LEGACY_ROW'
            event['lineage_residual']=reason;counts[reason]+=1
    return dict(counts)


def trace_distributions(observations: list[dict], events: list[dict], ack_events: list[dict]) -> dict:
    """Only explicit immutable print references license production hop statistics.

    Legacy source hints require prior exact native-row reconstruction; never use
    nearest publication time or the newest A->B->A revision retrospectively.
    """
    refs = {observation_ref(r)['identity']:r for r in observations}
    if len(refs)!=len(observations): raise ValueError('DUPLICATE_OBSERVATION_REVISION')
    records = defaultdict(list); not_referenced=0
    for event in events:
        ref = event.get('observation_ref')
        key = ref.get('identity') if isinstance(ref,dict) else ref
        if key in refs: records[key].append(event)
        else: not_referenced+=1
    ready_hash = {}
    for key, es in records.items():
        for e in es:
            if e.get('stage')=='POSTERIOR_READY' and e.get('posterior_identity_hash'):
                ready_hash.setdefault(e['posterior_identity_hash'],set()).add(key)
    # q/command/ACK can join through the exact posterior identity, not time proximity.
    for e in events:
        h=e.get('posterior_identity_hash') or e.get('q_version')
        keys=ready_hash.get(h,set())
        if len(keys)==1:
            key=next(iter(keys))
            if e not in records[key]:records[key].append(e)
    native_acks = {e['event_id']:e for e in ack_events if e.get('event_type') in ('SUBMIT_ACKED','POST_ACKED')}
    residuals=Counter(); hops=defaultdict(list); paired=[]
    for key,row in refs.items():
        es=records.get(key,[])
        source=[e for e in es if e.get('stage')=='SOURCE_COMMITTED' and e.get('world_committed_at_ms') is not None]
        ready=[e for e in es if e.get('stage')=='POSTERIOR_READY']
        if not source:residuals['MISSING_EXACT_REVISION_SOURCE_COMMIT']+=1;continue
        commits={e['world_committed_at_ms'] for e in source}
        if len(commits)!=1:residuals['CONFLICTING_SOURCE_COMMIT_CLOCKS']+=1;continue
        commit=next(iter(commits));receipt=millis(row['fetched_at_utc'])
        if commit<receipt:residuals['SOURCE_CLOCK_ORDER_VIOLATION']+=1;continue
        hops['receipt_to_world'].append(commit-receipt)
        if not ready:residuals['NO_EXACT_REVISION_POSTERIOR_READY']+=1;continue
        # A print may cause multiple families/posteriors: preserve each distinct hash.
        seen=set()
        for r in sorted(ready,key=lambda e:e.get('posterior_ready_at_ms',e['recorded_at_ms'])):
            h=r['posterior_identity_hash']
            if h in seen:continue
            seen.add(h);rt=r.get('posterior_ready_at_ms',r['recorded_at_ms'])
            if rt<commit:residuals['POSTERIOR_CLOCK_ORDER_VIOLATION']+=1;continue
            hops['world_to_posterior'].append(rt-commit)
            q=[e for e in es if e.get('stage')=='Q_SERVED' and e.get('posterior_identity_hash')==h and e.get('q_served_at_ms',-1)>=rt]
            if not q:residuals['POSTERIOR_NOT_PROVED_SERVED']+=1;continue
            serve=min(q,key=lambda e:e['q_served_at_ms']);qt=serve['q_served_at_ms'];hops['posterior_to_q'].append(qt-rt)
            wake=[e for e in es if e.get('stage')=='WAKE_RECEIVED' and e.get('wake_id') and e.get('posterior_identity_hash')==h and rt<=e.get('wake_received_at_ms',-1)<=qt]
            if wake:
                wt=min(e['wake_received_at_ms'] for e in wake)
                hops['posterior_to_wake'].append(wt-rt);hops['wake_to_q'].append(qt-wt)
            else:residuals['WAKE_RECEIPT_IDENTITY_OR_CLOCK_MISSING']+=1
            ack=[]
            for e in es:
                native=native_acks.get(e.get('event_id'))
                if e.get('stage')!='VENUE_ACK_OBSERVED' or e.get('q_version')!=h or not native:continue
                if native['command_id']!=e.get('command_id'):continue
                at=millis(native['occurred_at'])
                if at>=qt:ack.append((at,native['command_id'],native['event_id']))
            if ack:
                at,cid,eid=min(ack);hops['q_to_ack'].append(at-qt);hops['receipt_to_ack'].append(at-receipt)
                paired.append({'observation_identity':key,'posterior_identity_hash':h,'command_id':cid,'ack_event_id':eid,'wake_proven':bool(wake),'readiness_proven':bool(r.get('readiness_id'))})
            else:
                # Not an outage assertion: the lawful policy may choose KEEP/HOLD/NO_TRADE.
                residuals['Q_SERVED_NO_CANONICAL_ACK_OR_NO_ACTION_PROOF']+=1
    names=('receipt_to_world','world_to_posterior','posterior_to_wake','wake_to_q','posterior_to_q','q_to_ack','receipt_to_ack')
    return {'window_observation_rows':len(observations),'trace_rows':len(events),
        'explicit_revision_source_rows':sum(bool(records.get(k)) for k in refs),
        'events_without_direct_revision_reference':not_referenced,
        'ack_lineages':len(paired),'full_ack_chains':sum(p['wake_proven'] and p['readiness_proven'] for p in paired),'paired':paired,'residual_counts':dict(residuals),'residual_grain':'source failures per observation; downstream failures per distinct observation/posterior pair',
        'hops':{name:stats(hops[name]) for name in names},
        'pairing_key':'WORLD.observation_prints full revision reference -> posterior hash -> command q_version -> canonical ACK event_id',
        'legacy_policy':'No content-only, rounded-value-only or nearest-publish-time attribution. Typed Day0 derived q aliases require their certificate link; no arbitrary hash stripping.',
        'no_action_policy':'Missing ACK is not called an error or KEEP without a linked decision receipt.'}


def production(root: Path, out: Path, start: datetime, end: datetime) -> dict:
    stamps={};residuals=[];observations=[];ack=[];reconstruction={}
    events,coverage=read_logs(root,start,end)
    world=root/'state/zeus-world.db';trade=root/'state/zeus_trades.db';forecast=root/'state/zeus-forecasts.db'
    try:
        with open_ro(world) as conn:
            stamps['world']=datetime.now(UTC).isoformat()
            if columns(conn,'observation_prints'):
                observations=table_rows(conn,'observation_prints',where=' WHERE julianday(fetched_at_utc)>=julianday(?) AND julianday(fetched_at_utc)<julianday(?)',args=(start.isoformat(),end.isoformat()))
            else:residuals.append('WORLD_OBSERVATION_PRINTS_UNAVAILABLE')
            kma={'by_station':{'RKSI':[],'RKPK':[]},'station_identity_unresolved':[]}
            if columns(conn,'opportunity_events'):
                kr=conn.execute("""SELECT event_id,event_type,entity_key,observed_at,available_at,received_at,payload_json
                    FROM opportunity_events WHERE json_valid(payload_json)
                    AND json_extract(payload_json,'$.observation_transport')='kma_amo_raw_metar'
                    AND julianday(received_at)>=julianday(?) AND julianday(received_at)<julianday(?)""",(start.isoformat(),end.isoformat()))
                for raw in kr:
                    event=dict(raw);payload=json.loads(event['payload_json'])
                    names={str(payload.get(k) or '') for k in ('station_id','station','source_station_id','settlement_station_id')}
                    names.update(re.findall(r'\b(RKSI|RKPK)\s+\d{6}Z\b',event['payload_json']))
                    names=names & {'RKSI','RKPK'}
                    if len(names)==1:kma['by_station'][next(iter(names))].append(event)
                    else:kma['station_identity_unresolved'].append(event)
            dump(out/'kma_events.json',kma)
            if observations:reconstruction=reconstruct_legacy_references(conn,observations,events)
    except (OSError,sqlite3.Error,ValueError) as exc:residuals.append('WORLD_READ_FAILED:'+type(exc).__name__)
    try:
        with open_ro(trade) as conn:
            stamps['trade']=datetime.now(UTC).isoformat()
            rows,details=live_command_rows(conn);dump(out/'command_summary.json',summarize_commands(rows));dump(out/'flagged_evidence.json',details)
            ack=table_rows(conn,'venue_command_events',where=" WHERE event_type IN ('SUBMIT_ACKED','POST_ACKED') AND julianday(occurred_at)>=julianday(?) AND julianday(occurred_at)<julianday(?)",args=(start.isoformat(),end.isoformat()))
    except (OSError,sqlite3.Error,ValueError,KeyError) as exc:residuals.append('TRADE_READ_FAILED:'+type(exc).__name__)
    try:
        with open_ro(forecast) as conn:
            stamps['forecast']=datetime.now(UTC).isoformat()
            posts=table_rows(conn,'forecast_posteriors',where=' WHERE julianday(computed_at)>=julianday(?) AND julianday(computed_at)<julianday(?)',args=(start.isoformat(),end.isoformat()))
            keep=('posterior_id','posterior_identity_hash','city','target_date','temperature_metric','computed_at','source_available_at','provenance_json','dependency_source_run_ids_json')
            dump(out/'posterior_rows.json',[{k:p.get(k) for k in keep} for p in posts])
            readiness={}
            for name in ('readiness_state','source_readiness'):
                cs=columns(conn,name)
                if cs:
                    rr=table_rows(conn,name,where=' WHERE julianday(computed_at)>=julianday(?) AND julianday(computed_at)<julianday(?)',args=(start.isoformat(),end.isoformat())) if 'computed_at' in cs else []
                    readiness[name]={'columns':sorted(cs),'rows':rr,'history_contract':'readiness_state is an UPSERT projection; absent historical rows are not proof readiness never existed','join_rule':'dependency_json soft_anchor_posterior.posterior_id, with city/date/metric; never latest timestamp'}
            dump(out/'readiness_schema.json',readiness)
    except (OSError,sqlite3.Error,ValueError) as exc:residuals.append('FORECAST_READ_FAILED:'+type(exc).__name__)
    with gzip.open(out/'observation_rows.jsonl.gz','wt') as stream:
        for row in observations:stream.write(json.dumps(scrub(dict(row,observation_ref=observation_ref(row))))+'\n')
    dump(out/'trace_events.json',events)
    result=trace_distributions(observations,events,ack)
    result.update(window_start=start.isoformat(),window_end_exclusive=end.isoformat(),database_snapshots=stamps,
        snapshot_contract='separate read-only transactions, not a cross-database atomic snapshot',
        collection_residuals=residuals,log_coverage=coverage,legacy_reference_reconstruction=reconstruction,
        production_status='MEASURED_MATCHED_SUBSET' if result['full_ack_chains'] else 'RESIDUAL_NO_PROVED_FULL_CHAIN')
    dump(out/'production_latency.json',result)
    return result


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('snapshot','production'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--start');parser.add_argument('--end')
    args=parser.parse_args(argv);root=args.root.resolve(strict=True);out=args.out.resolve()
    if any(out==root/p or (root/p) in out.parents for p in ('state','logs','config','src')):
        parser.error('output must not be inside production state/logs/config/src')
    if args.mode=='production':
        if not args.start or not args.end:parser.error('production requires explicit --start and --end UTC offsets')
        start,end=instant(args.start),instant(args.end)
        if not start<end or end-start>timedelta(days=2):parser.error('window must be >0 and <=48 hours')
    out.mkdir(parents=True,exist_ok=False)
    result=snapshot(root,out) if args.mode=='snapshot' else production(root,out,start,end)
    print(json.dumps({'output':str(out),'mode':args.mode,'commands':result.get('commands'),
        'full_ack_chains':result.get('full_ack_chains'),'residuals':result.get('collection_residuals',[])},sort_keys=True))
    return 2 if args.mode=='production' and (result.get('collection_residuals') or not result.get('full_ack_chains')) else 0


if __name__=='__main__':
    raise SystemExit(main())
