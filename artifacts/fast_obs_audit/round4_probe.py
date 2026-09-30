"""Bounded anonymous endpoint inspection; emits no credentials or response headers.
JSON job list on stdin: tag,url, optional params/data. Raw pages stay local until reviewed.
"""
import gzip,hashlib,json,re,sys,time
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin,urlsplit
sys.path.insert(0,str(Path(__file__).parent/'python_deps'))
import httpx
from bs4 import BeautifulSoup
OUT=Path(__file__).parent/'round4_discovery';OUT.mkdir(exist_ok=True)

def probe(j):
    before=datetime.now(timezone.utc);result={'tag':j['tag'],'url':j['url'],'request_at':before.isoformat()}
    try:
        with httpx.Client(timeout=12,follow_redirects=True,max_redirects=4,headers={'User-Agent':'Zeus-free-public-source-audit/4'}) as c:
            r=c.post(j['url'],data=j['data']) if 'data' in j else c.get(j['url'],params=j.get('params'))
        if len(r.content)>15_000_000:raise ValueError('OVERSIZE')
        digest=hashlib.sha256(r.content).hexdigest();path=OUT/(j['tag']+'_'+digest+'.gz')
        path.write_bytes(gzip.compress(r.content,mtime=0))
        result.update(status=r.status_code,receipt_at=datetime.now(timezone.utc).isoformat(),sha256=digest,body_path=str(path),content_type=r.headers.get('content-type'))
        soup=BeautifulSoup(r.text,'html.parser')
        result['title']=soup.title.get_text(' ',strip=True) if soup.title else ''
        result['scripts']=[urljoin(str(r.url),x['src']) for x in soup.select('script[src]')]
        result['frames']=[urljoin(str(r.url),x['src']) for x in soup.select('iframe[src]')]
        result['forms']=[{'action':urljoin(str(r.url),x.get('action','')),'method':x.get('method','GET'),'fields':[{'name':f.get('name'),'value':f.get('value'),'type':f.get('type')} for f in x.select('input,select') if f.get('type')!='password']} for x in soup.select('form')]
        result['links']=[{'text':x.get_text(' ',strip=True)[:80],'url':urljoin(str(r.url),x['href'])} for x in soup.select('a[href]') if re.search('metar|opmet|csv|obs|airport|rasat|actual|grat|free|regist|登录|登錄|api|vlieg|synop',str(x),re.I)][:50]
        result['preview']=soup.get_text(' ',strip=True)[:1100]
        result['station_snippets']={s:[r.text[max(0,m.start()-50):m.start()+320] for m in list(re.finditer(re.escape(s),r.text))[:2]] for s in j.get('stations',[])}
    except Exception as e:result.update(error=type(e).__name__)
    return result

if __name__=='__main__':
    jobs=json.load(sys.stdin)
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(probe,jobs))
    (OUT/('batch_'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    for r in rows:
        print(r['tag'],r.get('status',r.get('error')),r.get('title',''),r.get('preview','')[:300])
        print('LINKS',json.dumps(r.get('links',[])[:6],ensure_ascii=False));print('SCRIPTS',json.dumps(r.get('scripts',[])[-8:]));print('FORMS',json.dumps(r.get('forms',[])[:2],ensure_ascii=False));print('STATIONS',json.dumps(r.get('station_snippets',{}),ensure_ascii=False))
