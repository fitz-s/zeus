"""Final bounded native-public window, including the IMD POST form and Moscow.
No login or registration: fields exactly match the public site's JavaScript.
"""
import sys,re,time
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).parent))
import round4_collect as c
from bs4 import BeautifulSoup
c.OUT=c.BASE/'round4_final_native';c.OUT.mkdir(exist_ok=True)

def india():
    r,m=c.fetch('imd_olbs_metar','https://olbs.amsschennai.gov.in/nsweb/FlightBriefing/showopmetquery.php',data={'icaos':'VILK','type':'metar'})
    text=BeautifulSoup(r.text,'html.parser').get_text(' ',strip=True);ss=[]
    for match in re.finditer(r'(?:METAR|SPECI)\s+VILK\s+\d{6}Z[^=]+=',text):
        sample=c.raw_metar(match[0],'VILK',c.instant(m['receipt_at']))
        if sample:ss.append(sample)
    c.record('imd_olbs_metar','VILK',ss,m)

def russia():
    r,m=c.fetch('metaviatelecom_display','http://display.meteocenter.ru/219')
    modal=BeautifulSoup(r.text,'html.parser').find(id='weatherModal')
    match=re.search(r'(?:METAR|SPECI)\s+UUWW\s+\d{6}Z[^=]+=',modal.get_text(' ',strip=True) if modal else '')
    ss=c.raw_metar(match[0],'UUWW',c.instant(m['receipt_at'])) if match else None
    c.record('metaviatelecom_display','UUWW',[ss] if ss else [],m)

if __name__=='__main__':
    nrounds=int(sys.argv[1]) if len(sys.argv)>1 else 18
    for n in range(nrounds):
        start=time.monotonic();fs=[india,russia,c.japan,c.canada,c.korea,c.awc,lambda:c.resolver('C')]
        for fn in fs[n%len(fs):]+fs[:n%len(fs)]:c.guarded(fn)
        c.save();print('FINAL_NATIVE',n,len(c.ROWS),len(c.BRACKETS),datetime.now(timezone.utc).isoformat(),flush=True)
        if n<nrounds-1:time.sleep(max(0,60-(time.monotonic()-start)))
    c.CLIENT.close()
