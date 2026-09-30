"""Reproduce exact-value and first-availability findings from recorded free sources.
Pure offline read/reduction; repairs audit-parser failures from retained HTTP bodies.
No nearest-time matching, no source promotion, no canonical DB access.
"""
import csv,gzip,hashlib,json,math,re,sys
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parent/'python_deps')]
import round4_collect as c
from bs4 import BeautifulSoup
BASE=Path(__file__).parent
from src.contracts.settlement_semantics import SettlementSemantics,expected_settlement_station_id
CITY_BY_STATION={expected_settlement_station_id(city):city for city in c.cities}
SEMANTICS={sid:SettlementSemantics.for_city(city) for sid,city in CITY_BY_STATION.items()}

def walk(x):
    if isinstance(x,dict):
        if 'observationText' in x:yield x
        for v in x.values():yield from walk(v)
    elif isinstance(x,list):
        for v in x:yield from walk(v)

def recovered(directory):
    rows=[];events=[];path=directory/'http.json'
    if not path.exists():return rows,events
    seen=set()
    for meta in json.loads(path.read_text()):
        ch=meta.get('channel')
        if meta.get('status')!=200 or ch not in {'mgm_metar','metaviatelecom_display','pagasa_metar','aac_metar'}:continue
        ident=(ch,meta['request_at'])
        if ident in seen:continue
        seen.add(ident);p=directory/(meta['sha256']+'.body.gz')
        if not p.exists():continue
        body=gzip.decompress(p.read_bytes());assert hashlib.sha256(body).hexdigest()==meta['sha256']
        soup=BeautifulSoup(body,'html.parser');group=defaultdict(list);receipt=c.instant(meta['receipt_at'])
        if ch=='mgm_metar':
            script=soup.find('script',id='__NEXT_DATA__')
            if script is None:continue
            for x in walk(json.loads(script.string)['props']['pageProps']['response']):
                sid=x.get('stationIcaoCode');clock=c.instant(x['observationTimeNormal'])
                if sid not in c.BYID:continue
                sample=c.raw_metar(x.get('observationText',''),sid,clock+timedelta(seconds=1))
                if sample and sample[0]==clock.replace(second=0,microsecond=0):group[sid].append(sample)
        elif ch=='metaviatelecom_display':
            modal=soup.find(id='weatherModal');text=modal.get_text(' ',strip=True) if modal else ''
            m=re.search(r'(?:METAR|SPECI)\s+UUWW\s+\d{6}Z[^=]+=',text)
            s=c.raw_metar(m[0],'UUWW',receipt) if m else None
            if s:group['UUWW'].append(s)
        elif ch=='pagasa_metar':
            for x in soup.select('p'):
                raw=x.get_text(' ',strip=True)
                if re.match(r'^(?:METAR |SPECI )?RPLL \d{6}Z',raw):
                    s=c.raw_metar(raw,'RPLL',receipt)
                    if s:group['RPLL'].append(s)
        else:
            for m in re.finditer(r'<b>MPMG</b>.{0,700}?\(M\)\s+([^"<]+)',body.decode('utf-8','replace')):
                raw=m[1].replace('\\/','/').strip()
                if not re.match(r'^(?:METAR |SPECI )?MPMG ',raw):raw='MPMG '+raw
                s=c.raw_metar(raw,'MPMG',receipt)
                if s:group['MPMG'].append(s)
        for sid,samples in group.items():
            good={(dt.isoformat(),float(v)) for dt,v in samples if dt<=receipt}
            events.append({'window':directory.name,'channel':ch,'station':sid,'samples':good,'meta':meta})
            for stamp,v in good:rows.append({'city':c.BYID[sid].name,'station':sid,'channel':ch,'observed_at':stamp,'value':v,'unit':'C',**{k:meta[k] for k in ('request_at','receipt_at','sha256')}})
    return rows,events

