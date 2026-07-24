import re, sqlite3, json
from collections import defaultdict
from datetime import datetime, timezone
DB="file:/Users/leofitz/zeus/state/zeus-forecasts.db?mode=ro&immutable=1"
con=sqlite3.connect(DB,uri=True); cur=con.cursor()
# For one family with many posteriors, show how q-mean and consumed cycle evolve
BIN=re.compile(r"(-?\d+)\s*°?C")
def gridmean(qjson):
    q=json.loads(qjson); g=defaultdict(float)
    for k,v in q.items():
        m=BIN.search(k)
        if m: g[int(m.group(1))]+=float(v)
    s=sum(g.values());
    return (sum(t*p for t,p in g.items())/s) if s>0 else None
rows=cur.execute("SELECT computed_at, source_cycle_time, dependency_source_run_ids_json, q_json FROM forecast_posteriors WHERE city='Shanghai' AND target_date='2026-06-12' AND temperature_metric='high' ORDER BY computed_at").fetchall()
print("Shanghai 2026-06-12 high — posterior evolution:")
print(f"  {'computed_at':26s} {'consumed_cycle':20s} {'q_mean°C':>8s} {'#deps':>5s}")
prev=None
for comp,sct,deps,qj in rows:
    mu=gridmean(qj); nd=len(json.loads(deps)) if deps else 0
    dmu = f"{mu-prev:+.2f}" if (prev is not None and mu is not None) else ""
    print(f"  {comp:26s} {str(sct):20s} {mu:8.2f} {nd:5d}  {dmu}")
    prev=mu
con.close()
