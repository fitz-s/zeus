# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator freshness-investigation directive 2026-06-12. READ-ONLY,
#   mode=ro&immutable=1, SELECT-only. Q3 exploration: q_json shape + settled overlap.
import sqlite3, json
from datetime import datetime, timezone

con = sqlite3.connect("file:state/zeus-forecasts.db?mode=ro&immutable=1", uri=True)
cur = con.cursor()

print("=== forecast_posteriors: method + authority distribution ===")
for r in cur.execute("SELECT posterior_method, trade_authority_status, COUNT(*) FROM forecast_posteriors GROUP BY 1,2 ORDER BY 3 DESC"):
    print("  ", r)

print("\n=== sample q_json ===")
row = cur.execute("SELECT city, target_date, temperature_metric, source_cycle_time, computed_at, q_json FROM forecast_posteriors WHERE q_json IS NOT NULL ORDER BY computed_at DESC LIMIT 1").fetchone()
print("  key:", row[:5])
q = json.loads(row[5])
print("  q_json type:", type(q).__name__)
if isinstance(q, dict):
    items = list(q.items())[:6]
    print("  first bins:", items)
    print("  sum:", sum(q.values()))

print("\n=== settlement_outcomes sample + winning_bin format ===")
for r in cur.execute("SELECT city, target_date, temperature_metric, winning_bin, settlement_value, authority FROM settlement_outcomes WHERE authority='VERIFIED' ORDER BY target_date DESC LIMIT 5"):
    print("  ", r)

print("\n=== JOIN overlap: posteriors with settled VERIFIED outcome ===")
n = cur.execute("""
  SELECT COUNT(*) FROM forecast_posteriors p
  JOIN settlement_outcomes s
    ON p.city=s.city AND p.target_date=s.target_date AND p.temperature_metric=s.temperature_metric
  WHERE p.q_json IS NOT NULL AND s.authority='VERIFIED' AND s.winning_bin IS NOT NULL
""").fetchone()[0]
print("  joined posterior×settlement rows:", n)

print("\n=== distinct settled (city,target,metric) cells that also have a posterior ===")
n2 = cur.execute("""
  SELECT COUNT(DISTINCT p.city||'|'||p.target_date||'|'||p.temperature_metric)
  FROM forecast_posteriors p
  JOIN settlement_outcomes s
    ON p.city=s.city AND p.target_date=s.target_date AND p.temperature_metric=s.temperature_metric
  WHERE p.q_json IS NOT NULL AND s.authority='VERIFIED' AND s.winning_bin IS NOT NULL
""").fetchone()[0]
print("  distinct settled families with >=1 posterior:", n2)

print("\n=== authority values present in settlement_outcomes ===")
for r in cur.execute("SELECT authority, COUNT(*) FROM settlement_outcomes GROUP BY 1"):
    print("  ", r)
con.close()
