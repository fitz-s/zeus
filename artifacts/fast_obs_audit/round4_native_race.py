"""Free national-service global METAR distribution and selected native public pages.
MGM is the national origin for Turkey, a national-service redistribution elsewhere.
This distinction is recorded rather than claiming CMA/SMN/etc origin access.
"""
import argparse,sys,json,re,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlencode
sys.path.insert(0,str(Path(__file__).parent))
import round4_collect as c
from bs4 import BeautifulSoup
c.OUT=c.BASE/'round4_native';c.OUT.mkdir(exist_ok=True)
UTC=timezone.utc

def observations(x):
    if isinstance(x,dict):
        if 'observationText' in x:yield x
        for v in x.values():yield from observations(v)
    elif isinstance(x,list):
        for v in x:yield from observations(v)

def mgm():
    ids=tuple(c.BYID)
    # One fixed public request per minute, not one per city; only raw METAR/SPECI.
    params=[('hours','24'),('obsType','1')]+[('stations',s) for s in ids]
    r,m=c.fetch('mgm_metar','https://rasat.mgm.gov.tr/result',params=params)
    s=BeautifulSoup(r.text,'html.parser').find('script',id='__NEXT_DATA__')
    if s is None:raise ValueError('MGM_PAGE_HAS_NO_OBSERVATION_DATA')
    d=json.loads(s.string); grouped={sid:[] for sid in ids}
    for x in observations(d.get('props',{}).get('pageProps',{}).get('response',{})):
        sid=x.get('stationIcaoCode');raw=x.get('observationText','').strip()
        if sid not in grouped:continue
        reported=c.instant(x['observationTimeNormal'])
        ss=c.raw_metar(raw,sid,reported+timedelta(seconds=1))
        if not ss or ss[0]!=reported.replace(second=0,microsecond=0):continue
        grouped[sid].append(ss)
    for sid,ss in grouped.items():c.record('mgm_metar',sid,ss,m)
    print('MGM',sum(len(x) for x in grouped.values()),'stations',sum(bool(x) for x in grouped.values()),flush=True)

def russia():
    r,m=c.fetch('metaviatelecom_display','http://display.meteocenter.ru/219')
    soup=BeautifulSoup(r.text,'html.parser')
    modal=soup.find(id='weatherModal')
    if modal is None:raise ValueError('RUSSIAN_RAW_REPORT_UNAVAILABLE')
    match=re.search(r'(?:METAR|SPECI)\s+UUWW\s+\d{6}Z[^=]+=',modal.get_text(' ',strip=True))
    if not match:raise ValueError('RUSSIAN_STATION_UNAVAILABLE')
    sample=c.raw_metar(match[0],'UUWW',c.instant(m['receipt_at']))
    if not sample:raise ValueError('RUSSIAN_RAW_REPORT_INVALID')
    c.record('metaviatelecom_display','UUWW',[sample],m)

def main():
    p=argparse.ArgumentParser();p.add_argument('--rounds',type=int,default=31);a=p.parse_args()
    for n in range(a.rounds):
        start=time.monotonic()
        # Independent repeated requests. Rotate before/after reference requests.
        fs=[mgm,c.awc,lambda:c.resolver('C'),russia]
        for fn in fs[n%4:]+fs[:n%4]:c.guarded(fn)
        c.save();print('NATIVE_ROUND',n,'samples',len(c.ROWS),'brackets',len(c.BRACKETS),'at',datetime.now(UTC).isoformat(),flush=True)
        if n<a.rounds-1:time.sleep(max(0,60-(time.monotonic()-start)))
    c.CLIENT.close()
if __name__=='__main__':main()
