# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §4 (sha 00c2399742)
"""T1 SCAFFOLD — book_hash_transitions writer + reader.

Production pass fills bodies (wave-critic reviews SCAFFOLD first).
INV-37 honored: writer takes `*, conn` (sanctioned single-conn path).

Table: book_hash_transitions (world DB, world_class)
  PK: (market_slug, observed_at, transition_seq)
  Writer: write_transition(market_slug, prev_hash, new_hash, observed_at,
                           delta_ms, cycle_id=None, *, conn)
  Reader: read_transitions_by_market(market_slug, since)

Producer wiring plan (for production pass):
  src/data/market_scanner.py:2170-2186 — PR 6 delta block where
  `_current_hash = snapshot.raw_orderbook_hash` (line 2171) is compared
  to prev cached hash; T1 hook = compare to prev cached hash and call
  `write_transition` on diff.

  market_slug at the write site = `snapshot.event_slug`
  (= `market.get("slug")` set at line 2133; confirmed via db.py:4492 which
  maps market_price_history.market_slug ← snapshot.event_slug).
  condition_id is a DISTINCT column — do NOT pass condition_id as market_slug.
"""
from __future__ import annotations

import sqlite3
from typing import Optional


def write_transition(
    market_slug: str,
    prev_hash: str,
    new_hash: str,
    observed_at: str,
    delta_ms: int,
    cycle_id: Optional[str] = None,
    *,
    conn: sqlite3.Connection,
) -> None:
    """Insert a book_hash_transitions row for a hash change event.

    INV-37: caller provides `conn` (world DB connection). No internal
    connection open; caller is responsible for transaction semantics.

    transition_seq: derived atomically under caller-provided conn using
    SAVEPOINT + SELECT MAX(transition_seq) + 1 pattern (no SQLite
    SELECT FOR UPDATE; SAVEPOINT is the SQLite-native atomicity mechanism).

    delta_ms: milliseconds since the prior hash for this market (from
    _hash_delta_ms at market_scanner.py:2178).
    cycle_id: live producer cycle context; None for backfill rows.
    observed_at: ISO-8601 UTC timestamp from _now_ts at the write site
    (use datetime.fromtimestamp(_now_ts, tz=timezone.utc).isoformat(),
    NOT datetime.utcnow().isoformat() — avoids write-lag drift).

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
