"""Bounded MGM global comparison, batches respect observed server maximum ten stations.
Repairs the audit-only timestamp call (KMA utility requires the trailing Z).
Prior raw HTTP evidence is retained and independently reparsed, not discarded.
"""
import argparse,json,time,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import round4_collect as c
from bs4 import BeautifulSoup
c.OUT=c.BASE/'round4_global';c.OUT.mkdir(exist_ok=True)

def records(x):
    if isinstance(x,dict):
        if 'observationText' in x:yield x
        for v in x.values():yield from records(v)
    elif isinstance(x,list):
        for v in x:yield from records(v)

def mgm_group(ids):
    params=[('hours','24'),('obsType','1')]+[('stations',s) for s in ids]
    r,m=c.fetch('mgm_metar','https://rasat.mgm.gov.tr/result',params=params)
    script=BeautifulSoup(r.text,'html.parser').find('script',id='__NEXT_DATA__')
    if script is None:raise ValueError('MGM_NO_DATA')
    groups={s:[] for s in ids}
    for row in records(json.loads(script.string)['props']['pageProps']['response']):
        sid=row.get('stationIcaoCode')
        if sid not in groups:continue
        clock=c.instant(row['observationTimeNormal'])
        sample=c.raw_metar(row['observationText'],sid,clock+timedelta(seconds=1))
        if sample and sample[0]==clock.replace(second=0,microsecond=0):groups[sid].append(sample)
    for sid,ss in groups.items():c.record('mgm_metar',sid,ss,m)
    return sum(len(v) for v in groups.values())

def main():
    a=argparse.ArgumentParser();a.add_argument('--rounds',type=int,default=22);args=a.parse_args()
    ids=tuple(c.BYID);groups=[ids[i:i+10] for i in range(0,len(ids),10)]
    for n in range(args.rounds):
        start=time.monotonic();fs=[lambda g=g:mgm_group(g) for g in groups]+[c.awc,lambda:c.resolver('C')]
        for fn in fs[n%len(fs):]+fs[:n%len(fs)]:c.guarded(fn)
        c.save();print('GLOBAL_ROUND',n,'n',len(c.ROWS),'transitions',len(c.BRACKETS),'at',datetime.now(timezone.utc).isoformat(),flush=True)
        if n<args.rounds-1:time.sleep(max(0,60-(time.monotonic()-start)))
    c.CLIENT.close()
if __name__=='__main__':main()
