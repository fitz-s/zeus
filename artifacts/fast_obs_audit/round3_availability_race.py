"""Bounded current-turn first-availability race; prior negatives are actual responses."""
import sys,json,re,gzip,hashlib,time
from pathlib import Path
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[2]
while not (ROOT/'src').is_dir():ROOT=ROOT.parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent/'python_deps'))
import httpx
from bs4 import BeautifulSoup
from src.config import cities
from src.data import noaa_wrh_timeseries as wrh
from src.data.physical_current_sources import load_physical_current_sources
from src.data.station_temperature_adapters import parse_station_payload
from src.data.day0_fast_obs import _kma_observation_time,parse_kma_metar_html,KMA_AMO_METAR_ENDPOINT
from src.data.metar_temperature import metar_temperature_c
UTC=timezone.utc;OUT=Path(__file__).parent/'round3_race';OUT.mkdir(exist_ok=True)
rows=[];transport=[];intervals=[];previous={};first=set();blocked=set()
byid={c.wu_station:c for c in cities if c.wu_station};routes={r.provider:r for r in load_physical_current_sources()[0]};token=wrh.fetch_wrh_token()
client=httpx.Client(timeout=10,follow_redirects=True,headers={'User-Agent':'Zeus-readonly-origin-race/3'})
def fetch(ch,url,*,params=None,data=None):
 if httpx.URL(url).host in blocked:raise RuntimeError('RATE_LIMITED_STOP')
 before=datetime.now(UTC);r=client.post(url,data=data) if data is not None else client.get(url,params=params)
 after=datetime.now(UTC);meta={'channel':ch,'url':url,'request_at':before.isoformat(),'receipt_at':after.isoformat(),'status':r.status_code,'sha256':hashlib.sha256(r.content).hexdigest()}
 transport.append(meta)
 if r.status_code==429:blocked.add(httpx.URL(url).host)
 r.raise_for_status();(OUT/(meta['sha256']+'.body.gz')).write_bytes(gzip.compress(r.content,mtime=0));return r,meta
def record(ch,station,samples,meta):
 key=(ch,station);old=previous.get(key)
 for observed,value in samples:
  if not isinstance(observed,datetime) or observed>datetime.fromisoformat(meta['receipt_at']):continue
  clock=observed.astimezone(UTC).isoformat();ident=(ch,station,clock,float(value))
  if ident not in first:
   first.add(ident);rows.append({'city':byid[station].name,'station':station,'channel':ch,'observed_at':clock,'value_c':float(value),**{k:meta[k] for k in ('request_at','receipt_at','sha256')}})
   if old and clock>old['latest']:
    lo=(datetime.fromisoformat(old['request_at'])-observed).total_seconds()*1000
    hi=(datetime.fromisoformat(meta['receipt_at'])-observed).total_seconds()*1000
    intervals.append({'city':byid[station].name,'station':station,'channel':ch,'observed_at':clock,'negative_request_at':old['request_at'],'positive_receipt_at':meta['receipt_at'],'lag_lower_ms':lo,'lag_upper_ms':hi})
 if samples:previous[key]={'request_at':meta['request_at'],'latest':max(x[0].astimezone(UTC).isoformat() for x in samples)}
def native_metar(raw,station,receipt):
 stamp=re.search(r'\b(\d{6})Z\b',raw);v=metar_temperature_c(raw)
 if not stamp or v is None:return None
 clock=_kma_observation_time(stamp[0],as_of=receipt)
 return (clock,v) if clock is not None else None
def awc():
 ids=[s for s in byid if s!='ZSJN'];r,m=fetch('awc','https://aviationweather.gov/api/data/metar',params={'ids':','.join(ids),'format':'json','hours':3})
 for station in ids:
  samples=[(datetime.fromtimestamp(float(x['obsTime']),UTC),x['temp']) for x in r.json() if x.get('icaoId')==station and x.get('temp') is not None]
  record('awc',station,samples,m)
