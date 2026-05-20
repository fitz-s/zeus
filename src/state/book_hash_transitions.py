# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3 §4 (sha 99875d4781)
"""T1 SCAFFOLD — book_hash_transitions writer + reader.

Production pass fills bodies (wave-critic reviews SCAFFOLD first).
INV-37 honored: both functions take `*, conn` (sanctioned single-conn path).

Table: book_hash_transitions (world DB, world_class)
  PK: (market_slug, observed_at, transition_seq)
  Writer: write_transition(market_slug, prev_hash, new_hash, observed_at, *, conn)
  Reader: read_transitions_by_market(market_slug, since)

Producer wiring plan (for production pass):
  src/data/market_scanner.py:2170-2186 — PR 6 delta block where
  `_current_hash = snapshot.raw_orderbook_hash` (line 2171) is compared
  to prev cached hash; T1 hook = compare to prev cached hash and call
  `write_transition` on diff.
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def write_transition(
    market_slug: str,
    prev_hash: str,
    new_hash: str,
    observed_at: str,
    *,
    conn: sqlite3.Connection,
) -> None:
    """Insert a book_hash_transitions row for a hash change event.

    INV-37: caller provides `conn` (world DB connection). No internal
    connection open; caller is responsible for transaction semantics.

    T1 SCAFFOLD — production pass fills this body.
    """
    raise NotImplementedError("T1 SCAFFOLD — production pass fills")


def read_transitions_by_market(
    market_slug: str,
    since: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Read book_hash_transitions rows for a market since a given ISO-8601 timestamp.

    Returns rows as dicts ordered by (observed_at, transition_seq) ASC.
    conn=None -> get_world_connection_read_only() (production pass wires this).

    T1 SCAFFOLD — production pass fills this body.
    """
    raise NotImplementedError("T1 SCAFFOLD — production pass fills")
