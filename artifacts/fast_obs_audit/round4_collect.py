"""Free public observation experiment; never opens a production database.

Run a bounded observation window while adding reviewed public endpoints to
round4_extra.json. Every negative/positive bracket comes from actual responses.
Credentials are never accepted from the job registry or stored in receipts.
"""
from __future__ import annotations
import argparse, csv, gzip, hashlib, io, json, math, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[2]
while not (ROOT/'src').is_dir(): ROOT=ROOT.parent
sys.path[:0]=[str(ROOT),str(Path(__file__).parent/'python_deps')]
import httpx
from bs4 import BeautifulSoup
from src.config import cities
from src.data import noaa_wrh_timeseries as wrh
from src.data.day0_fast_obs import _kma_observation_time,parse_kma_metar_html,KMA_AMO_METAR_ENDPOINT
from src.data.physical_current_sources import load_physical_current_sources
from src.data.station_temperature_adapters import parse_station_payload
from src.data.metar_temperature import metar_temperature_c
UTC=timezone.utc
BASE=Path(__file__).parent
OUT=BASE/'round4';OUT.mkdir(exist_ok=True)
LOCK=threading.Lock();REQUEST_LOCK=threading.Lock()
ROWS=[];HTTP=[];BRACKETS=[];LAST={};FIRST={};PAUSED={}
BYID={c.wu_station:c for c in cities if c.wu_station}
ROUTES={r.provider:r for r in load_physical_current_sources()[0]}
CLIENT=httpx.Client(timeout=12,follow_redirects=True,headers={'User-Agent':'Zeus-free-public-observation-audit/4'})

