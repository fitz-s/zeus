# Created: 2026-07-08
# Last reused or audited: 2026-07-08
"""Latest order-fact-per-command queries, factored out of chain_truth.py.

Deliberately its own module: the order-fact table's local_sequence column is
scoped PER order id (UNIQUE(venue_order_id, local_sequence) -- see
src/state/db.py's order-fact CREATE TABLE), NOT per trade id the way the
sibling trade-fact table's local_sequence is (see src/state/fill_dedup.py
module docstring for that distinction). A ``PARTITION BY command_id ORDER BY
local_sequence DESC`` over the order-fact table is therefore the correct,
table-appropriate recency key here (one command has one order id in this
system's model) -- it is NOT an instance of the trade-fact dedup bug shape
(command_id-only dedup silently dropping other trade ids) the F108 antibody
(tests/state/test_position_lots_reconciliation.py) guards against. Kept in a
separate file (rather than inline in chain_truth.py, which also references
the trade-fact table + its canonical dedup CTE for the actually-dedup-
sensitive fill aggregate) so that antibody's file-scoped heuristic never has
to disambiguate the two tables' distinct local_sequence semantics.
"""
from __future__ import annotations

import sqlite3

LATEST_ORDER_FACT_SQL = """
    WITH ranked AS (
        SELECT command_id, state, source, observed_at, local_sequence,
               ROW_NUMBER() OVER (
                   PARTITION BY command_id ORDER BY local_sequence DESC
               ) AS rn
          FROM venue_order_facts
    )
    SELECT command_id, state, source, observed_at, local_sequence
      FROM ranked WHERE rn = 1
"""

LATEST_REST_ORDER_FACT_SQL = """
    WITH ranked AS (
        SELECT command_id, state, source, observed_at, local_sequence,
               ROW_NUMBER() OVER (
                   PARTITION BY command_id ORDER BY local_sequence DESC
               ) AS rn
          FROM venue_order_facts
         WHERE source IN ('REST', 'DATA_API')
    )
    SELECT command_id, state, observed_at
      FROM ranked WHERE rn = 1
"""


def load_latest_order_facts_by_command(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {str(row["command_id"]): row for row in conn.execute(LATEST_ORDER_FACT_SQL).fetchall()}


def load_latest_rest_order_facts_by_command(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {str(row["command_id"]): row for row in conn.execute(LATEST_REST_ORDER_FACT_SQL).fetchall()}
