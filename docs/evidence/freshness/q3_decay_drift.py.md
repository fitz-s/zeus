# READ-ONLY freshness probe Q3 (mode=ro&immutable=1). See 2026-06-12_forecast_freshness_truth.md.
import sqlite3, json, math, re, statistics as st
from collections import defaultdict
from datetime import datetime, timezone
DB="file:state/zeus-forecasts.db?mode=ro&immutable=1"; BIN=re.compile(r"(-?\d+)\s*°?C")
def pt(ts):
    if not ts: return None
    d=datetime.fromisoformat(ts); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def bt(s):
    m=BIN.search(s); return int(m.group(1)) if m else None
def pctl(xs,p):
    if not xs: return None
    xs=sorted(xs); k=(len(xs)-1)*p; f=int(k); c=min(f+1,len(xs)-1); return xs[f]+(xs[c]-xs[f])*(k-f)
con=sqlite3.connect(DB,uri=True); cur=con.cursor()
settled={}
for c,td,m,w in cur.execute("SELECT city,target_date,temperature_metric,winning_bin FROM settlement_outcomes WHERE authority='VERIFIED' AND winning_bin IS NOT NULL"):
    t=bt(w)
    if t is not None: settled[(c,td,m)]=t
fam=defaultdict(list)
for c,td,m,sct,comp,qj in cur.execute("SELECT city,target_date,temperature_metric,source_cycle_time,computed_at,q_json FROM forecast_posteriors WHERE q_json IS NOT NULL ORDER BY 1,2,3,5"):
    try: q=json.loads(qj)
    except: continue
    g=defaultdict(float)
    for k,v in q.items():
        t=bt(k)
        if t is not None: g[t]+=float(v)
    if g: fam[(c,td,m)].append((pt(comp),pt(sct),dict(g)))
# new vs same cycle drift
tvs=[]; tvn=[]
for key,l in fam.items():
    l.sort(key=lambda x:x[0] or datetime.min.replace(tzinfo=timezone.utc))
    for i in range(len(l)):
        c0,s0,g0=l[i]
        for j in range(i+1,len(l)):
            c1,s1,g1=l[j]
            if not c0 or not c1: continue
            dh=(c1-c0).total_seconds()/3600
            if dh<0.5: continue
            T=set(g0)|set(g1); tv=0.5*sum(abs(g1.get(t,0)-g0.get(t,0)) for t in T)
            if s0 and s1: (tvn if s1!=s0 else tvs).append(tv)
            if dh>=16: break
print(f"same-cycle drift  TV mean={st.mean(tvs):.3f} p90={pctl(tvs,.9):.3f} n={len(tvs)}")
print(f"new-cycle  drift  TV mean={st.mean(tvn):.3f} p90={pctl(tvn,.9):.3f} n={len(tvn)}")
con.close()