def instant(x):
    d=datetime.fromisoformat(str(x).replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('NAIVE_PROVIDER_CLOCK')
    return d.astimezone(UTC)

def fetch(channel,url,*,params=None,data=None,headers=None,save=True):
    host=urlsplit(url).hostname
    with REQUEST_LOCK:
        if time.monotonic()<PAUSED.get(host,0): raise RuntimeError('PROVIDER_COOLDOWN')
    before=datetime.now(UTC)
    try:
        r=CLIENT.post(url,data=data,headers=headers) if data is not None else CLIENT.get(url,params=params,headers=headers)
        receipt=datetime.now(UTC)
        if len(r.content)>15_000_000: raise ValueError('RESPONSE_TOO_LARGE')
        digest=hashlib.sha256(r.content).hexdigest()
        meta={'channel':channel,'url':urlunsplit((urlsplit(url).scheme,urlsplit(url).netloc,urlsplit(url).path,'','')),
              'request_at':before.isoformat(),'receipt_at':receipt.isoformat(),'status':r.status_code,'sha256':digest}
        with LOCK:
            HTTP.append(meta)
            if save and r.status_code==200: (OUT/(digest+'.body.gz')).write_bytes(gzip.compress(r.content,mtime=0))
        if r.status_code in {401,403,429}:
            with REQUEST_LOCK: PAUSED[host]=time.monotonic()+3600
        r.raise_for_status()
        return r,meta
    except Exception as exc:
        with LOCK: HTTP.append({'channel':channel,'url':urlunsplit((urlsplit(url).scheme,urlsplit(url).netloc,urlsplit(url).path,'','')),
             'request_at':before.isoformat(),'error':type(exc).__name__})
        raise

def record(channel,station,samples,meta,unit='C'):
    receipt=instant(meta['receipt_at']); good=[]
    for stamp,value in samples:
        if stamp is None or value is None: continue
        stamp=instant(stamp) if not isinstance(stamp,datetime) else stamp.astimezone(UTC)
        if stamp>receipt: continue
        v=float(value)
        if not math.isfinite(v) or not -100< v < 150: continue
        good.append((stamp.isoformat(),v))
    current=set(good);key=(channel,station)
    with LOCK:
        old=LAST.get(key)
        for stamp,value in sorted(current):
            identity=(channel,station,stamp,value)
            if identity in FIRST: continue
            FIRST[identity]=meta['receipt_at']
            ROWS.append({'city':BYID[station].name if station in BYID else 'Hong Kong','station':station,
                         'channel':channel,'observed_at':stamp,'value':value,'unit':unit,
                         **{k:meta[k] for k in ('request_at','receipt_at','sha256')}})
            # The immediately preceding successful response from this exact
            # resource must have lacked this observation/version. Restrict to
            # an advancing observation clock so a finite history window cannot
            # manufacture a negative for an old report outside retention.
            if old and current and stamp>old['latest'] and (stamp,value) not in old['samples']:
                lo=(instant(old['request_at'])-instant(stamp)).total_seconds()*1000
                hi=(receipt-instant(stamp)).total_seconds()*1000
                BRACKETS.append({'city':BYID[station].name if station in BYID else 'Hong Kong','station':station,'channel':channel,
                                 'observed_at':stamp,'value':value,'unit':unit,'negative_request_at':old['request_at'],
                                 'positive_receipt_at':meta['receipt_at'],'lag_lower_ms':lo,'lag_upper_ms':hi})
        if current: LAST[key]={'latest':max(s for s,v in current),'samples':current,'request_at':meta['request_at']}

def raw_metar(raw,station,receipt):
    raw=' '.join(str(raw).split()).replace('\\/','/')
    # Only an explicit ICAO prefix admits a bulletin as this station.
    if not re.match(r'^(?:(?:METAR|SPECI)\s+)?(?:COR\s+)?'+re.escape(station)+r'\s',raw): return None
    m=re.search(r'\b(\d{6})Z\b',raw);v=metar_temperature_c(raw)
    if not m or v is None: return None
    d=_kma_observation_time(m[1],as_of=receipt)
    return (d,v) if d else None

def awc():
    r,m=fetch('awc','https://aviationweather.gov/api/data/metar',params={'ids':','.join(BYID),'format':'json','hours':24})
    for sid in BYID:
        ss=[(datetime.fromtimestamp(float(x['obsTime']),UTC),x['temp']) for x in r.json() if x.get('icaoId')==sid and x.get('temp') is not None]
        record('awc',sid,ss,m)

def resolver(unit):
    ids=[c.wu_station for c in cities if c.settlement_source_type=='noaa' and c.settlement_unit==unit]
    r,m=fetch('resolver',wrh.WRH_TIMESERIES_URL,params=wrh._query_params(','.join(ids),unit=unit,start_utc=None,end_utc=None,recent_minutes=72*60,token=wrh.fetch_wrh_token()),headers=wrh._page_headers(ids[0]))
    if r.json().get('UNITS',{}).get('air_temp')!={'C':'Celsius','F':'Fahrenheit'}[unit]: raise ValueError('WRH_UNIT_MISMATCH')
    for st in r.json().get('STATION',[]):
        sid=st['STID'];c=BYID[sid]
        rows=wrh.rows_from_payload({'STATION':[st]},sid)
        record('resolver',sid,[(x.utc,x.air_temp) for x in rows if unit=='C' or x.is_official_report],m,unit)

def japan():
    local=datetime.now(UTC).astimezone(ZoneInfo('Asia/Tokyo'))
    r,m=fetch('jma_amedas',f'https://www.jma.go.jp/bosai/amedas/data/point/44166/{local:%Y%m%d}_{local.hour//3*3:02d}.json')
    ss=parse_station_payload(ROUTES['jma_amedas'],r.content,received_at=instant(m['receipt_at']))
    record('jma_amedas','RJTT',[(s.observed_at,s.temperature_c) for s in ss],m)

def canada():
    r,m=fetch('eccc_swob','https://dd.weather.gc.ca/today/observations/swob-ml/latest/CYYZ-MAN-swob.xml')
    ss=parse_station_payload(ROUTES['eccc_swob'],r.content,received_at=instant(m['receipt_at']))
    record('eccc_swob','CYYZ',[(s.observed_at,s.temperature_c) for s in ss],m)

def korea():
    local=datetime.now(UTC).astimezone(ZoneInfo('Asia/Seoul'))
    for sid in ('RKSI','RKPK'):
        r,m=fetch('kma_amo_raw_metar',KMA_AMO_METAR_ENDPOINT,data={'stnCd':sid,'tm':local.strftime('%Y.%m.%d %H:%M')})
        ss=parse_kma_metar_html(r.content,station_id=sid,as_of=instant(m['receipt_at']),first_seen_at=instant(m['receipt_at']))
        record('kma_amo_raw_metar',sid,[(s.obs_time,s.temp_c) for s in ss],m)

def origin(job):
    channel,sid,kind=job['channel'],job['station'],job['parser']
    params=dict(job.get('params',{}))
    if kind=='fmi':
        params.update(service='WFS',version='2.0.0',request='getFeature',storedquery_id='fmi::observations::weather::simple',fmisid='100968',parameters='t2m',starttime=(datetime.now(UTC)-timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    r,m=fetch(channel,job['url'],params=params or None,data=job.get('data'))
    received=instant(m['receipt_at']);ss=[]
    if kind in ('dwd_cdc','imgw_synop'):
        route=ROUTES[kind];s=parse_station_payload(route,r.content,received_at=received)
        ss=[(x.observed_at,x.temperature_c) for x in s]
    elif kind=='fmi':
        import xml.etree.ElementTree as ET
        root=ET.fromstring(r.content)
        for e in root.iter():
            if e.tag.endswith('BsWfsElement'):
                v={x.tag.rsplit('}',1)[-1]:x.text for x in e}
                if v.get('ParameterName')=='t2m': ss.append((instant(v['Time']),float(v['ParameterValue'])))
    elif kind=='metar_html':
        soup=BeautifulSoup(r.text,'html.parser');text=soup.get_text(' ',strip=True)
        # A station-prefixed line ends at '=' or the next METAR/HTML fragment.
        for x in re.findall(r'(?:(?:METAR|SPECI)\s+)?(?:COR\s+)?'+re.escape(sid)+r'\s+\d{6}Z.{0,400}?(?:=|(?=\s+(?:METAR|SPECI)\s)|$)',text):
            s=raw_metar(x,sid,received)
            if s:ss.append(s)
    elif kind=='panama':
        for match in re.finditer(r'<b>'+re.escape(sid)+r'</b>.{0,700}?\(M\)\s+([^"<]+)',r.text):
            s=raw_metar(match[1],sid,received)
            if s:ss.append(s)
    elif kind=='aemet_table':
        soup=BeautifulSoup(r.text,'html.parser')
        if '3129' not in soup.get_text(): raise ValueError('AEMET_STATION_MISSING')
        for row in soup.select('tr'):
            cells=[x.get_text(' ',strip=True) for x in row.select('td')]
            if len(cells)>1 and re.fullmatch(r'\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}',cells[0]):
                stamp=datetime.strptime(cells[0],'%d/%m/%Y %H:%M').replace(tzinfo=ZoneInfo('Europe/Madrid'))
                ss.append((stamp,float(cells[1].replace(',','.'))))
    elif kind=='knmi_xml':
        import xml.etree.ElementTree as ET
        root=ET.fromstring(r.content)
        for s in root.iter('stationmeting'):
            if s.findtext('stationcode') in ('06240','6240'):
                # KNMI actual XML carries date/time explicitly; reject if absent.
                stamp=s.findtext('datum')
                if stamp and 'T' in stamp:ss.append((instant(stamp),float(s.findtext('temperatuur'))))
    else: raise ValueError('UNKNOWN_AUDIT_PARSER')
    if not ss:
        with LOCK: HTTP.append({'channel':channel,'station':sid,'receipt_at':m['receipt_at'],'status':'NO_PARSED_STATION_OBSERVATIONS','sha256':m['sha256']})
    record(channel,sid,ss,m)

def wu():
    # Existing free resolver-page backend; no account credentials or paid key.
    from src.data.daily_obs_append import WU_API_KEY,WU_HEADERS
    for c in cities:
        if c.settlement_source_type!='wu_icao': continue
        sid=c.wu_station;local=datetime.now(UTC).astimezone(ZoneInfo(c.timezone))
        r,m=fetch('resolver',f'https://api.weather.com/v1/location/{sid}:9:{c.country_code}/observations/historical.json',params={'apiKey':WU_API_KEY,'units':'m','startDate':(local-timedelta(days=1)).strftime('%Y%m%d'),'endDate':local.strftime('%Y%m%d')},headers=WU_HEADERS)
        d=r.json()
        if d.get('metadata',{}).get('location_id')!=f'{sid}:9:{c.country_code}':raise ValueError('WU_STATION_MISMATCH')
        record('resolver',sid,[(datetime.fromtimestamp(float(x['valid_time_gmt']),UTC),x['temp']) for x in d.get('observations',[]) if x.get('obs_id')==sid and x.get('temp') is not None],m)

def guarded(fn):
    try: fn()
    except Exception as e:
        with LOCK: HTTP.append({'job':getattr(fn,'__name__','origin'),'error':type(e).__name__,'at':datetime.now(UTC).isoformat()})

def save():
    with LOCK:
        for name,data in [('samples',ROWS),('http',HTTP),('intervals',BRACKETS)]:
            p=OUT/(name+'.json');tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));tmp.replace(p)

def main():
    p=argparse.ArgumentParser();p.add_argument('--rounds',type=int,default=25);a=p.parse_args()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n in range(a.rounds):
            started=time.monotonic()
            jobs=[japan,canada,korea,awc,lambda:resolver('C'),lambda:resolver('F')]
            if n%5==0:jobs.append(wu)
            extra=BASE/'round4_extra.json'
            for job in json.loads(extra.read_text()) if extra.exists() else []:
                if n%int(job.get('every_n_rounds',1))==0: jobs.append(lambda job=job:origin(job))
            # Rotate ordering; comparisons use request/receipt bounds regardless.
            cut=n%len(jobs);list(pool.map(guarded,jobs[cut:]+jobs[:cut]));save()
            print('ROUND',n,'samples',len(ROWS),'brackets',len(BRACKETS),'at',datetime.now(UTC).isoformat(),flush=True)
            if n<a.rounds-1:time.sleep(max(0,60-(time.monotonic()-started)))
    CLIENT.close()
if __name__=='__main__':main()
