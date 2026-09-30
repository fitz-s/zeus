"""Read-only exact-time JMA/ECCC/WRH comparison. Public bodies only; no credentials exported."""
from __future__ import annotations
import sys, json, gzip, re, math, time, hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
import httpx
ROOT=Path(__file__).resolve().parents[2]
# This file lives under artifacts/fast_obs_audit, three parents below the repo.
ROOT=Path(__file__).resolve().parents[2]
while not (ROOT/'src').is_dir(): ROOT=ROOT.parent
sys.path.insert(0,str(ROOT))
from src.data import noaa_wrh_timeseries as wrh
from src.data.physical_current_sources import load_physical_current_sources
from src.data.station_temperature_adapters import parse_station_payload
UTC=timezone.utc
OUT=Path(__file__).parent/'round3_window';OUT.mkdir(exist_ok=True)
NOW=datetime.now(UTC);START=NOW-timedelta(hours=72)
routes={r.provider:r for r in load_physical_current_sources()[0]}
rows=[];transports=[];halted=set()
client=httpx.Client(timeout=15,follow_redirects=True,headers={'User-Agent':'Zeus-readonly-source-audit/3'})
def get(url):
 host=httpx.URL(url).host
 if host in halted: raise RuntimeError('RATE_LIMITED_HOST_STOPPED')
 start=datetime.now(UTC); t=time.monotonic_ns()
 r=client.get(url); receipt=datetime.now(UTC)
 meta={'url':url,'request_at':start.isoformat(),'receipt_at':receipt.isoformat(),'status':r.status_code,'http_ms':(time.monotonic_ns()-t)/1e6,'sha256':hashlib.sha256(r.content).hexdigest()}
 transports.append(meta)
 if r.status_code==429: halted.add(host)
 r.raise_for_status()
 (OUT/(meta['sha256']+'.body.gz')).write_bytes(gzip.compress(r.content,mtime=0))
 time.sleep(0.75)
 return r,meta
for city,station in [('Tokyo','RJTT'),('Toronto','CYYZ')]:
 try:
  stamp=datetime.now(UTC)
  readings=wrh.fetch_wrh_timeseries(station,unit='C',recent_minutes=4320)
  for x in readings:
   rows.append({'city':city,'station':station,'channel':'resolver','observed_at':x.utc.isoformat(),'value_c':x.air_temp,'raw_metar':x.raw_metar,'receipt_at':datetime.now(UTC).isoformat()})
  print('RESOLVER',city,len(readings),flush=True)
 except Exception as exc: transports.append({'city':city,'channel':'resolver','error_class':type(exc).__name__,'detail':str(exc).split('http')[0][:180]})
# Archived three-hour JMA resources avoid waiting for 48 future reports.
jst=timezone(timedelta(hours=9));at=START.astimezone(jst).replace(minute=0,second=0,microsecond=0)
at=at.replace(hour=at.hour//3*3)
while at<=NOW.astimezone(jst):
 url=f'https://www.jma.go.jp/bosai/amedas/data/point/44166/{at:%Y%m%d}_{at.hour:02d}.json'
 try:
  response,meta=get(url)
  samples=parse_station_payload(routes['jma_amedas'],response.content,received_at=datetime.fromisoformat(meta['receipt_at']))
  for s in samples:
   if START<=s.observed_at<=NOW: rows.append({'city':'Tokyo','station':'RJTT','channel':'jma_amedas','observed_at':s.observed_at.isoformat(),'value_c':s.temperature_c,'receipt_at':s.fetched_at.isoformat(),'body_sha256':meta['sha256']})
 except Exception as exc: transports.append({'channel':'jma_amedas','url':url,'error_class':type(exc).__name__})
 at+=timedelta(hours=3)
(OUT/'samples.json').write_text(json.dumps(rows,indent=2))
for n in range(4):
 day=(NOW-timedelta(days=n)).strftime('%Y%m%d')
 base=(f'https://dd.weather.gc.ca/today/observations/swob-ml/{day}/CYYZ/' if n<=1 else f'https://dd.weather.gc.ca/{day}/WXO-DD/observations/swob-ml/{day}/CYYZ/')
 try:
  listing,meta=get(base)
  names=sorted(set(re.findall(r'href="([^"]*CYYZ-MAN[^"/]*-swob\.xml)"',listing.text)))
  print('ECCC_DIRECTORY',day,len(names),flush=True)
  for name in names:
   try:
    response,meta=get(urljoin(base,name))
    samples=parse_station_payload(routes['eccc_swob'],response.content,received_at=datetime.fromisoformat(meta['receipt_at']))
    for s in samples:
     if START<=s.observed_at<=NOW:rows.append({'city':'Toronto','station':'CYYZ','channel':'eccc_swob','observed_at':s.observed_at.isoformat(),'value_c':s.temperature_c,'receipt_at':s.fetched_at.isoformat(),'body_sha256':meta['sha256'],'resource':name})
   except Exception as exc:transports.append({'channel':'eccc_swob','url':urljoin(base,name),'error_class':type(exc).__name__})
 except Exception as exc:transports.append({'channel':'eccc_swob','url':base,'error_class':type(exc).__name__})
(OUT/'samples.json').write_text(json.dumps(rows,indent=2));(OUT/'transports.json').write_text(json.dumps(transports,indent=2))
results=[]
for city,ch in [('Tokyo','jma_amedas'),('Toronto','eccc_swob')]:
 resolver={r['observed_at']:r for r in rows if r['city']==city and r['channel']=='resolver'}
 candidates={ (r['observed_at'],r['value_c']):r for r in rows if r['city']==city and r['channel']==ch}
 pairs=[]
 for (clock,value),r in sorted(candidates.items()):
  if clock not in resolver:continue
  rv=resolver[clock]['value_c'];match=math.floor(value+0.5)==math.floor(rv+0.5)
  pairs.append({'time':clock,'candidate_c':value,'candidate_contract':math.floor(value+0.5),'resolver_c':rv,'resolver_contract':math.floor(rv+0.5),'match':match,'body_sha256':r.get('body_sha256')})
 result={'city':city,'channel':ch,'n_pairs':len(pairs),'n_exact':sum(p['match'] for p in pairs),'mismatches':[p for p in pairs if not p['match']],'pairs':pairs,'first_availability':'UNKNOWN_HISTORICAL_FETCH_NOT_FIRST_PUBLICATION','window_start':START.isoformat(),'window_end':NOW.isoformat()}
 results.append(result);print('COMPARISON',city,result['n_pairs'],result['n_exact'],json.dumps(result['mismatches']),flush=True)
(OUT/'comparison.json').write_text(json.dumps(results,indent=2))
client.close()
