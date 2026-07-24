# READ-ONLY freshness probe Q2 (mode=ro&immutable=1). See 2026-06-12_forecast_freshness_truth.md.
# Run: .venv/bin/python docs/evidence/freshness/q2_ingest_materialization.py  (from repo root)
import sqlite3, statistics as st
from collections import defaultdict
from datetime import datetime, timezone
DB="file:state/zeus-forecasts.db?mode=ro&immutable=1"; LIVE_FROM="2026-06-08T19:00:00+00:00"
def pt(ts):
    if not ts: return None
    d=datetime.fromisoformat(ts); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def hrs(a,b): return None if (a is None or b is None) else (a-b).total_seconds()/3600
def pctl(xs,p):
    if not xs: return None
    xs=sorted(xs); k=(len(xs)-1)*p; f=int(k); c=min(f+1,len(xs)-1); return xs[f]+(xs[c]-xs[f])*(k-f)
con=sqlite3.connect(DB,uri=True); cur=con.cursor()
print("=== Q2A ingest lag (captured-cycle) live single_runs by model ===")
cap=defaultdict(list)
for model,sct,capd in cur.execute("SELECT model,source_cycle_time,captured_at FROM raw_model_forecasts WHERE provider='open-meteo' AND endpoint='single_runs' AND captured_at>=?",(LIVE_FROM,)):
    h=hrs(pt(capd),pt(sct))
    if h is not None and -2<h<240: cap[model].append(h)
for m in sorted(cap,key=lambda k:-len(cap[k])):
    cl=cap[m]; print(f"  {m:32s} n={len(cl):5d} p10={pctl(cl,.1):6.2f} p50={pctl(cl,.5):6.2f} p90={pctl(cl,.9):6.2f}")
print("\n=== Q2B posterior refresh interval (live) ===")
fam=defaultdict(list)
for city,td,m,comp in cur.execute("SELECT city,target_date,temperature_metric,computed_at FROM forecast_posteriors WHERE computed_at>=? ORDER BY 1,2,3,4",(LIVE_FROM,)):
    fam[(city,td,m)].append(pt(comp))
iv=[]
for k,l in fam.items():
    l=sorted(x for x in l if x)
    iv+=[(l[i]-l[i-1]).total_seconds()/3600 for i in range(1,len(l)) if (l[i]-l[i-1]).total_seconds()>0]
print(f"  refresh interval n={len(iv)} p10={pctl(iv,.1):.2f} p50={pctl(iv,.5):.2f} p90={pctl(iv,.9):.2f} max={max(iv):.2f}")
con.close()
