"""Reproduce exact-time origin comparisons from saved public responses; no I/O to venues."""
from __future__ import annotations
from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
import gzip
import hashlib
import json

HERE = Path(__file__).resolve().parent

def load(path):
    data = path.read_bytes() if path.exists() else gzip.decompress(path.with_suffix(path.suffix + '.gz').read_bytes())
    return json.loads(data)

def rounded(value):
    return int((Decimal(str(value)) + Decimal('0.5')).to_integral_value(rounding=ROUND_FLOOR))

def summarize():
    samples = load(HERE/'round3_race/samples.json')
    intervals = load(HERE/'round3_race/intervals.json')
    by_stream = defaultdict(lambda: defaultdict(list))
    for row in samples:
        by_stream[(row['channel'], row['station'])][row['observed_at']].append(row)
    result = []
    for (channel, station), candidates in sorted(by_stream.items()):
        if channel in {'awc', 'resolver'}:
            continue
        resolver = by_stream.get(('resolver', station), {})
        pairs = []
        for clock in sorted(set(candidates) & set(resolver)):
            cv = sorted({rounded(r['value_c']) for r in candidates[clock]})
            rv = sorted({rounded(r['value_c']) for r in resolver[clock]})
            pairs.append({'time':clock, 'candidate_values_c':sorted({r['value_c'] for r in candidates[clock]}),
                          'resolver_values_c':sorted({r['value_c'] for r in resolver[clock]}),
                          'candidate_contract':cv, 'resolver_contract':rv,
                          'match':len(cv)==len(rv)==1 and cv==rv})
        races = []
        for row in intervals:
            if (row['channel'],row['station']) != (channel,station):
                continue
            comparison = {'observation':row['observed_at'],'candidate':row,'comparators':[]}
            for other in intervals:
                if other['channel'] not in {'awc','resolver'} or other['station']!=station or other['observed_at']!=row['observed_at']:
                    continue
                verdict = ('FASTER' if row['lag_upper_ms'] < other['lag_lower_ms'] else
                           'SLOWER' if row['lag_lower_ms'] > other['lag_upper_ms'] else 'INTERVALS_OVERLAP')
                comparison['comparators'].append({'channel':other['channel'],'verdict':verdict,'interval':other})
            races.append(comparison)
        result.append({'city':next(iter(candidates.values()))[0]['city'],'station':station,'channel':channel,
                       'n_pairs':len(pairs),'n_exact':sum(p['match'] for p in pairs),
                       'mismatches':[p for p in pairs if not p['match']], 'pairs':pairs, 'races':races})
    return {'window_start':min(r['request_at'] for r in samples),'window_end':max(r['receipt_at'] for r in samples),
            'rounds':50,'distinct_samples':len(samples),'bounded_transitions':len(intervals),
            'interval_method':'last observed negative request through first positive complete response; negative lower bounds are retained, not relabeled zero latency',
            'results':result}

if __name__=='__main__':
    result=summarize()
    (HERE/'round3_origin_measurements.json').write_text(json.dumps(result,indent=2)+'\n')
    for r in result['results']:
        leads=sum(x['verdict']=='FASTER' and x['channel']=='awc' for race in r['races'] for x in race['comparators'])
        print(r['city'],r['channel'],r['n_pairs'],r['n_exact'],'strict_leads_vs_awc',leads)
