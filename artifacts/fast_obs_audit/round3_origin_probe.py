"""Bounded anonymous national-origin discovery. HTTP success is not data identity."""
import sys,json,gzip,time,hashlib,re
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor,as_completed
from urllib.parse import urljoin
import httpx
sys.path.insert(0,str(Path(__file__).parent/'python_deps'))
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[2]
while not (ROOT/'src').is_dir():ROOT=ROOT.parent
sys.path.insert(0,str(ROOT))
OUT=Path(__file__).parent/'round3_origins';OUT.mkdir(exist_ok=True)
TARGETS={
 'ES':['https://www.aemet.es/es/eltiempo/observacion/ultimosdatos?datos=det&f=temperatura&k=mad&l=3129&w=0&x=', 'https://opendata.aemet.es/opendata/api/observacion/convencional/datos/estacion/3129'],
 'GB':['https://datahub.metoffice.gov.uk/docs/g/category/observations/overview'],
 'FR':['https://wis2nc.meteo.fr/oapi/collections?f=json'],
 'BR':['https://api-redemet.decea.mil.br/mensagens/metar/SBGR'],
 'MY':['https://www.met.gov.my/en/', 'https://metapi2.met.gov.my/'],
 'KR':['https://global.amo.go.kr/control/metar-metreport.do?tab=2'],
 'TW':['https://aoaws.anws.gov.tw/'],
 'CN':['https://data.cma.cn/'],
 'IN':['https://olbs.amsschennai.gov.in/nsweb/FlightBriefing/showopmetquery.php'],
 'TR':['https://rasat.mgm.gov.tr/'],
 'IL':['https://ims.gov.il/sites/default/files/ims_data/xml_files/imslasthour.xml'],
 'SA':['https://api-doc.ncm.gov.sa/'],
 'PK':['https://rmcsindh.pmd.gov.pk/Services_Aviation.html'],
 'MX':['https://www.gob.mx/seneam/acciones-y-programas/mas-servicios-de-control'],
 'AR':['https://ws2.smn.gob.ar/mensajes-metar-speci'],
 'ZA':['https://aviation.weathersa.co.za/aviationold/avmobile.php'],
 'RU':['https://www.aviamettelecom.ru/services/'],
 'PH':['https://www.pagasa.dost.gov.ph/aviation/metar'],
 'SG':['https://api.data.gov.sg/v1/environment/air-temperature'],
 'PA':['https://www.aeronautica.gob.pa/met/met.php?c=metar'],
 'IT':['https://www.meteoam.it/it/metar-e-taf'],
 'ID':['https://aviation.bmkg.go.id/'],
 'NG':['https://nimet.gov.ng/'],
 'NZ':['https://developer.metservice.com/docs/api-catalog/1min-obs-api/'],
}
def probe(country,url):
 start=datetime.now(timezone.utc);ts=time.monotonic_ns()
 result={'country':country,'url':url,'request_at':start.isoformat()}
 try:
  with httpx.Client(timeout=16,follow_redirects=True,headers={'User-Agent':'Zeus-public-source-audit/3'}) as c:r=c.get(url)
  result.update(status=r.status_code,receipt_at=datetime.now(timezone.utc).isoformat(),http_ms=(time.monotonic_ns()-ts)/1e6,sha256=hashlib.sha256(r.content).hexdigest(),final_url=str(r.url),content_type=r.headers.get('Content-Type'))
  out=OUT/(country+'_'+result['sha256']+'.gz');out.write_bytes(gzip.compress(r.content,mtime=0));result['body_path']=str(out.relative_to(ROOT))
  soup=BeautifulSoup(r.text,'html.parser')
  result['title']=soup.title.get_text(' ',strip=True) if soup.title else ''
  result['links']=[{'text':a.get_text(' ',strip=True)[:100],'url':urljoin(str(r.url),a['href'])} for a in soup.select('a[href]') if re.search('metar|aviat|observ|weather|csv|s[yp]nop|airport|taf|\bmet\b|amss|brief|погод|航|справ',str(a),re.I)][:70]
  result['scripts']=[urljoin(str(r.url),a['src']) for a in soup.select('script[src]')][-12:]
  result['forms']=[str(f)[:7500] for f in soup.find_all('form')][:3]
  result['text_preview']=soup.get_text(' ',strip=True)[:2000]
 except Exception as e:result.update(error_class=type(e).__name__,error=str(e).split('https://')[0][:180])
 return result
rows=[]
with ThreadPoolExecutor(max_workers=4) as pool:
 fs=[pool.submit(probe,cc,url) for cc,urls in TARGETS.items() for url in urls]
 for f in as_completed(fs):
  result=f.result();rows.append(result);print(result['country'],result.get('status',result.get('error_class')),result.get('title',''),flush=True)
  (OUT/'discovery.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
