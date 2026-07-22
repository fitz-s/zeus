#!/usr/bin/env python3
"""Read-only: split the NEW lot-repair candidates by whether they have a position_current
row (primary exposure) and by phase — to decide whether missing position_lots is a live
exposure undercount or a secondary-ledger gap."""
import argparse
import sqlite3
import sys
from pathlib import Path

CTE = """canonical_trade_fact AS (
  SELECT ranked.* FROM (
    SELECT scored.*, ROW_NUMBER() OVER (PARTITION BY command_id, trade_id
        ORDER BY proof_rank DESC, local_sequence DESC) AS canonical_rank
    FROM (
      SELECT fact.*, CASE
        WHEN UPPER(COALESCE(fact.state,''))='CONFIRMED' AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 500
        WHEN UPPER(COALESCE(fact.state,''))='MINED'     AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 450
        WHEN UPPER(COALESCE(fact.state,''))='MATCHED'   AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 400
        WHEN CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 300 ELSE 100 END AS proof_rank
      FROM venue_trade_facts fact) scored) ranked
  WHERE ranked.canonical_rank=1)"""

BASE = """
FROM venue_commands cmd
LEFT JOIN position_current pc ON pc.position_id = cmd.position_id
JOIN canonical_trade_fact fact ON fact.command_id = cmd.command_id
LEFT JOIN position_lots lot ON lot.source_trade_fact_id = fact.trade_fact_id
WHERE cmd.intent_kind='ENTRY' AND cmd.side='BUY' AND cmd.state='FILLED'
  AND fact.state IN ('MATCHED','MINED','CONFIRMED')
  AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0
  AND CAST(COALESCE(fact.fill_price,'0') AS REAL)>0
  AND lot.lot_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM position_lots tl
       JOIN venue_trade_facts lf ON lf.trade_fact_id=tl.source_trade_fact_id
       WHERE lf.command_id=fact.command_id AND lf.trade_id=fact.trade_id
         AND tl.state IN ('OPTIMISTIC_EXPOSURE','CONFIRMED_EXPOSURE'))"""

ap = argparse.ArgumentParser(description="W0-b exposure probe (read-only).")
ap.add_argument("db_path", help="Path to the trades DB, e.g. state/zeus_trades.db. No default — "
                                 "this probe must never silently point at one operator's machine.")
args = ap.parse_args()
db_path = Path(args.db_path).resolve()
if not db_path.exists():
    sys.exit(f"REFUSED: DB not found: {db_path}")

con = sqlite3.connect(f"file:{db_path}?mode=ro&cache=private",
                      uri=True, timeout=0.25, isolation_level=None)
con.execute("PRAGMA query_only=ON"); con.execute("PRAGMA busy_timeout=250"); con.execute("PRAGMA mmap_size=0")
print(f"DB: {db_path}")
print(f"sqlite_source_id(): {con.execute('SELECT sqlite_source_id()').fetchone()[0]}")

split = con.execute(
    "WITH " + CTE + " SELECT "
    "SUM(CASE WHEN pc.position_id IS NOT NULL THEN 1 ELSE 0 END) AS with_pc, "
    "SUM(CASE WHEN pc.position_id IS NULL THEN 1 ELSE 0 END) AS without_pc, "
    "COUNT(*) AS total " + BASE).fetchone()
print(f"NEW candidates: total={split[2]}  with position_current={split[0]}  WITHOUT position_current={split[1]}")

print("phase distribution (candidates WITH position_current — exposure carried there):")
for r in con.execute("WITH " + CTE + " SELECT pc.phase, COUNT(*) " + BASE +
                     " AND pc.position_id IS NOT NULL GROUP BY pc.phase ORDER BY 2 DESC").fetchall():
    print(f"   phase={r[0]!r}: {r[1]}")

# the without-pc set is exactly what riskguard's _unprojected_entry_fill_equity_usd nets
print(f"\nWITHOUT position_current = {split[1]} — these are caught by riskguard "
      "_unprojected_entry_fill_equity_usd (valued from the fill fact).")
con.close()
