"""Recovered anonymous national endpoints; one-minute bounded first-availability race."""
import sys,time,json,csv,io,re
from pathlib import Path
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).parent))
import round4_collect as c
c.OUT=c.BASE/'round4_extra_native';c.OUT.mkdir(exist_ok=True)

def madrid():
    r,m=c.fetch('aemet_station_xml','https://www.aemet.es/es/api-eltiempo/udat/tablas-graficas/horario/9/3129')
    root=ET.fromstring(r.content);ss=[]
    for station in root.iter('estacion'):
        if station.get('id_s')!='3129':continue
        for p in station.findall('periodo'):
            val=p.findtext('temperatura');stamp=p.get('utc')
            if val and stamp:ss.append((datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc),float(val)))
    c.record('aemet_station_xml','LEMD',ss,m)

def italy(station):
    now=datetime.now(timezone.utc);start=(now-timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ');end=now.strftime('%Y-%m-%dT%H:%M:%SZ')
    r,m=c.fetch('meteoam_metar',f'https://api.meteoam.it/deda-ows/metar-taf-icao/{station}/{start}/{end}')
    ss=[]
    for entry in r.json():
        if entry.get('icao')!=station:continue
        for row in entry.get('metar') or []:
            stamp=datetime.fromisoformat(row['validity']).replace(tzinfo=timezone.utc)
            value=c.raw_metar(row['metar_message'],station,stamp+timedelta(seconds=1))
            if value and value[0]==stamp:ss.append(value)
    c.record('meteoam_metar',station,ss,m)

def hongkong():
    r,m=c.fetch('hko_native_csv','https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_1min_temperature.csv')
    ss=[]
    for row in csv.reader(io.StringIO(r.text)):
        if len(row)>=3 and row[1]=='HK Observatory':
            stamp=datetime.strptime(row[0],'%Y%m%d%H%M').replace(tzinfo=ZoneInfo('Asia/Hong_Kong'))
            ss.append((stamp,float(row[2])))
    c.record('hko_native_csv','HKO',ss,m)
    r,m=c.fetch('hko_rhr_json','https://data.weather.gov.hk/weatherAPI/opendata/weather.php',params={'dataType':'rhrread','lang':'en'})
    temp=r.json().get('temperature',{});stamp=c.instant(temp['recordTime'])
    ss=[(stamp,x['value']) for x in temp.get('data',[]) if x.get('place')=='Hong Kong Observatory' and x.get('unit')=='C']
    c.record('hko_rhr_json','HKO',ss,m)

if __name__=='__main__':
    rounds=int(sys.argv[1]) if len(sys.argv)>1 else 18
    for n in range(rounds):
        start=time.monotonic();fs=[madrid,lambda:italy('LIMC'),lambda:italy('SBGR'),hongkong,c.awc,lambda:c.resolver('C')]
        for fn in fs[n%len(fs):]+fs[:n%len(fs)]:c.guarded(fn)
        c.save();print('EXTRA',n,len(c.ROWS),len(c.BRACKETS),datetime.now(timezone.utc).isoformat(),flush=True)
        if n<rounds-1:time.sleep(max(0,60-(time.monotonic()-start)))
    c.CLIENT.close()
