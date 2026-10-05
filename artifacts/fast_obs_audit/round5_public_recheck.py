"""Finite four-city free-public race; no database access or automatic promotion.
Reuses the reviewed Round 4 HTTP, parser and receipt collector. Output is new and
local. Sixty-one one-minute rounds bracket hourly as well as half-hourly reports.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import time

TARGETS={'EHAM':'knmi_public_metar','LIMC':'meteoam_metar','RPLL':'pagasa_metar','MPMG':'aac_metar'}


def compare(rows, brackets, prior, rounders):
    values=defaultdict(lambda:defaultdict(set))
    for row in rows:
        if row['station'] in TARGETS:
            if row['unit']!='C':raise ValueError('FOUR_CITY_NATIVE_UNIT_MISMATCH')
            values[(row['station'],row['channel'])][row['observed_at']].add(float(row['value']))
    bounds=defaultdict(list)
    for row in brackets:bounds[(row['station'],row['channel'],row['observed_at'])].append(row)
    results=[]
    for station,channel in TARGETS.items():
        candidates=values[(station,channel)];resolver=values[(station,'resolver')]
        pairs=[]
        for stamp in sorted(candidates.keys() & resolver.keys()):
            aa={int(rounders[station](v)) for v in candidates[stamp]}
            bb={int(rounders[station](v)) for v in resolver[stamp]}
            pairs.append({'time':stamp,'candidate_c':sorted(candidates[stamp]),'resolver_c':sorted(resolver[stamp]),
                'candidate_contract':sorted(aa),'resolver_contract':sorted(bb),'match':len(aa)==len(bb)==1 and aa==bb})
        previous=[p for r in prior if r.get('station')==station and r.get('channel')==channel for p in r.get('mismatches',[])]
        races=[]
        for pair in pairs:
            stamp=pair['time'];native=bounds.get((station,channel,stamp),[])
            if not native:continue
            own=min(native,key=lambda b:b['positive_receipt_at']);peers=[]
            for name in ('awc','resolver'):
                candidates_peer=bounds.get((station,name,stamp),[])
                if not candidates_peer:continue
                peer=min(candidates_peer,key=lambda b:b['positive_receipt_at'])
                result='FASTER' if own['lag_upper_ms']<peer['lag_lower_ms'] else 'SLOWER' if own['lag_lower_ms']>peer['lag_upper_ms'] else 'OVERLAP'
                peers.append({'channel':name,'verdict':result,'interval':peer})
            races.append({'observation':stamp,'paired_value_identity':pair['match'],'candidate':own,'comparators':peers,
                'beats_all_current_paths':pair['match'] and {p['channel'] for p in peers if p['verdict']=='FASTER'}=={'awc','resolver'}})
        bad=[p for p in pairs if not p['match']]
        verdict=('PHYSICAL_ONLY_MISMATCH' if bad or previous else 'NO_PAIRED_NATIVE_RESPONSE' if not pairs else
                 'ELIGIBLE_VALUE_IDENTICAL_FASTER' if any(r['beats_all_current_paths'] for r in races) else 'RETAIN_CURRENT_NO_PROVED_FASTER_ROUTE')
        results.append({'station':station,'channel':channel,'n_pairs':len(pairs),'n_exact':len(pairs)-len(bad),
            'mismatches':bad,'prior_mismatches':previous,'pairs':pairs,'races':races,'verdict':verdict})
    return results


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--rounds',type=int,default=61);a=p.parse_args()
    if not 2<=a.rounds<=61:p.error('rounds must be 2..61')
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    sys.path.insert(0,str(Path(__file__).parent))
    import round4_collect as c
    from round4_extra_native import italy
    from round4_resume_race import knmi_metar
    from bs4 import BeautifulSoup
    from src.contracts.settlement_semantics import SettlementSemantics
    c.OUT=out
    # Refuse to erase evidence or reuse a prior in-process acquisition state.
    assert not c.ROWS and not c.BRACKETS
    admitted=[s for s in c.load_physical_current_sources()[0] if s.station_id in TARGETS and getattr(s.role,'value',s.role)=='fast_admission']
    if admitted:raise ValueError('NEW_CURRENT_ROUTE_REQUIRES_ADDITIONAL_SPEED_COMPARATOR')
    def pagasa():
        r,m=c.fetch('pagasa_metar','https://www.pagasa.dost.gov.ph/aviation/metar');rows=[]
        for element in BeautifulSoup(r.text,'html.parser').select('p'):
            text=element.get_text(' ',strip=True)
            if re.match(r'^(?:METAR |SPECI )?RPLL \d{6}Z',text):
                sample=c.raw_metar(text,'RPLL',c.instant(m['receipt_at']))
                if sample:rows.append(sample)
        c.record('pagasa_metar','RPLL',rows,m)
    def panama():
        r,m=c.fetch('aac_metar','https://www.aeronautica.gob.pa/met/met.php?c=metar');rows=[]
        for match in re.finditer(r'<b>MPMG</b>.{0,700}?\(M\)\s+([^"<]+)',r.text):
            raw=match[1].replace('\\/','/').strip()
            if not re.match(r'^(?:METAR |SPECI )?MPMG ',raw):raw='MPMG '+raw
            sample=c.raw_metar(raw,'MPMG',c.instant(m['receipt_at']))
            if sample:rows.append(sample)
        c.record('aac_metar','MPMG',rows,m)
    def awc():
        r,m=c.fetch('awc','https://aviationweather.gov/api/data/metar',params={'ids':','.join(TARGETS),'format':'json','hours':3})
        for sid in TARGETS:
            c.record('awc',sid,[(datetime.fromtimestamp(float(row['obsTime']),timezone.utc),row['temp']) for row in r.json() if row.get('icaoId')==sid and row.get('temp') is not None],m)
    def resolver():
        r,m=c.fetch('resolver',c.wrh.WRH_TIMESERIES_URL,params=c.wrh._query_params(','.join(TARGETS),unit='C',start_utc=None,end_utc=None,recent_minutes=180,token=c.wrh.fetch_wrh_token()),headers=c.wrh._page_headers('EHAM'))
        if r.json().get('UNITS',{}).get('air_temp')!='Celsius':raise ValueError('RESOLVER_UNIT_MISMATCH')
        for st in r.json().get('STATION',[]):
            sid=st['STID']
            if sid in TARGETS:c.record('resolver',sid,[(x.utc,x.air_temp) for x in c.wrh.rows_from_payload({'STATION':[st]},sid)],m)
    funcs=[pagasa,panama,knmi_metar,lambda:italy('LIMC'),awc,resolver]
    try:
        for n in range(a.rounds):
            begin=time.monotonic();shift=n%len(funcs)
            for fn in funcs[shift:]+funcs[:shift]:c.guarded(fn)
            c.save();print('ROUND',n,datetime.now(timezone.utc).isoformat(),flush=True)
            if n<a.rounds-1:time.sleep(max(0.,60-(time.monotonic()-begin)))
    finally:c.save();c.CLIENT.close()
    prior_path=Path(__file__).with_name('round4_comparison.json')
    prior=json.loads(prior_path.read_text()) if prior_path.exists() else []
    rounding={sid:SettlementSemantics.for_city(c.BYID[sid]).round_single for sid in TARGETS}
    result={'execution':'BOUNDED_PUBLIC_HTTP_MEASUREMENT_NOT_PRODUCTION_REACTION_LATENCY',
        'rounds':a.rounds,'report':compare(c.ROWS,c.BRACKETS,prior,rounding),
        'transport_failures':[r for r in c.HTTP if r.get('status')!=200],
        'closure':'All four cities receive a finite measured/transport disposition. No absence-of-pair proves that no faster source exists. No automatic runtime writes or promotions.'}
    (out/'verdict.json').write_text(json.dumps(result,indent=2)+'\n')
    for row in result['report']:print(row['station'],row['n_exact'],row['n_pairs'],row['verdict'])


if __name__=='__main__':main()
