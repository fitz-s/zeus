"""Deduplicate exact-time identity evidence across Rounds 3 and 4.
Retain every contradiction; never add sample counts across overlapping windows.
Only updates already-admitted Tokyo/Toronto routes, not speed-based new admissions.
"""
import json,sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
from round4_compare import BASE,CITY_BY_STATION,SEMANTICS


def main():
    current=json.loads((BASE/'round4_comparison.json').read_text())
    oldwindow=json.loads((BASE/'round3_window/comparison.json').read_text())
    oldrace=json.loads((BASE/'round3_origin_measurements.json').read_text())['results']
    oldcumulative=BASE/'round4_cumulative_identity.json'
    # Rebuild from immutable original windows every time; no self-inclusion.
    targets=[('Tokyo','RJTT','jma_amedas'),('Toronto','CYYZ','eccc_swob'),
             ('Seoul','RKSI','kma_amo_raw_metar'),('Busan','RKPK','kma_amo_raw_metar')]
    result=[]
    for city,sid,ch in targets:
        times=defaultdict(lambda:{'candidate':set(),'resolver':set(),'provenance':set()})
        for source,items in [('round3_window/comparison.json',oldwindow),('round3_origin_measurements.json',oldrace),('round4_comparison.json',current)]:
            for item in items:
                if item['city']!=city or item['channel']!=ch:continue
                for p in item['pairs']:
                    cell=times[p['time']];cell['provenance'].add(source)
                    ca=p.get('candidate_contract');ra=p.get('resolver_contract')
                    cell['candidate'].update(ca if isinstance(ca,list) else [ca])
                    cell['resolver'].update(ra if isinstance(ra,list) else [ra])
        pairs=[]
        for stamp,v in sorted(times.items()):
            good=len(v['candidate'])==len(v['resolver'])==1 and v['candidate']==v['resolver']
            pairs.append({'time':stamp,'candidate_contract':sorted(v['candidate']),
                          'resolver_contract':sorted(v['resolver']),'match':good,'source_reports':sorted(v['provenance'])})
        bad=[p for p in pairs if not p['match']]
        result.append({'city':city,'station':sid,'channel':ch,'n_pairs':len(pairs),'n_exact':len(pairs)-len(bad),
                       'mismatches':bad,'pairs':pairs,'verdict':'DEMOTE' if bad else 'RETAIN_EXISTING_ADMISSION'})
    oldcumulative.write_text(json.dumps(result,indent=2)+'\n')
    registry=Path('config/physical_current_sources.json');data=json.loads(registry.read_text())
    for row in data['sources']:
        matches=[r for r in result if r['station']==row['station_id'] and r['channel']==row['provider']]
        if not matches:continue
        r=matches[0]
        row['value_identity_proof']={'city':r['city'],'channel':r['channel'],'n_pairs':r['n_pairs'],'n_exact':r['n_exact'],
            'mismatches':r['mismatches'],'report_path':'artifacts/fast_obs_audit/round4_cumulative_identity.json'}
        row['settlement_grade']=bool(r['n_pairs'] and not r['mismatches'])
    registry.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    for r in result:print(r['city'],r['n_exact'],r['n_pairs'],r['verdict'])
    if any(r['mismatches'] for r in result if r['channel']=='kma_amo_raw_metar'):
        raise RuntimeError('KMA_COUNTEREXAMPLE_REQUIRES_RUNTIME_DEMOTION_BEFORE_DELIVERY')
if __name__=='__main__':main()
