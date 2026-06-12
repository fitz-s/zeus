import re, sqlite3, statistics as st
from collections import defaultdict
from datetime import datetime, timezone
DB="file:/Users/leofitz/zeus/state/zeus-forecasts.db?mode=ro&immutable=1"
LIVE_FROM="2026-06-08T19:00:00+00:00"
def pt(ts):
    if not ts: return None
    d=datetime.fromisoformat(ts); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def pctl(xs,p):
    if not xs: return None
    xs=sorted(xs); k=(len(xs)-1)*p; f=int(k); c=min(f+1,len(xs)-1); return xs[f]+(xs[c]-xs[f])*(k-f)
con=sqlite3.connect(DB,uri=True); cur=con.cursor()
prows=cur.execute("SELECT city,target_date,temperature_metric,computed_at,source_cycle_time FROM forecast_posteriors WHERE computed_at>=? ORDER BY 1,2,3,4",(LIVE_FROM,)).fetchall()
fam=defaultdict(list)
for city,td,m,comp,sct in prows: fam[(city,td,m)].append((pt(comp),pt(sct)))
max_gap=[]
for key,lst in fam.items():
    comps=sorted(c for c,_ in lst if c)
    gaps=[(comps[i]-comps[i-1]).total_seconds()/3600 for i in range(1,len(comps))]
    if gaps: max_gap.append((max(gaps),key,len(comps)))
max_gap.sort(reverse=True)
print("=== REFRESH STARVATION: worst per-family max gap (live) ===")
for g,key,n in max_gap[:12]: print(f"  {g:6.1f}h  {key[0]:12s} {key[1]} {key[2]:5s} (#post={n})")
gaps_all=[g for g,_,_ in max_gap]
print(f"  per-family MAX gap: p50={pctl(gaps_all,.5):.1f}h p90={pctl(gaps_all,.9):.1f}h p99={pctl(gaps_all,.99):.1f}h max={max(gaps_all):.1f}h")
ing=cur.execute("SELECT captured_at,source_cycle_time FROM raw_model_forecasts WHERE provider='open-meteo' AND model='ecmwf_ifs' AND endpoint='single_runs' AND captured_at>=? ORDER BY captured_at",(LIVE_FROM,)).fetchall()
ev=sorted((pt(c),pt(s)) for c,s in ing if pt(c) and pt(s))
def fresh_by(t):
    best=None
    for cap,sct in ev:
        if cap<=t and (best is None or sct>best): best=sct
    return best
missed=[]
for key,lst in fam.items():
    for comp,consumed in lst:
        if not comp or not consumed: continue
        a=fresh_by(comp)
        if a is None: continue
        missed.append((a-consumed).total_seconds()/3600)
print("\n=== MISSED-CYCLE GAP at compute (freshest ingested ecmwf_ifs cycle - consumed) ===")
if missed:
    pos=[m for m in missed if m>0.5]
    print(f"  n={len(missed)} share born-stale(>0.5h fresher avail): {len(pos)/len(missed)*100:.1f}%")
    print(f"  gap p50={pctl(missed,.5):.2f}h p90={pctl(missed,.9):.2f}h max={max(missed):.2f}h")
con.close()
