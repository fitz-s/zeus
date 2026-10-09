"""Round 5 read-only closeout collector. Standard library; no network or daemon imports.

This is an audit command, not a repair or trading command. It refuses production
output paths, opens each database mode=ro/query_only, never ATTACHes, and reports
separate snapshot times. No credentials, settings, or arbitrary SQL are accepted.
Run --help for the two explicit input modes. Missing proof remains a named residual.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
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
    if isinstance(value, float) and not math.isfinite(value):
        return {'nonfinite_float': repr(value)}  # Named, never clamped or silently dropped.
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
    return _trace().observation_revision_reference(row)


from functools import lru_cache
@lru_cache(maxsize=1)
def _trace():
    """The pure telemetry module itself: one reference and wake-label law."""
    import importlib.util
    path=Path(__file__).resolve().parents[2]/'src/runtime/observation_reaction_trace.py'
    spec=importlib.util.spec_from_file_location('_round5_pure_trace',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _quantile(ordered: list[float], p: float):
    if not ordered:
        return None
    x = (len(ordered) - 1) * p
    lo = int(x); hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (x - lo)


def stats(values: list[float]) -> dict:
    ordered = sorted(v for v in values if math.isfinite(v) and v >= 0)
    return {'n': len(ordered), 'p50_ms': _quantile(ordered, .50), 'p90_ms': _quantile(ordered, .90),
            'p99_ms': _quantile(ordered, .99),
            'method': 'linear empirical quantile; conditional on proved lineage',
            'discarded_negative_or_nonfinite': len(values) - len(ordered)}


def signed_stats(values: list[float]) -> dict:
    """Ordering checks and ages: negatives stay in the quantiles and are counted."""
    ordered = sorted(v for v in values if math.isfinite(v))
    return {'n': len(ordered), 'negative': sum(v < 0 for v in ordered),
            'min_ms': ordered[0] if ordered else None, 'p50_ms': _quantile(ordered, .50),
            'p90_ms': _quantile(ordered, .90), 'p99_ms': _quantile(ordered, .99),
            'max_ms': ordered[-1] if ordered else None, 'nonfinite': len(values) - len(ordered),
            'method': 'signed linear empirical quantile; negatives retained and counted'}


def parsed(value: Any) -> Any:
    """A json_extract object arrives as text; anything else is already a value."""
    if isinstance(value, str):
        try: return json.loads(value)
        except ValueError: return None
    return value


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
    # Only command-bound snapshots are ever looked up; never scan the whole book history.
    bindings = {r['snapshot_id']:dict(r) for r in conn.execute('SELECT '+','.join(snapshot_fields)
        +' FROM executable_market_snapshots WHERE snapshot_id IN (SELECT snapshot_id FROM venue_commands)')}
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
    # Exact-key indexes, built on first use: identical predicates, no per-event scan.
    by_receipt=None
    retained={}
    def julian(value):
        return conn.execute('SELECT julianday(?)',(value,)).fetchone()[0]
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
            if by_receipt is None:
                by_receipt=defaultdict(list)
                for r in observations:by_receipt[(r['city'],r['station_id'],r['source_channel'])].append(r)
            # The original predicate, evaluated only over its own exact string key.
            candidates=[r for r in by_receipt.get((city,event.get('station_id'),source),())
                if instant(r['publish_ts_utc'])==observed and decimal(r['value_native'])==value
                and millis(r['fetched_at_utc'])==event.get('response_received_at_ms')]
        elif event.get('stage')=='POSTERIOR_READY':
            cutoff=event.get('posterior_ready_at_ms',event.get('recorded_at_ms'))
            if not isinstance(cutoff,int):counts['INVALID_READY_CLOCK']+=1;continue
            stamp=datetime.fromtimestamp(cutoff/1000,UTC).isoformat()
            if (city,source) not in retained:
                # One read per city/channel. SQLite computes the same julianday
                # values the per-event predicate compared; REAL equality is unchanged.
                index=defaultdict(list)
                for r in conn.execute('SELECT *,julianday(publish_ts_utc) AS _publish_jd,julianday(fetched_at_utc) AS _receipt_jd '
                        'FROM observation_prints WHERE city=? AND source_channel=?',(city,source)):
                    r=dict(r);publish_jd,receipt_jd=r.pop('_publish_jd'),r.pop('_receipt_jd')
                    if publish_jd is not None and receipt_jd is not None:
                        index[(r['value_native'],publish_jd)].append((receipt_jd,r))
                retained[(city,source)]=index
            limit=julian(stamp)
            candidates=[r for receipt_jd,r in retained[(city,source)].get((float(value),julian(observed.isoformat())),())
                if limit is not None and receipt_jd<=limit]
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


KMA_SOURCE_EVENTS = frozenset(('day0_extreme_updated_trigger', 'day0_kma_conflict'))


def _kma_key(event: dict) -> str | None:
    """A POSTERIOR_READY that names the exact KMA source event its reader consumed."""
    ref = event.get('input_ref')
    return 'kma:' + ref['kma_event_id'] if isinstance(ref, dict) and isinstance(ref.get('kma_event_id'), str) else None


DISPOSITIONS = {
    'CONSUMED_NO_EXACT_READY_EVENT': 'a sidecar posterior names this print as its input_ref, but no POSTERIOR_READY trace for it was read',
    'SUPERSEDED_BY': 'a later revision (higher rowid; rowid is commit order) of the same city/station/channel stream, '
        'ranked above this print by the Day0 reader\'s own order (reader clock, publish, receipt), was consumed for this '
        'print\'s family date by a posterior whose input cut (computed_at, the reader decision_time) is after this '
        'print\'s WORLD commit and publish clock: the reader had this print and kept the named successor',
    'OUTSIDE_SCOPE': 'no posterior recorded in [start, follow_until) for this print\'s city and family date carries an '
        'observation input (current-temperature ref/state or Day0 observation context)',
    'PENDING': 'in scope, but no posterior of the family has an input cut after this print\'s WORLD commit and publish '
        'clock by follow_until; it could still be consumed',
    'UNEXPLAINED': 'nothing above holds; later_family_inputs counts what the family\'s later readers carried, or reason '
        'names why the family date is unknown or that the highest-ranked later consumption is an earlier revision',
    'POSTERIOR_SIDECAR_UNAVAILABLE': 'posterior rows were not read or their selection is unproven'}
_METAR_GROUP = re.compile(r'\b(\d{2})(\d{2})(\d{2})Z\b')


def reader_clock(row: dict) -> datetime | None:
    """The clock the Day0 reader ranks and dates a print by; None where it rejects the print.

    Mirrors read_day0_current_temperature_state (src/data/day0_hourly_vectors.py):
    the METAR DDHHMMZ group nearest publication within [-36h, +15min], the HKO
    record clock, else publish_ts_utc.
    """
    published=instant(row['publish_ts_utc']);channel=row['source_channel']
    if channel=='aviationweather_metar':
        m=_METAR_GROUP.search(str(row.get('raw_report') or ''))
        if m is None:return None
        day,hour,minute=map(int,m.groups());near=[]
        for offset in (-2,-1,0,1):
            d=(published+timedelta(days=offset)).date()
            if d.day!=day:continue
            try:t=datetime(d.year,d.month,d.day,hour,minute,tzinfo=UTC)
            except ValueError:continue
            if published-timedelta(hours=36)<=t<=published+timedelta(minutes=15):near.append(t)
        return min(near,key=lambda t:abs(t-published)) if near else None
    if channel in ('hko_rhrread_spot','hko_current_1min_mean'):
        try:return instant(parsed(row.get('raw_report'))['recordTime' if channel=='hko_rhrread_spot' else 'observed_at_utc'])
        except (KeyError,ValueError,TypeError):return None
    return published


def print_dispositions(unmatched: list[tuple[dict, int, Any]], posteriors: list[dict] | None, prints: dict,
                       zones: dict, follow_until_ms: int | None = None) -> list[dict]:
    """Why an exact print has no POSTERIOR_READY, only as far as posterior rows prove it.

    A newer print merely existing, or an unchanged q, is never supersession: the
    successor must outrank this print and be consumed by a reader whose cut
    follows this print's commit. The witness is the stream's highest-ranked
    consumption after that cut; when it is an earlier revision, a lower-ranked
    later revision is not searched for and the print stays UNEXPLAINED.
    """
    stream=lambda r:(r['city'],r['station_id'],r['source_channel'])
    rank=lambda r,clock:(millis(clock),millis(r['publish_ts_utc']),millis(r['fetched_at_utc']))
    consumed=defaultdict(list);chains=defaultdict(list);families=defaultdict(lambda:defaultdict(list))
    for p in posteriors or ():
        ref=parsed(p.get('day0_current_temperature_input_ref'));cut=millis(p['computed_at'])
        family=(p['city'],str(p['target_date']));kind='NO_OBSERVATION_INPUT'
        if isinstance(ref,dict) and ref.get('print_id') is not None:
            pid=int(ref['print_id']);row=prints.get(pid);consumed[pid].append(p['posterior_id'])
            kind=row['source_channel'] if row else 'PRINT_REF_ROW_UNAVAILABLE'
            if row and (clock:=reader_clock(row)):chains[(*stream(row),family[1])].append((cut,rank(row,clock),pid,p))
        elif isinstance(ref,dict) and ref.get('kma_event_id'):kind='KMA_EVENT_REF'
        elif isinstance(parsed(p.get('day0_current_temperature_state')),dict):kind='CURRENT_STATE_WITHOUT_REVISION_REF'
        elif isinstance(context:=parsed(p.get('day0_observation_context')),dict):
            kind='OBSERVATION_CONTEXT_WITHOUT_REVISION_REF:'+str(context.get('source'))
        families[family][kind].append(cut)
    # Per stream and family date: consumptions by cut and each suffix's
    # highest-ranked one. Every query is a bisection, never a pairwise scan.
    index={}
    for key,chain in chains.items():
        chain.sort(key=lambda c:c[0]);best=None;suffix=[]
        for c in reversed(chain):
            best=c if best is None or c[1:3]>best[1:3] else best;suffix.append(best)
        index[key]=([c[0] for c in chain],suffix[::-1])
    for kinds in families.values():
        for cuts in kinds.values():cuts.sort()
    result=[]
    for r,commit,frontier in unmatched:
        clock=reader_clock(r);zone=zones.get(r['city'])
        day=clock.astimezone(ZoneInfo(zone)).date().isoformat() if clock and zone else None
        rec={'print_id':r['id'],'city':r['city'],'station_id':r['station_id'],'source_channel':r['source_channel'],
            'fetched_at_utc':r['fetched_at_utc'],'world_committed_at_ms':commit,'commit_disposition':frontier,'family_date':day}
        if posteriors is None:result.append(dict(rec,disposition='POSTERIOR_SIDECAR_UNAVAILABLE'));continue
        # A reader whose cut is strictly later (in ms) had this print committed and admissible by clock.
        seen=max(commit,millis(r['publish_ts_utc']));kinds=families.get((r['city'],day),{})
        later={k:n for k,cuts in kinds.items() if (n:=len(cuts)-bisect_right(cuts,seen))}
        cuts,suffix=index.get((*stream(r),day),((),()));i=bisect_right(cuts,seen)
        witness=suffix[i] if i<len(suffix) else None
        if r['id'] in consumed:rec.update(disposition='CONSUMED_NO_EXACT_READY_EVENT',consuming_posterior_ids=consumed[r['id']])
        elif day is None:rec.update(disposition='UNEXPLAINED',reason='READER_CLOCK_UNRESOLVED' if zone else 'CITY_ZONE_UNKNOWN')
        elif not any(k!='NO_OBSERVATION_INPUT' for k in kinds):rec['disposition']='OUTSIDE_SCOPE'
        elif witness and witness[1]>rank(r,clock):
            p=witness[3];rec.update(consuming_posterior_id=p['posterior_id'],consumer_input_cut=p['computed_at'],
                family=[p['city'],p['target_date'],p['temperature_metric']])
            if witness[2]>r['id']:rec.update(disposition='SUPERSEDED_BY',successor_print_id=witness[2])
            # Outranked by an earlier revision (a late, behind-frontier arrival): not a successor.
            else:rec.update(disposition='UNEXPLAINED',reason='OUTRANKED_BY_EARLIER_REVISION',outranking_print_id=witness[2])
        elif not later:
            rec.update(disposition='PENDING',age_at_follow_until_ms=None if follow_until_ms is None else follow_until_ms-commit)
        else:rec.update(disposition='UNEXPLAINED',later_family_inputs=later)
        result.append(rec)
    return result


HOP_DEFINITIONS = {
    'receipt_to_world': 'print fetched_at -> its SOURCE_COMMITTED WORLD commit clock',
    'world_to_posterior': 'WORLD commit -> POSTERIOR_READY, per distinct observation/posterior lineage',
    'kma_available_to_received': 'KMA event available_at -> received_at (post-HTTP tick cut)',
    'kma_available_to_posterior': 'KMA available_at -> POSTERIOR_READY naming that event',
    'posterior_to_wake': 'legacy key; the same samples as posterior_to_wake_before_first_q',
    'posterior_to_wake_before_first_q': 'POSTERIOR_READY -> labelled WAKE_RECEIVED, counted only when ready <= wake <= first q served',
    'wake_to_q': 'that conditional wake receipt -> first q served',
    'posterior_to_q': 'POSTERIOR_READY -> first Q_SERVED of the same hash at or after ready',
    'receipt_to_first_valid_q': 'input receipt (print fetched_at, or KMA available_at) -> that first valid Q_SERVED, '
        'per lineage, measured directly, not a sum of components',
    'q_to_ack': 'first valid Q_SERVED -> canonical TRADE ACK of a command carrying that q_version',
    'receipt_to_ack': 'input receipt (print fetched_at, or KMA available_at) -> that canonical ACK',
    'wake_published_to_received': 'WAKE_PUBLISHED -> first WAKE_RECEIVED of the same wake_id regardless of q timing; '
        'cohort = wakes published before the cohort end. WAKE_RECEIVED is emitted only on the reactor poll path '
        '(src/main.py); a wake acknowledged by retire_served_wakes (src/runtime/reactor_wake.py) leaves no receipt, '
        'so a censored wake is not proven lost'}
CENSORABLE = ('receipt_to_world', 'world_to_posterior', 'kma_available_to_posterior', 'posterior_to_q',
              'receipt_to_first_valid_q', 'q_to_ack', 'receipt_to_ack', 'wake_published_to_received')


def trace_distributions(observations: list[dict], events: list[dict], ack_events: list[dict],
                        kma_events: list[dict] = (), *, cohort_end_ms: int | None = None,
                        follow_until_ms: int | None = None, posteriors: list[dict] | None = None,
                        prints: list[dict] = (), zones: dict | None = None) -> dict:
    """Only explicit immutable print references license production hop statistics.

    Legacy source hints require prior exact native-row reconstruction; never use
    nearest publication time or the newest A->B->A revision retrospectively.
    KMA inputs are opportunity_events, joined only by the exact event id the
    reader recorded. Their receipt clock is the event's available_at (the
    report's LOCAL_FIRST_SEEN_AFTER_COMPLETE_RESPONSE possession clock);
    received_at is the tick's post-HTTP cut, reported separately, never a
    WORLD commit clock.

    The caller selects the source cohort. Downstream events and ACKs count
    through follow_until_ms; a hop whose start was observed but whose end was
    not by then is CENSORED with its outstanding age, never dropped.
    """
    if follow_until_ms is not None:
        events=[e for e in events if e.get('recorded_at_ms',-1)<follow_until_ms]
    refs = {observation_ref(r)['identity']:r for r in observations}
    if len(refs)!=len(observations): raise ValueError('DUPLICATE_OBSERVATION_REVISION')
    kma = {'kma:'+e['event_id']:e for e in kma_events if e.get('source') in KMA_SOURCE_EVENTS}
    records = defaultdict(list); not_referenced=0
    for event in events:
        ref = event.get('observation_ref')
        key = ref.get('identity') if isinstance(ref,dict) else ref
        if key in refs: records[key].append(event)
        elif event.get('stage')=='POSTERIOR_READY' and _kma_key(event) in kma: records[_kma_key(event)].append(event)
        else: not_referenced+=1
    ready_hash = {}
    for key, es in records.items():
        for e in es:
            if e.get('stage')=='POSTERIOR_READY' and e.get('posterior_identity_hash'):
                ready_hash.setdefault(e['posterior_identity_hash'],set()).add(key)
    # A consumer wake carries only its wake_id; its label is that wake's own
    # publication record, never a family's latest posterior.
    published=_trace().published_wake_hashes(events)
    def label(e):
        return _trace().wake_posterior_hash(e,published) if e.get('stage')=='WAKE_RECEIVED' else e.get('posterior_identity_hash')
    # q/command/ACK can join through the exact posterior identity, not time proximity.
    # Membership keeps list `in` (==) semantics; equal records share these scalars,
    # so bucketing by them only removes comparisons that could never match.
    def signature(e):
        value=tuple(e.get(k) for k in ('stage','recorded_at_ms','trace_event_id','_log_file','_log_line'))
        try:hash(value);return value
        except TypeError:return None
    members={}
    for key,es in records.items():
        buckets=members[key]=defaultdict(list)
        for e in es:buckets[signature(e)].append(e)
    for e in events:
        h=label(e) or e.get('q_version')
        keys=ready_hash.get(h,set())
        if len(keys)==1:
            key=next(iter(keys));bucket=members[key][signature(e)]
            if e not in bucket:bucket.append(e);records[key].append(e)
    native_acks = {e['event_id']:e for e in ack_events if e.get('event_type') in ('SUBMIT_ACKED','POST_ACKED')}
    residuals=Counter(); hops=defaultdict(list); paired=[]
    since=defaultdict(list)  # hop -> start clocks whose end was not observed: the censored set
    unbound=[]  # receipt clocks of source revisions that reached no lineage by follow_until
    unmatched=[]; fan={'WORLD_PRINT':Counter(),'KMA_EVENT':Counter()}
    by_hash={p['posterior_identity_hash']:p for p in posteriors or () if p.get('posterior_identity_hash')}
    order=[]; rowless=0
    for key,row in (*refs.items(),*kma.items()):
        es=records.get(key,[]);kind='KMA_EVENT' if key in kma else 'WORLD_PRINT'
        source=[e for e in es if e.get('stage')=='SOURCE_COMMITTED' and e.get('world_committed_at_ms') is not None]
        ready=[e for e in es if e.get('stage')=='POSTERIOR_READY']
        # Per-key stage indexes keep each posterior's lookups exact and O(1).
        served,woken,acked=defaultdict(list),defaultdict(list),defaultdict(list)
        for e in es:
            stage=e.get('stage')
            if stage=='Q_SERVED':served[e.get('posterior_identity_hash')].append(e)
            elif stage=='WAKE_RECEIVED' and e.get('wake_id'):woken[label(e)].append(e)
            elif stage=='VENUE_ACK_OBSERVED':acked[e.get('q_version')].append(e)
        if key in kma:
            try:receipt=millis(row['available_at']);received=millis(row['received_at'])
            except (KeyError,TypeError,ValueError):residuals['KMA_EVENT_CLOCK_INVALID']+=1;continue
            if received<receipt:residuals['KMA_EVENT_CLOCK_ORDER_VIOLATION']+=1;continue
            hops['kma_available_to_received'].append(received-receipt)
            commit=receipt  # Lower bound for each posterior; no WORLD commit clock is claimed.
            if not ready:
                residuals['KMA_EVENT_NO_EXACT_POSTERIOR_READY']+=1;since['kma_available_to_posterior'].append(receipt);unbound.append(receipt);continue
        else:
            receipt=millis(row['fetched_at_utc'])
            if not source:
                residuals['MISSING_EXACT_REVISION_SOURCE_COMMIT']+=1;since['receipt_to_world'].append(receipt);unbound.append(receipt);continue
            commits={e['world_committed_at_ms'] for e in source}
            if len(commits)!=1:residuals['CONFLICTING_SOURCE_COMMIT_CLOCKS']+=1;continue
            commit=next(iter(commits))
            if commit<receipt:residuals['SOURCE_CLOCK_ORDER_VIOLATION']+=1;continue
            hops['receipt_to_world'].append(commit-receipt)
            if not ready:
                # Only "no exact ready event found"; print_dispositions says what the data proves beyond that.
                residuals['NO_EXACT_REVISION_POSTERIOR_READY']+=1;since['world_to_posterior'].append(commit);unbound.append(receipt)
                unmatched.append((row,commit,source[0].get('commit_disposition')));continue
        # A print may cause multiple families/posteriors: preserve each distinct hash.
        seen=set()
        for r in sorted(ready,key=lambda e:e.get('posterior_ready_at_ms',e['recorded_at_ms'])):
            h=r['posterior_identity_hash']
            if h in seen:continue
            seen.add(h);rt=r.get('posterior_ready_at_ms',r['recorded_at_ms']);fan[kind][key]+=1
            if kind=='WORLD_PRINT' and posteriors is not None:
                # Every lineage, before any clock filter: ordering is counted, not assumed.
                if h in by_hash:order.append((receipt,commit,millis(by_hash[h]['computed_at']),rt))
                else:rowless+=1
            if rt<commit:residuals['POSTERIOR_CLOCK_ORDER_VIOLATION']+=1;continue
            hops['kma_available_to_posterior' if key in kma else 'world_to_posterior'].append(rt-commit)
            q=[e for e in served.get(h,()) if e.get('q_served_at_ms',-1)>=rt]
            if not q:
                residuals['POSTERIOR_NOT_PROVED_SERVED']+=1
                for hop,t in (('posterior_to_q',rt),('receipt_to_first_valid_q',receipt),('receipt_to_ack',receipt)):since[hop].append(t)
                continue
            serve=min(q,key=lambda e:e['q_served_at_ms']);qt=serve['q_served_at_ms'];hops['posterior_to_q'].append(qt-rt)
            hops['receipt_to_first_valid_q'].append(qt-receipt)
            wake=[e for e in woken.get(h,()) if rt<=e.get('wake_received_at_ms',-1)<=qt]
            if wake:
                wt=min(e['wake_received_at_ms'] for e in wake)
                for hop in ('posterior_to_wake','posterior_to_wake_before_first_q'):hops[hop].append(wt-rt)
                hops['wake_to_q'].append(qt-wt)
            else:residuals['WAKE_RECEIPT_IDENTITY_OR_CLOCK_MISSING']+=1
            ack=[]
            for e in acked.get(h,()):
                native=native_acks.get(e.get('event_id'))
                if not native:continue
                if native['command_id']!=e.get('command_id'):continue
                at=millis(native['occurred_at'])
                if at>=qt and (follow_until_ms is None or at<follow_until_ms):ack.append((at,native['command_id'],native['event_id']))
            if ack:
                at,cid,eid=min(ack);hops['q_to_ack'].append(at-qt);hops['receipt_to_ack'].append(at-receipt)
                paired.append({'observation_identity':key,'input_kind':kind,'posterior_identity_hash':h,'command_id':cid,'ack_event_id':eid,'wake_proven':bool(wake),'readiness_proven':bool(r.get('readiness_id'))})
            else:
                # Not an outage assertion: the lawful policy may choose KEEP/HOLD/NO_TRADE.
                residuals['Q_SERVED_NO_CANONICAL_ACK_OR_NO_ACTION_PROOF']+=1
                since['q_to_ack'].append(qt);since['receipt_to_ack'].append(receipt)
    # Wake transport, unconditional on q timing: publication joined to receipt by wake_id alone.
    sent,got={},{}
    for e in events:
        stage,w=e.get('stage'),e.get('wake_id')
        clock=e.get('wake_published_at_ms' if stage=='WAKE_PUBLISHED' else 'wake_received_at_ms',e.get('recorded_at_ms'))
        if not w or not isinstance(clock,int):continue
        if stage=='WAKE_PUBLISHED':sent[w]=min(sent.get(w,clock),clock)
        elif stage=='WAKE_RECEIVED':got[w]=min(got.get(w,clock),clock)
    cohort={w:t for w,t in sent.items() if cohort_end_ms is None or t<cohort_end_ms}
    for w,t in cohort.items():
        if w in got:hops['wake_published_to_received'].append(got[w]-t)
        else:since['wake_published_to_received'].append(t)
    names=('receipt_to_world','world_to_posterior','kma_available_to_received','kma_available_to_posterior',
        'posterior_to_wake','wake_to_q','posterior_to_q','q_to_ack','receipt_to_ack',
        'posterior_to_wake_before_first_q','receipt_to_first_valid_q','wake_published_to_received')
    witnessed={e.get('event_id') for e in events if e.get('stage')=='VENUE_ACK_OBSERVED'}
    disposed=print_dispositions(unmatched,posteriors,{r['id']:r for r in (*prints,*observations)},zones or {},follow_until_ms)
    if posteriors is None:decomposition={'status':'POSTERIOR_SIDECAR_UNAVAILABLE'}
    else:
        decomposition={'grain':'WORLD_PRINT lineages','lineages':len(order)+rowless,'lineages_without_posterior_row':rowless,
            'world_commit_to_effective_input_cut':signed_stats([cut-commit for _,commit,cut,_ in order]),
            'effective_input_cut_to_ready':signed_stats([rt-cut for *_,cut,rt in order]),
            'ordering_violations':{'cut_before_receipt':sum(cut<rc for rc,_,cut,_ in order),
                'cut_before_world_commit':sum(cut<cm for _,cm,cut,_ in order),'ready_before_cut':sum(rt<cut for *_,cut,rt in order)},
            'clock_contract':'computed_at is the effective input cut: the request stamp lifted to the latest role possession '
                'and anchor recording (src/data/replacement_forecast_materializer.py _request_with_materialization_clock), '
                'and the Day0 reader decision_time. It is not a dequeue, queue-wait or compute-start clock.',
            'sum_contract':'Separate marginal distributions over every lineage, negatives retained; component percentiles '
                'do not sum to world_to_posterior or receipt_to_first_valid_q.'}
    return {'window_observation_rows':len(observations),'trace_rows':len(events),
        'kma_source_events':len(kma),'kma_events_with_exact_posterior':sum(bool(records.get(k)) for k in kma),
        'kma_clock_contract':'receipt=available_at (report first-seen possession); received_at=post-HTTP tick cut; no KMA WORLD commit clock',
        'explicit_revision_source_rows':sum(bool(records.get(k)) for k in refs),
        'events_without_direct_revision_reference':not_referenced,
        'ack_lineages':len(paired),'full_ack_chains':sum(p['wake_proven'] and p['readiness_proven'] for p in paired),'paired':paired,'residual_counts':dict(residuals),'residual_grain':'source failures per observation; downstream failures per distinct observation/posterior pair',
        'hops':{name:stats(hops[name]) for name in names},'hop_definitions':HOP_DEFINITIONS,
        'censored':{name:{'n':len(since[name]),'outstanding_age_at_follow_until':
            None if follow_until_ms is None else signed_stats([follow_until_ms-t for t in since[name]])} for name in CENSORABLE},
        'censored_source_revisions_without_lineage':{'n':len(unbound),'outstanding_age_from_receipt_at_follow_until':
            None if follow_until_ms is None else signed_stats([follow_until_ms-t for t in unbound])},
        'censoring_contract':'Start observed, end not observed before follow_until, at the hop sample grain. receipt_to_* '
            'hops are per lineage; a source revision (print or KMA event) that reached no lineage is censored once, at '
            'source grain, in censored_source_revisions_without_lineage, aged from its receipt. Clock-order residuals '
            '(residual_counts) are neither sampled nor censored. A q_to_ack/receipt_to_ack completion needs both the '
            'canonical ACK occurred_at and its VENUE_ACK_OBSERVED trace before follow_until (ack_trace_witness). Censored '
            'is not a failure: print_dispositions classifies the world_to_posterior set; no-action decisions are unproven.',
        'ack_trace_witness':{'canonical_acks_read':len(native_acks),'with_venue_ack_observed_trace':sum(i in witnessed for i in native_acks)},
        'fan_out':{k:{'source_revisions_with_lineage':len(c),'posterior_lineages':sum(c.values()),
            'lineages_per_source_revision':{str(n):m for n,m in sorted(Counter(c.values()).items())}} for k,c in fan.items()},
        'fan_out_grain':'lineage = distinct (source revision, posterior_identity_hash) with an exact POSTERIOR_READY',
        'wake_transport':{'published_in_cohort':len(cohort),'received':len(got),'intersect':sum(w in got for w in cohort),
            'published_after_cohort_end':len(sent)-len(cohort),'received_without_read_publication':len(got.keys()-sent.keys())},
        'print_disposition_counts':dict(Counter(r['disposition'] for r in disposed)),'print_dispositions':disposed,
        'print_disposition_definitions':DISPOSITIONS,
        'print_disposition_contract':'One record per NO_EXACT_REVISION_POSTERIOR_READY print. That residual means only that no '
            'exact POSTERIOR_READY event was found; it never implies harmless supersession. family_date is the city-local '
            'date of the clock the Day0 reader dates the print by: the METAR DDHHMMZ group, the HKO record clock, else '
            'publish_ts_utc. Family scope is city and date, not metric.',
        'computed_at_decomposition':decomposition,
        'pairing_key':'WORLD.observation_prints full revision reference -> posterior hash -> command q_version -> canonical ACK event_id',
        'legacy_policy':'No content-only, rounded-value-only or nearest-publish-time attribution. Typed Day0 derived q aliases require their certificate link; no arbitrary hash stripping.',
        'no_action_policy':'Missing ACK is not called an error or KEEP without a linked decision receipt.'}


def first_posterior_at(conn, stamp: datetime) -> int:
    """Least posterior_id recorded at or after stamp, by bisection on the rowid.

    recorded_at follows the multi-MB provenance column, so a scan of it walks
    every overflow chain. Premise: one INSERT site with AUTOINCREMENT id and
    DEFAULT CURRENT_TIMESTAMP under SQLite's single writer makes recorded_at
    non-decreasing in posterior_id unless the wall clock steps back. The
    caller checks it on the selection and its two neighbours.
    """
    lo,hi=conn.execute('SELECT min(posterior_id),max(posterior_id)+1 FROM forecast_posteriors').fetchone()
    if lo is None:return 0
    while lo<hi:
        mid=(lo+hi)//2
        row=conn.execute('SELECT posterior_id,julianday(recorded_at)>=julianday(?) FROM forecast_posteriors '
            'WHERE posterior_id>=? ORDER BY posterior_id LIMIT 1',(stamp.isoformat(),mid)).fetchone()
        if row[1]:hi=mid
        else:lo=row[0]+1
    return lo


def start_backlog(queue: Path, start: datetime) -> dict:
    """What request filenames and latest receipts can prove about work pending at start."""
    facts={'requests_present_at_collection':Counter()}
    try:
        for entry in (queue/'requests').iterdir():
            m=re.search(r'\.(\d{8}T\d{6}Z)\.',entry.name)
            facts['requests_present_at_collection']['HIDDEN_STAGING' if entry.name.startswith('.') else 'UNSTAMPED' if m is None
                else 'STAMPED_BEFORE_START' if datetime.strptime(m[1],'%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)<start
                else 'STAMPED_AT_OR_AFTER_START']+=1
        for name in ('succeeded_latest','superseded_latest'):
            seen=facts[name]=Counter()
            for entry in (queue/name).glob('*.json'):
                try:body=json.loads(entry.read_text());made,done=instant(body['computed_at']),instant(body['recorded_at'])
                except (OSError,ValueError,KeyError,TypeError):seen['NO_READABLE_CLOCKS']+=1;continue
                seen['REQUEST_COMPUTED_BEFORE_START_TERMINAL_AT_OR_AFTER_START' if made<start<=done
                    else 'TERMINAL_BEFORE_START' if done<start else 'REQUEST_COMPUTED_AT_OR_AFTER_START']+=1
    except OSError as exc:facts['read_error']=type(exc).__name__
    return {'status':'BACKLOG_COHORT_NOT_RECONSTRUCTABLE',
        'reasons':['The filename stamp is the seed computed_at, not the instant the file entered requests/; presence in '
                'requests/ at start is not provable from it.',
            'Terminal requests are unlinked and succeeded_latest/superseded_latest keep one overwritten receipt per family, '
                'so requests that finished between start and collection survive only as each family\'s latest receipt.'],
        'observable_facts':{k:dict(v) if isinstance(v,Counter) else v for k,v in facts.items()},
        'facts_contract':'File counts at collection time, not a cohort: no queue wait, age or backlog size is derived from them.'}


def production(root: Path, out: Path, start: datetime, end: datetime, follow_until: datetime | None = None) -> dict:
    """Source cohort [start, end); every downstream read runs through follow_until."""
    follow_until=end+timedelta(hours=2) if follow_until is None else follow_until
    stamps={};residuals=[];observations=[];ack=[];reconstruction={};kma={};posts=None;prints=[];decisions={}
    events,coverage=read_logs(root,start,follow_until)
    try:
        roster=json.loads((root/'config/cities.json').read_text())['cities']
        # KMA transport is written only on DAY0_EXTREME_UPDATED for the RKSI/RKPK
        # cities (src/data/day0_fast_obs.py); this scope rides the indexed city key.
        kma_cities=tuple(c['name'] for c in roster if c.get('wu_station') in ('RKSI','RKPK'))
        zones={c['name']:c['timezone'] for c in roster if c.get('timezone')}
    except (OSError,ValueError,KeyError,TypeError):kma_cities=();zones={}
    if not kma_cities:residuals.append('KMA_CITY_SCOPE_UNRESOLVED')
    world=root/'state/zeus-world.db';trade=root/'state/zeus_trades.db';forecast=root/'state/zeus-forecasts.db'
    try:
        with open_ro(world) as conn:
            stamps['world']=datetime.now(UTC).isoformat()
            if columns(conn,'observation_prints'):
                # asos5_* (5-min whole-degree ASOS rows) is ingested but consumed by no
                # probability law yet; it enters the revision cohort once the dense law admits it.
                observations=table_rows(conn,'observation_prints',where=" WHERE julianday(fetched_at_utc)>=julianday(?) AND julianday(fetched_at_utc)<julianday(?) AND source_channel NOT LIKE 'asos5\\_%' ESCAPE '\\'",args=(start.isoformat(),end.isoformat()))
            else:residuals.append('WORLD_OBSERVATION_PRINTS_UNAVAILABLE')
            kma={'by_station':{'RKSI':[],'RKPK':[]},'station_identity_unresolved':[]}
            if columns(conn,'opportunity_events'):
                kr=conn.execute("""SELECT event_id,event_type,source,entity_key,observed_at,available_at,received_at,payload_json
                    FROM opportunity_events WHERE event_type='DAY0_EXTREME_UPDATED'
                    AND json_extract(payload_json,'$.city') IN ("""+','.join('?'*len(kma_cities))+""")
                    AND julianday(received_at)>=julianday(?) AND julianday(received_at)<julianday(?)
                    AND json_valid(payload_json)
                    AND json_extract(payload_json,'$.observation_transport')='kma_amo_raw_metar'""",(*kma_cities,start.isoformat(),end.isoformat()))
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
            # Latency ACK proof first: an evidence-dump refusal must not erase it.
            ack=table_rows(conn,'venue_command_events',where=" WHERE event_type IN ('SUBMIT_ACKED','POST_ACKED') AND julianday(occurred_at)>=julianday(?) AND julianday(occurred_at)<julianday(?)",args=(start.isoformat(),follow_until.isoformat()))
            # Whether any order was created at all: zero submissions make ACK latency inapplicable, not 0 ms.
            spans={'cohort':(start.isoformat(),end.isoformat()),'follow_up':(end.isoformat(),follow_until.isoformat())}
            created={k:Counter(r[0] for r in conn.execute('SELECT intent_kind FROM venue_commands '
                'WHERE julianday(created_at)>=julianday(?) AND julianday(created_at)<julianday(?)',span)) for k,span in spans.items()}
            decisions={'venue_commands_created':{k:{'n':sum(c.values()),'by_intent':dict(c)} for k,c in created.items()},
                'venue_commands_created_at_unparseable':conn.execute('SELECT count(*) FROM venue_commands WHERE julianday(created_at) IS NULL').fetchone()[0],
                'canonical_acks':{k:conn.execute("SELECT count(*) FROM venue_command_events WHERE event_type IN ('SUBMIT_ACKED','POST_ACKED') "
                    'AND julianday(occurred_at)>=julianday(?) AND julianday(occurred_at)<julianday(?)',span).fetchone()[0] for k,span in spans.items()}}
            rows,details=live_command_rows(conn);dump(out/'command_summary.json',summarize_commands(rows));dump(out/'flagged_evidence.json',details)
    except (OSError,sqlite3.Error,ValueError,KeyError) as exc:residuals.append('TRADE_READ_FAILED:'+type(exc).__name__)
    try:
        with open_ro(forecast) as conn:
            stamps['forecast']=datetime.now(UTC).isoformat()
            # Actual publication (recorded_at), not computed_at, which is lifted to input possession.
            lo,hi=first_posterior_at(conn,start),first_posterior_at(conn,follow_until)
            # Only the trace-joined provenance keys; the full multi-GB provenance is not lineage.
            # Ordered by id, so the selection itself is the cheap witness of the bisection premise.
            posts=[dict(r) for r in conn.execute("""SELECT posterior_id,posterior_identity_hash,city,target_date,
                temperature_metric,computed_at,recorded_at,julianday(recorded_at) AS recorded_jd,source_available_at,
                json_extract(provenance_json,'$.day0_current_temperature_state') AS day0_current_temperature_state,
                json_extract(provenance_json,'$.day0_current_temperature_input_ref') AS day0_current_temperature_input_ref,
                json_extract(provenance_json,'$.day0_causal_evidence_bundle.observation_context') AS day0_observation_context,
                dependency_source_run_ids_json FROM forecast_posteriors WHERE posterior_id>=? AND posterior_id<?
                ORDER BY posterior_id""",(lo,hi))]
            before=conn.execute('SELECT julianday(recorded_at) FROM forecast_posteriors WHERE posterior_id<? ORDER BY posterior_id DESC LIMIT 1',(lo,)).fetchone()
            after=conn.execute('SELECT julianday(recorded_at) FROM forecast_posteriors WHERE posterior_id>=? ORDER BY posterior_id LIMIT 1',(hi,)).fetchone()
            first,last=conn.execute('SELECT julianday(?),julianday(?)',(start.isoformat(),follow_until.isoformat())).fetchone()
            clocks=[p.pop('recorded_jd') for p in posts]
            dump(out/'posterior_rows.json',posts)
            if (any(b<a for a,b in zip(clocks,clocks[1:])) or any(not first<=c<last for c in clocks)
                    or (before and before[0]>=first) or (after and after[0]<last)):
                # An unproven selection classifies nothing; the rows stay on disk as evidence.
                residuals.append('POSTERIOR_RECORDED_AT_SELECTION_PREMISE_FAILED');posts=None
            readiness={}
            for name in ('readiness_state','source_readiness'):
                cs=columns(conn,name)
                if cs:
                    rr=table_rows(conn,name,where=' WHERE julianday(computed_at)>=julianday(?) AND julianday(computed_at)<julianday(?)',args=(start.isoformat(),follow_until.isoformat())) if 'computed_at' in cs else []
                    readiness[name]={'columns':sorted(cs),'rows':rr,'history_contract':'readiness_state is an UPSERT projection; absent historical rows are not proof readiness never existed','join_rule':'dependency_json soft_anchor_posterior.posterior_id, with city/date/metric; never latest timestamp'}
            dump(out/'readiness_schema.json',readiness)
    except (OSError,sqlite3.Error,ValueError) as exc:residuals.append('FORECAST_READ_FAILED:'+type(exc).__name__)
    # Consumed prints outside the cohort (a successor received during follow-up): primary-key seeks only.
    have={r['id'] for r in observations}
    wanted=sorted({int(ref['print_id']) for p in posts or () if isinstance(ref:=parsed(p.get('day0_current_temperature_input_ref')),dict)
        and ref.get('print_id') is not None}-have)
    if wanted:
        try:
            with open_ro(world) as conn:
                stamps['world_consumed_prints']=datetime.now(UTC).isoformat()
                prints=table_rows(conn,'observation_prints',where=' WHERE id IN ('+','.join('?'*len(wanted))+')',args=tuple(wanted))
        except (OSError,sqlite3.Error,ValueError) as exc:residuals.append('WORLD_CONSUMED_PRINT_READ_FAILED:'+type(exc).__name__)
    if datetime.now(UTC)<follow_until:residuals.append('FOLLOW_UNTIL_NOT_ELAPSED_AT_COLLECTION')
    with gzip.open(out/'observation_rows.jsonl.gz','wt') as stream:
        for row in observations:stream.write(json.dumps(scrub(dict(row,observation_ref=observation_ref(row))))+'\n')
    dump(out/'trace_events.json',events)
    kma_rows=[e for rows in kma.get('by_station',{}).values() for e in rows]+kma.get('station_identity_unresolved',[])
    result=trace_distributions(observations,events,ack,kma_rows,cohort_end_ms=millis(end),follow_until_ms=millis(follow_until),
        posteriors=posts,prints=prints,zones=zones)
    if decisions:
        decisions['ack_latency']=('NOT_APPLICABLE_ZERO_VENUE_COMMANDS_CREATED'
            if not any(v['n'] for v in decisions['venue_commands_created'].values())
            else 'MEASURED' if result['hops']['q_to_ack']['n'] else 'COMMANDS_CREATED_WITHOUT_TRACE_LINKED_CANONICAL_ACK')
        decisions['no_action_decisions']='UNPROVEN: no linked decision receipt is read; zero commands is not proof that each served q was evaluated and declined'
    else:decisions={'status':'TRADE_READ_FAILED'}
    decisions['spans']={'cohort':[start.isoformat(),end.isoformat()],'follow_up':[end.isoformat(),follow_until.isoformat()]}
    result.update(window_start=start.isoformat(),window_end_exclusive=end.isoformat(),follow_until_exclusive=follow_until.isoformat(),
        cohort_contract='Source cohort: prints by fetched_at and KMA events by received_at in [start, end). Trace events, '
            'canonical ACKs, readiness rows and posterior rows (by recorded_at) through follow_until. Unfinished work is censored.',
        posterior_selection={'by':'recorded_at','posterior_id_range':None if posts is None else [lo,hi],'rows':None if posts is None else len(posts),
            'completeness':'Rows found by bisection on posterior_id. Checked: every selected recorded_at is inside the window '
                'and non-decreasing, and both neighbouring rows are outside. Not checked: a row outside the id range with '
                'recorded_at inside the window, which needs a backward wall-clock step at insert; a full check reads every '
                'provenance overflow chain (minutes).'},
        decision_evidence=decisions,start_backlog_cohort=start_backlog(root/'state/replacement_forecast_live',start),
        database_snapshots=stamps,
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
    parser.add_argument('--follow-until',help='exclusive downstream follow-up bound (default: end + 2h)')
    args=parser.parse_args(argv);root=args.root.resolve(strict=True);out=args.out.resolve()
    if any(out==root/p or (root/p) in out.parents for p in ('state','logs','config','src')):
        parser.error('output must not be inside production state/logs/config/src')
    if args.mode=='production':
        if not args.start or not args.end:parser.error('production requires explicit --start and --end UTC offsets')
        start,end=instant(args.start),instant(args.end)
        if not start<end or end-start>timedelta(days=2):parser.error('window must be >0 and <=48 hours')
        follow=instant(args.follow_until) if args.follow_until else end+timedelta(hours=2)
        if follow<end:parser.error('--follow-until must not precede --end')
    out.mkdir(parents=True,exist_ok=False)
    result=snapshot(root,out) if args.mode=='snapshot' else production(root,out,start,end,follow)
    print(json.dumps({'output':str(out),'mode':args.mode,'commands':result.get('commands'),
        'full_ack_chains':result.get('full_ack_chains'),'residuals':result.get('collection_residuals',[])},sort_keys=True))
    return 2 if args.mode=='production' and (result.get('collection_residuals') or not result.get('full_ack_chains')) else 0


if __name__=='__main__':
    raise SystemExit(main())