def main():
    allrows=[];intervals=[];http=[]
    for dn in ['round4','round4_native','round4_global','round4_knmi','round4_extra_native','round4_final_native','round4_pointchecks','round4_resume']:
        p=BASE/dn
        if (p/'samples.json').exists():allrows+=json.loads((p/'samples.json').read_text())
        if (p/'intervals.json').exists():intervals+=json.loads((p/'intervals.json').read_text())
        if (p/'http.json').exists():http+=json.loads((p/'http.json').read_text())
        rr,ee=recovered(p);allrows+=rr;previous={};first=set()
        for e in sorted(ee,key=lambda x:x['meta']['request_at']):
            k=(e['channel'],e['station']);old=previous.get(k);ss=e['samples'];m=e['meta']
            for stamp,v in sorted(ss):
                ident=(k,stamp,v)
                if ident in first:continue
                first.add(ident)
                if old and stamp>max(t for t,_ in old['samples']):
                    intervals.append({'city':c.BYID[e['station']].name,'station':e['station'],'channel':e['channel'],'observed_at':stamp,'value':v,'unit':'C',
                        'negative_request_at':old['meta']['request_at'],'positive_receipt_at':m['receipt_at'],
                        'lag_lower_ms':(c.instant(old['meta']['request_at'])-c.instant(stamp)).total_seconds()*1000,
                        'lag_upper_ms':(c.instant(m['receipt_at'])-c.instant(stamp)).total_seconds()*1000})
            if ss:previous[k]=e
    # Keep every distinct source value/version at an exact clock; any conflicting
    # rounded values are a contradiction, never resolved by cherry-picking a poll.
    groups=defaultdict(lambda:defaultdict(set))
    firstseen={}
    for r in allrows:
        sid,ch,stamp=r['station'],r['channel'],r['observed_at'];unit=CITY_BY_STATION[sid].settlement_unit
        val=float(r['value']);converted=val if r['unit']==unit else (val*1.8+32 if unit=='F' else (val-32)/1.8)
        groups[(sid,ch)][stamp].add((val,r['unit'],int(SEMANTICS[sid].round_single(converted))))
        key=(sid,ch,stamp);firstseen[key]=min(firstseen.get(key,r['receipt_at']),r['receipt_at'])
    bounds=defaultdict(list)
    for r in intervals:bounds[(r['station'],r['channel'],r['observed_at'])].append(r)
    # A newly found channel must beat the CURRENT path, including an already
    # admitted faster national lane, not merely beat a slower AWC comparator.
    from src.data.day0_fast_obs import KMA_PRIORITY_STATIONS
    current_natives=defaultdict(set)
    aliases={'jma_amedas':'jma_amedas','eccc_swob':'eccc_swob',
             'metaviatelecom_metar':'metaviatelecom_display','imd_olbs_metar':'imd_olbs_metar'}
    for route in c.load_physical_current_sources()[0]:
        if route.settlement_grade and route.provider in aliases:
            current_natives[route.station_id].add(aliases[route.provider])
    for station in KMA_PRIORITY_STATIONS:current_natives[station].add('kma_amo_raw_metar')
    report=[]
    for (sid,ch),clocks in sorted(groups.items()):
        if ch=='resolver':continue
        reference_channel='hko_native_csv' if CITY_BY_STATION[sid].settlement_source_type=='hko' else 'resolver'
        ref=groups.get((sid,reference_channel),{}) if ch!=reference_channel else {};pairs=[]
        for stamp in sorted(clocks.keys()&ref.keys()):
            aa=clocks[stamp];bb=ref[stamp];ac={x[2] for x in aa};bc={x[2] for x in bb}
            pairs.append({'time':stamp,'candidate_raw':[{'value':x[0],'unit':x[1]} for x in sorted(aa)],'resolver_raw':[{'value':x[0],'unit':x[1]} for x in sorted(bb)],
                          'candidate_contract':sorted(ac),'resolver_contract':sorted(bc),'match':len(ac)==len(bc)==1 and ac==bc})
        races=[]
        for (st,channel,stamp),bs in bounds.items():
            if (st,channel)!=(sid,ch):continue
            native=min(bs,key=lambda x:x['positive_receipt_at'])
            rr={'observation':stamp,'candidate':native,'comparators':[]}
            for name in ['awc','resolver','jma_amedas','eccc_swob','kma_amo_raw_metar','hko_native_csv','metaviatelecom_display','imd_olbs_metar']:
                if name==ch:continue
                peer=bounds.get((sid,name,stamp),[])
                if not peer:continue
                earliest=min(peer,key=lambda x:x['positive_receipt_at'])
                verdict=('FASTER' if native['lag_upper_ms']<earliest['lag_lower_ms'] else 'SLOWER' if native['lag_lower_ms']>earliest['lag_upper_ms'] else 'OVERLAP')
                rr['comparators'].append({'channel':name,'verdict':verdict,'interval':earliest})
            if rr['comparators']:races.append(rr)
        bad=[x for x in pairs if not x['match']]
        required=({'awc'} if (sid,'awc') in groups else {reference_channel}) | current_natives[sid]
        required.discard(ch)
        paired_match_times={p['time'] for p in pairs if p['match']}
        for race in races:
            race['paired_value_identity'] = race['observation'] in paired_match_times
        # A lead at an unpaired ten-minute clock cannot promote a feed whose
        # equality was checked only at other, hourly resolver instants.
        lead=bool(required) and any(
            race['paired_value_identity'] and
            all(any(x['channel']==name and x['verdict']=='FASTER' for x in race['comparators'])
                for name in required) for race in races)
        verdict='PHYSICAL_ONLY_MISMATCH' if bad else 'UNMEASURED_NO_EXACT_PAIRS' if not pairs else 'VALUE_IDENTICAL_FASTER_OBSERVED' if lead else 'VALUE_IDENTICAL_SPEED_NOT_PROVEN'
        report.append({'city':CITY_BY_STATION[sid].name,'station':sid,'channel':ch,'reference_channel':reference_channel,
            'comparison_role':'SPOT_PRODUCT_COMPARISON_NOT_FINAL_DAILY_SETTLEMENT' if reference_channel=='hko_native_csv' else 'EXACT_RESOLVER_VALUE_COMPARISON',
            'n_pairs':len(pairs),'n_exact':len(pairs)-len(bad),'mismatches':bad,'pairs':pairs,'races':races,
            'required_speed_comparators':sorted(required),'verdict':verdict})
    (BASE/'round4_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (BASE/'round4_all_samples.json.gz').write_bytes(gzip.compress(json.dumps(allrows,separators=(',',':')).encode(),mtime=0))
    (BASE/'round4_all_intervals.json').write_text(json.dumps(intervals,indent=2))
    for r in report:
        if r['channel']!='awc':print(r['city'],r['channel'],f"{r['n_exact']}/{r['n_pairs']}",r['verdict'],'races',len(r['races']))
    print('ROWS',len(allrows),'intervals',len(intervals),'cities_with_pairs',len({r['city'] for r in report if r['n_pairs']}))
if __name__=='__main__':main()
