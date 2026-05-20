# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §4 (sha 00c2399742)
"""T1 — book_hash_transitions writer + reader (production pass 2026-05-20).

INV-37 honored: writer takes `*, conn` (sanctioned single-conn path).

Table: book_hash_transitions (world DB, world_class)
  PK: (market_slug, observed_at, transition_seq)
  Writer: write_transition(market_slug, prev_hash, new_hash, observed_at,
                           delta_ms, cycle_id=None, *, conn)
  Reader: read_transitions_by_market(market_slug, since)

Producer wiring:
  src/data/market_scanner.py — PR 6 delta block where
  `_current_hash = snapshot.raw_orderbook_hash` is compared to prev cached
  hash; write_transition called on diff.

  market_slug at the write site = `snapshot.event_slug`
  (= `market.get("slug")`; confirmed via db.py which maps
  market_price_history.market_slug ← snapshot.event_slug).
  condition_id is a DISTINCT column — do NOT pass condition_id as market_slug.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from src.state.schema.book_hash_transitions_schema import SCHEMA_VERSION


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

    No-op if prev_hash == new_hash (no transition occurred).

    transition_seq: derived atomically under caller-provided conn using
    SAVEPOINT + SELECT COALESCE(MAX(transition_seq), 0) + 1 pattern.
    SAVEPOINT provides rollback-on-error atomicity within the caller's
    connection scope; the caller's db_writer_lock serialises cross-process.

    delta_ms: milliseconds since the prior hash for this market.
    cycle_id: live producer cycle context; None for scanner-path rows.
    observed_at: ISO-8601 UTC timestamp (datetime.fromtimestamp(_now_ts,
    tz=timezone.utc).isoformat()).
    """
    if prev_hash == new_hash:
        return

    conn.execute("SAVEPOINT write_transition_sp")
    try:
        row_seq = conn.execute(
            """
            SELECT COALESCE(MAX(transition_seq), 0) + 1
            FROM book_hash_transitions
            WHERE market_slug = ? AND observed_at = ?
            """,
            (market_slug, observed_at),
        ).fetchone()[0]

        conn.execute(
            """
            INSERT INTO book_hash_transitions (
                market_slug, observed_at, transition_seq,
                prev_hash, new_hash,
                delta_ms, cycle_id,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market_slug,
                observed_at,
                row_seq,
                prev_hash,
                new_hash,
                delta_ms,
                cycle_id,
                SCHEMA_VERSION,
            ),
        )
        conn.execute("RELEASE write_transition_sp")
    except Exception:
        conn.execute("ROLLBACK TO write_transition_sp")
        conn.execute("RELEASE write_transition_sp")
        raise


def read_transitions_by_market(
    market_slug: str,
    since: str,
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """Read book_hash_transitions rows for a market since a given ISO-8601 timestamp.

    Returns rows as dicts ordered by (observed_at, transition_seq) ASC.
    conn=None -> opens a read-only world connection (auto-closed after query).
    """
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_world_connection_read_only
        conn = get_world_connection_read_only()

    prior_row_factory = conn.row_factory
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT market_slug, observed_at, transition_seq,
                   prev_hash, new_hash, delta_ms, cycle_id, schema_version
            FROM book_hash_transitions
            WHERE market_slug = ? AND observed_at >= ?
            ORDER BY observed_at ASC, transition_seq ASC
            """,
            (market_slug, since),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    finally:
        conn.row_factory = prior_row_factory
        if own_conn:
            conn.close()
    return rows
