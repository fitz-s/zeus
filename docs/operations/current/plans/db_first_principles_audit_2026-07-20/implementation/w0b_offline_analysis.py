#!/usr/bin/env python3
"""W0-b offline proof (READ-ONLY, live trades DB): compare the OLD lot-repair candidate
predicate (gated on the fail-soft trade_decisions projection) vs the NEW predicate
(gated on canonical fill authority only). Removing a restrictive clause can only ADD
candidates, so OLD must be a subset of NEW; NEW\\OLD is exactly the post-07-02 gap the
re-anchor is meant to repair. Proves no over-repair and characterizes the delta.
Not in the live process; one-shot diagnostic; no writes."""
import sqlite3

DB = "/Users/leofitz/zeus/state/zeus_trades.db"

CANONICAL_CTE = """
        canonical_trade_fact AS (
            SELECT ranked.* FROM (
                SELECT scored.*, ROW_NUMBER() OVER (
                    PARTITION BY command_id, trade_id
                    ORDER BY proof_rank DESC, local_sequence DESC) AS canonical_rank
                FROM (
                    SELECT fact.*, CASE
                        WHEN UPPER(COALESCE(fact.state,''))='CONFIRMED' AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 500
                        WHEN UPPER(COALESCE(fact.state,''))='MINED' AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 450
                        WHEN UPPER(COALESCE(fact.state,''))='MATCHED' AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 400
                        WHEN CAST(COALESCE(fact.filled_size,'0') AS REAL)>0 THEN 300 ELSE 100 END AS proof_rank
                    FROM venue_trade_facts fact) scored) ranked
            WHERE ranked.canonical_rank=1)
"""

MAIN = """
        SELECT cmd.command_id, cmd.position_id, cmd.decision_id, cmd.token_id,
               fact.trade_fact_id, fact.trade_id, fact.state AS trade_state,
               fact.filled_size, fact.observed_at
          FROM venue_commands cmd
          LEFT JOIN position_current pc ON pc.position_id = cmd.position_id
          JOIN canonical_trade_fact fact ON fact.command_id = cmd.command_id
          LEFT JOIN position_lots lot ON lot.source_trade_fact_id = fact.trade_fact_id
         WHERE cmd.intent_kind='ENTRY' AND cmd.side='BUY' AND cmd.state='FILLED'
           AND fact.state IN ('MATCHED','MINED','CONFIRMED')
           AND CAST(COALESCE(fact.filled_size,'0') AS REAL)>0
           AND CAST(COALESCE(fact.fill_price,'0') AS REAL)>0
           AND lot.lot_id IS NULL
           {TRADE_DECISIONS_GATE}
           AND NOT EXISTS (
               SELECT 1 FROM position_lots trade_lot
                 JOIN venue_trade_facts lot_fact ON lot_fact.trade_fact_id = trade_lot.source_trade_fact_id
                WHERE lot_fact.command_id = fact.command_id AND lot_fact.trade_id = fact.trade_id
                  AND trade_lot.state IN ('OPTIMISTIC_EXPOSURE','CONFIRMED_EXPOSURE'))
"""

OLD_GATE = """AND EXISTS (SELECT 1 FROM trade_decisions td
               WHERE td.runtime_trade_id = cmd.position_id
                  OR CAST(td.trade_id AS TEXT) = cmd.position_id
                  OR CAST(td.trade_id AS TEXT) = cmd.decision_id)"""

con = sqlite3.connect(f"file:{DB}?mode=ro&cache=private", uri=True, timeout=0.25, isolation_level=None)
con.execute("PRAGMA query_only=ON"); con.execute("PRAGMA busy_timeout=250"); con.execute("PRAGMA mmap_size=0")

def candidates(gate):
    sql = "WITH " + CANONICAL_CTE + MAIN.replace("{TRADE_DECISIONS_GATE}", gate)
    return {r[0]: r for r in con.execute(sql).fetchall()}  # keyed by command_id

old = candidates(OLD_GATE)
new = candidates("")   # no trade_decisions gate
old_ids, new_ids = set(old), set(new)

print(f"OLD candidates (trade_decisions-gated): {len(old_ids)}")
print(f"NEW candidates (fill-authority only):   {len(new_ids)}")
print(f"OLD \\ NEW (must be EMPTY — relaxing a filter cannot drop rows): {len(old_ids - new_ids)}")
print(f"NEW \\ OLD (newly repairable = the gap): {len(new_ids - old_ids)}")

gap = new_ids - old_ids
# characterize the newly-admitted candidates: are they legitimate real fills?
print("\nNewly-admitted (NEW-only) candidates — verify each is a real FILLED ENTRY with a canonical fill:")
for cid in list(gap)[:40]:
    r = new[cid]
    # r: command_id, position_id, decision_id, token_id, trade_fact_id, trade_id, trade_state, filled_size, observed_at
    print(f"  cmd={r[0][:12]} pos={str(r[1])[:12]} fact={r[4]} state={r[6]} filled={r[7]} observed={r[8]}")
if len(gap) > 40:
    print(f"  ... +{len(gap)-40} more")

# safety: does any NEW-only candidate lack a canonical positive fill? (should be impossible by construction)
bad = [cid for cid in gap if new[cid][6] not in ('MATCHED','MINED','CONFIRMED') or float(new[cid][7] or 0) <= 0]
print(f"\nNEW-only candidates WITHOUT a canonical positive fill (over-repair risk; must be 0): {len(bad)}")
con.close()
