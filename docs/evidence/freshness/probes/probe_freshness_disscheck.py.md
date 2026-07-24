# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator freshness-investigation directive 2026-06-12. READ-ONLY,
#   mode=ro&immutable=1, SELECT-only. Verifies dissemination-timestamp semantics.
import sqlite3, statistics
from datetime import datetime, timezone
from collections import Counter

con = sqlite3.connect("file:state/zeus-forecasts.db?mode=ro&immutable=1", uri=True)
cur = con.cursor()

def p(ts):
    if not ts:
        return None
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

print("=== (source_available_at - source_cycle_time) live single_runs ===")
c = Counter()
for sct, sav in cur.execute("SELECT source_cycle_time, source_available_at FROM raw_model_forecasts WHERE provider='open-meteo' AND endpoint='single_runs' AND captured_at>='2026-06-08T19:00:00+00:00'"):
    a, b = p(sct), p(sav)
    if a and b:
        c[round((b - a).total_seconds() / 3600, 2)] += 1
for k in sorted(c):
    print(f"   +{k:6.2f}h : {c[k]}")

print("\n=== (captured_at - source_available_at) live single_runs ===")
xs = []
for sav, cap in cur.execute("SELECT source_available_at, captured_at FROM raw_model_forecasts WHERE provider='open-meteo' AND endpoint='single_runs' AND captured_at>='2026-06-08T19:00:00+00:00'"):
    a, b = p(sav), p(cap)
    if a and b:
        xs.append((b - a).total_seconds() / 3600)
xs.sort()
print(f"   n={len(xs)} min={xs[0]:.2f} p50={statistics.median(xs):.2f} p90={xs[len(xs)*9//10]:.2f} max={xs[-1]:.2f}")

print("\n=== newest cycle captured per (model, capture-day) single_runs ===")
for r in cur.execute("SELECT model, substr(captured_at,1,10) day, MAX(source_cycle_time), COUNT(DISTINCT source_cycle_time) FROM raw_model_forecasts WHERE provider='open-meteo' AND endpoint='single_runs' AND captured_at>='2026-06-10T00:00:00+00:00' AND model IN ('ecmwf_ifs','gfs_global','icon_global') GROUP BY model, day ORDER BY model, day"):
    print("  ", r)
con.close()