def resolver():
 if httpx.URL(wrh.WRH_TIMESERIES_URL).host in blocked:raise RuntimeError('RATE_LIMITED_STOP')
 ids=[c.wu_station for c in cities if c.settlement_source_type=='noaa' and c.settlement_unit=='C'];before=datetime.now(UTC)
 r=client.get(wrh.WRH_TIMESERIES_URL,params=wrh._query_params(','.join(ids),unit='C',start_utc=None,end_utc=None,recent_minutes=240,token=token),headers=wrh._page_headers(ids[0]));receipt=datetime.now(UTC)
 meta={'channel':'resolver','url':wrh.WRH_TIMESERIES_URL,'request_at':before.isoformat(),'receipt_at':receipt.isoformat(),'status':r.status_code,'sha256':hashlib.sha256(r.content).hexdigest()};transport.append(meta)
 if r.status_code==429:blocked.add(httpx.URL(wrh.WRH_TIMESERIES_URL).host)
 r.raise_for_status();(OUT/(meta['sha256']+'.body.gz')).write_bytes(gzip.compress(r.content,mtime=0))
 for station in r.json().get('STATION',[]):
  sid=station['STID'];vals=wrh.rows_from_payload({'STATION':[station]},sid);record('resolver',sid,[(v.utc,v.air_temp) for v in vals],meta)
def japan():
 now=datetime.now(UTC).astimezone(ZoneInfo('Asia/Tokyo'));r,m=fetch('jma_amedas',f'https://www.jma.go.jp/bosai/amedas/data/point/44166/{now:%Y%m%d}_{now.hour//3*3:02d}.json')
 ss=parse_station_payload(routes['jma_amedas'],r.content,received_at=datetime.fromisoformat(m['receipt_at']));record('jma_amedas','RJTT',[(s.observed_at,s.temperature_c) for s in ss],m)
def canada():
 r,m=fetch('eccc_swob','https://dd.weather.gc.ca/today/observations/swob-ml/latest/CYYZ-MAN-swob.xml');ss=parse_station_payload(routes['eccc_swob'],r.content,received_at=datetime.fromisoformat(m['receipt_at']));record('eccc_swob','CYYZ',[(s.observed_at,s.temperature_c) for s in ss],m)
def philippines():
 r,m=fetch('pagasa_metar','https://www.pagasa.dost.gov.ph/aviation/metar');receipt=datetime.fromisoformat(m['receipt_at']);soup=BeautifulSoup(r.text,'html.parser')
 ss=[native_metar(p.get_text(' ',strip=True),'RPLL',receipt) for p in soup.select('p') if re.match(r'^(?:METAR |SPECI )?RPLL \d{6}Z',p.get_text(' ',strip=True))];record('pagasa_metar','RPLL',[x for x in ss if x],m)
def panama():
 r,m=fetch('aac_metar','https://www.aeronautica.gob.pa/met/met.php?c=metar');receipt=datetime.fromisoformat(m['receipt_at'])
 for match in re.finditer(r'<b>MPMG</b>.{0,700}?\(M\)\s+([^"<]+)',r.text):
  raw=match[1].replace('\\/','/');sample=native_metar(raw,'MPMG',receipt)
  if sample:record('aac_metar','MPMG',[sample],m)
def korea():
 now=datetime.now(UTC).astimezone(ZoneInfo('Asia/Seoul'))
 for station in ('RKSI','RKPK'):
  r,m=fetch('kma_amo_raw_metar',KMA_AMO_METAR_ENDPOINT,data={'stnCd':station,'tm':now.strftime('%Y.%m.%d %H:%M')});receipt=datetime.fromisoformat(m['receipt_at']);ss=parse_kma_metar_html(r.content,station_id=station,as_of=receipt,first_seen_at=receipt);record('kma_amo_raw_metar',station,[(s.obs_time,s.temp_c) for s in ss],m)
for n in range(50):
 started=time.monotonic()
 # Rotate order to avoid consistently favoring an earlier queried publisher.
 funcs=[japan,canada,philippines,panama,korea,awc,resolver];cut=n%len(funcs)
 for fn in funcs[cut:]+funcs[:cut]:
  try:fn()
  except Exception as exc:transport.append({'channel':fn.__name__,'error_class':type(exc).__name__,'at':datetime.now(UTC).isoformat()})
 (OUT/'samples.json').write_text(json.dumps(rows,indent=2));(OUT/'intervals.json').write_text(json.dumps(intervals,indent=2));(OUT/'transport.json').write_text(json.dumps(transport,indent=2))
 print('ROUND',n,'unique',len(rows),'transitions',len(intervals),'at',datetime.now(UTC).isoformat(),flush=True)
 if n<49:time.sleep(max(0,60-(time.monotonic()-started)))
client.close()
