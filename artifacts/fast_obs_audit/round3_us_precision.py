"""Compare US raw METAR precision choices against the actual WRH hourly-view values."""
import sys,json,gzip,re,math,time,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
while not (ROOT/'src').is_dir(): ROOT=ROOT.parent
sys.path.insert(0,str(ROOT))
import httpx
from src.config import cities
from src.data import noaa_wrh_timeseries as wrh
from src.data.metar_temperature import metar_t_group_temperature_c
UTC=timezone.utc;OUT=Path(__file__).parent/'round3_us';OUT.mkdir(exist_ok=True)
cs=[c for c in cities if c.country_code=='US'];token=wrh.fetch_wrh_token();pairs=[];stats=[]
body_re=re.compile(r'(?:^|\s)(M?\d{2})/(?:M?\d{2}|//)(?=\s|$)')
with httpx.Client(timeout=15) as client:
 for c in cs:
  rs=wrh.fetch_wrh_timeseries(c.wu_station,unit='F',token=token,recent_minutes=4320)
  response=client.get('https://aviationweather.gov/api/data/metar',params={'ids':c.wu_station,'format':'json','hours':72})
  response.raise_for_status();stamp=datetime.now(UTC).isoformat();data=response.json()
  (OUT/(c.wu_station+'.awc.json.gz')).write_bytes(gzip.compress(response.content,mtime=0))
  by={datetime.fromtimestamp(float(r['obsTime']),UTC).isoformat():r for r in data}
  for r in rs:
   if not r.is_official_report or r.utc.isoformat() not in by:continue
   a=by[r.utc.isoformat()];raw=str(a.get('rawOb') or '');m=body_re.search(raw)
   body=None if not m else float(m[1].replace('M','-'));tg=metar_t_group_temperature_c(raw)
   # Payload and raw report are retained so another reviewer can re-evaluate mechanisms.
   p={'city':c.name,'station':c.wu_station,'observed_at':r.utc.isoformat(),'resolver_f':r.air_temp,'resolver_raw':r.raw_metar,'routine':r.is_routine_metar,'awc_type':a.get('metarType'),'awc_temp_c':a.get('temp'),'raw':raw,'body_c':body,'t_group_c':tg,'receipt_at':stamp}
   for key,v in [('body',body),('t_group',tg),('conditional',tg if r.is_routine_metar else body),('raw_slp',tg if re.search(r'\bSLP\d{3}\b',raw) else body),('type',body if str(a.get('metarType','')).upper()=='SPECI' else tg)]:
    p[key+'_f']=None if v is None else v*1.8+32
    p[key+'_match']=v is not None and math.floor(v*1.8+32+.5)==math.floor(r.air_temp+.5)
   pairs.append(p)
  these=[p for p in pairs if p['city']==c.name]
  result={'city':c.name,'station':c.wu_station,'n_pairs':len(these),**{key:sum(p[key+'_match'] for p in these) for key in ('body','t_group','conditional','raw_slp','type')}}
  stats.append(result);print(json.dumps(result),flush=True)
  (OUT/'pairs.json').write_text(json.dumps(pairs,indent=2));(OUT/'summary.json').write_text(json.dumps(stats,indent=2))
  time.sleep(1)
print('MISMATCHES',json.dumps([p for p in pairs if not p['raw_slp_match']],indent=2),flush=True)
