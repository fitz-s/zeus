"""Audit exact station/time value identity and bounded first availability.

Run from the feature root. Inputs are the saved, read-only provider experiments;
no network or database writes occur. This report cannot make trading decisions.
"""
from __future__ import annotations
import csv
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import hashlib
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.config import cities
from src.contracts.settlement_semantics import SettlementSemantics

HERE = Path(__file__).resolve().parent
INPUTS = (
    HERE/'recovered/measurements_samples.json',
    HERE/'recovered/measurements_late_samples.json',
    HERE/'resume_measurements/samples.json',
)
# Value-only pair audits retained without first-seen clocks: (city, channel) ->
# file of {time, candidate, resolver} in the city's settlement unit. They add
# value pairs, never availability timing or sample rows.
VALUE_PAIR_AUDITS = {('Toronto','eccc'): HERE/'recovered/eccc_expanded_identity.json'}

def stamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00'))

def contract_value(value, unit, city):
    """The city's settlement integer for one reading, via the contract's own law."""
    number = Decimal(str(value))
    if unit != city.settlement_unit:
        number = number * Decimal(9)/5 + 32 if city.settlement_unit == 'F' else (number-32)*5/9
    return int(SettlementSemantics.for_city(city).round_single(float(number)))

def main():
    by_city={city.name: city for city in cities}
    all_rows=[]; manifests=[]; intervals=[]
    for source in INPUTS:
        data=(source.read_bytes() if source.exists()
              else gzip.decompress(source.with_suffix(source.suffix+'.gz').read_bytes()))
        rows=json.loads(data)
        manifests.append({'path':str(source.relative_to(HERE)),
            'sha256':hashlib.sha256(data).hexdigest(),'sample_rows':len(rows),
            'first_receipt':min(r['receipt_at'] for r in rows),
            'last_receipt':max(r['receipt_at'] for r in rows)})
        for r in rows:
            if r.get('station') != by_city[r['city']].wu_station:
                raise ValueError('Experiment station identity changed')
            if stamp(r['observed_at']) > stamp(r['receipt_at']):
                raise ValueError('Noncausal sample in experiment')
            all_rows.append(r)
            # KNMI sampling script recorded its listing receipt, not download
            # completion. Old JMA scans also changed 3-hour resources within a
            # poll. Neither supplies a valid lower availability bound here.
            if r['channel']=='knmi' or (r['channel']=='jma' and source != INPUTS[-1]):
                continue
            lower=r.get('availability_lower_at'); upper=r.get('availability_upper_at')
            if lower and upper:
                origin=stamp(r['observed_at'])
                intervals.append({'city':r['city'],'channel':r['channel'],
                    'station':r['station'],'observed_at':r['observed_at'],
                    'negative_request_at':lower,'positive_receipt_at':upper,
                    'lag_lower_ms':round((stamp(lower)-origin).total_seconds()*1000,3),
                    'lag_upper_ms':round((stamp(upper)-origin).total_seconds()*1000,3),
                    'experiment':str(source.relative_to(HERE))})
    # Exact source/valid-time pairs, never nearest-time matching. Preserve all
    # seen values in the pair report so a changed reading is not concealed.
    stream=defaultdict(list)
    for r in all_rows:
        stream[(r['city'],r['channel'],r['observed_at'])].append(r)
    last={k:max(rr,key=lambda r:stamp(r['receipt_at'])) for k,rr in stream.items()}
    summaries=[]; pairs=[]
    channels=sorted({(city,ch) for city,ch,_time in stream if ch!='resolver'})
    for city,ch in channels:
        c=by_city[city]; compared=[]; unpaired=[]; corrected=[]
        for (name,source_ch,when), candidate in last.items():
            if (name,source_ch)!=(city,ch): continue
            resolver=last.get((city,'resolver',when))
            if resolver is None:
                unpaired.append(when);continue
            candidate_values=sorted({contract_value(r['value'],r['unit'],c)
                                    for r in stream[(city,ch,when)]})
            resolver_values=sorted({contract_value(r['value'],r['unit'],c)
                                   for r in stream[(city,'resolver',when)]})
            a=contract_value(candidate['value'],candidate['unit'],c)
            b=contract_value(resolver['value'],resolver['unit'],c)
            pair={'city':city,'channel':ch,'station':c.wu_station,'time':when,
                  'candidate_raw':candidate['value'],'candidate_unit':candidate['unit'],
                  'candidate_contract':a,'resolver_raw':resolver['value'],
                  'resolver_unit':resolver['unit'],'resolver_contract':b,'match':a==b,
                  'candidate_versions':candidate_values,'resolver_versions':resolver_values}
            compared.append(pair);pairs.append(pair)
            if len(candidate_values)>1 or len(resolver_values)>1: corrected.append(pair)
        evidence=VALUE_PAIR_AUDITS.get((city,ch))
        if evidence is not None:
            unit=c.settlement_unit
            for old in json.loads(evidence.read_text())['pairs']:
                if any(r['time']==old['time'] for r in compared): continue
                a=contract_value(old['candidate'],unit,c);b=contract_value(old['resolver'],unit,c)
                pair={'city':city,'channel':ch,'station':c.wu_station,'time':old['time'],
                    'candidate_raw':old['candidate'],'candidate_unit':unit,'candidate_contract':a,
                    'resolver_raw':old['resolver'],'resolver_unit':unit,'resolver_contract':b,'match':a==b,
                    'candidate_versions':[a],'resolver_versions':[b],
                    'source_pair_audit_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}
                compared.append(pair);pairs.append(pair)
        bad=[r for r in compared if not r['match']]
        # A changed observed version that ever conflicts is not hidden by the
        # latest pair. Its full sequence is reported and must be reviewed.
        version_conflicts=[r for r in corrected if set(r['candidate_versions'])!=set(r['resolver_versions'])]
        latest=max((r for r in all_rows if r['city']==city and r['channel']==ch),key=lambda r:(stamp(r['observed_at']),stamp(r['receipt_at'])))
        summaries.append({'city':city,'station':c.wu_station,'channel':ch,
            'unit':c.settlement_unit,'page_view':c.settlement_page_view,
            'n_pairs':len(compared),'n_exact':len(compared)-len(bad),
            'mismatches':bad,'version_conflicts':version_conflicts,
            'n_unpaired_times':len(unpaired),'unpaired_times':sorted(unpaired),
            'value_identity_proven':bool(compared) and not bad and not version_conflicts,
            'same_endpoint_as_resolver':ch=='wu_station_history',
            'latest_observed':latest['observed_at'],'latest_value':latest['value'],
            'latest_receipt':latest['receipt_at'],
            'bounded_transitions':sum(i['city']==city and i['channel']==ch for i in intervals)})
    # Explicit all-configured-city coverage, including absent channels.
    coverage=[]
    for city in cities:
        options=[r for r in summaries if r['city']==city.name]
        coverage.append({'city':city.name,'station':city.wu_station,
            'channels':[{'channel':r['channel'],'n_pairs':r['n_pairs'],'n_exact':r['n_exact'],
                         'value_identity_proven':r['value_identity_proven'],
                         'bounded_transitions':r['bounded_transitions']} for r in options],
            'status':'MEASURED' if options else 'NO_COMPARISON_IN_THIS_EXPERIMENT'})
    report={'rule':'same configured station and exact UTC valid time; contract unit; floor(x+0.5)',
        'scope':'Short sampled windows only; value equality does not prove every future value or publication latency.',
        'excluded_latency_claims':['KNMI listing receipt is not observation download receipt',
            'Initial positive observations are left-censored, not measured first-publication delays',
            'Old JMA multi-resource polls do not prove a negative bound',
            'WU history is the resolver endpoint itself, not an independent faster transport'],
        'inputs':manifests,'cities':coverage,'comparisons':summaries}
    (HERE/'source_identity_report.json').write_text(json.dumps(report,indent=2)+'\n')
    (HERE/'source_identity_pairs.json').write_text('[\n'+',\n'.join(
        json.dumps(r,separators=(',',':')) for r in sorted(pairs,key=lambda r:(r['city'],r['channel'],r['time'])))+'\n]\n')
    (HERE/'source_availability_intervals.json').write_text(json.dumps(intervals,indent=2)+'\n')
    with (HERE/'source_identity_summary.csv').open('w') as f:
        fields=('city','station','channel','n_pairs','n_exact','value_identity_proven','bounded_transitions','n_unpaired_times')
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(summaries)
    for r in summaries:
        print(r['city'],r['channel'],f"{r['n_exact']}/{r['n_pairs']}",'transitions',r['bounded_transitions'],
              'VALUE_IDENTICAL' if r['value_identity_proven'] else 'PHYSICAL_OR_UNKNOWN')
    print('Unique pairs',len(pairs),'configured cities',len(coverage),'bounded transitions',len(intervals))

if __name__=='__main__': main()
