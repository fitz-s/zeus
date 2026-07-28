# Created: 2026-05-20
# Last reused or audited: 2026-07-28
# Authority basis: immutable executable snapshot evidence and DB growth reduction.
"""Antibodies for reconstructable book-hash history and the legacy transition API.

The immutable ``executable_market_snapshots`` sequence already stores the
condition, capture order, timestamp, and raw orderbook hash. A transition is a
lossless derived view of adjacent snapshot rows; persisting it again is not an
independent evidence source.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime


def test_snapshot_history_reconstructs_hash_transitions() -> None:
    """Adjacent immutable snapshots reproduce every real raw-book change."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            raw_orderbook_hash TEXT NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO executable_market_snapshots VALUES (?, ?, ?, ?)",
        (
            ("s1", "c1", "2026-07-28T08:00:00+00:00", "hash-a"),
            ("s2", "c1", "2026-07-28T08:00:01+00:00", "hash-a"),
            ("s3", "c1", "2026-07-28T08:00:02+00:00", "hash-b"),
            ("s4", "c1", "2026-07-28T08:00:04+00:00", "hash-c"),
            ("other", "c2", "2026-07-28T08:00:03+00:00", "hash-z"),
        ),
    )

    transitions = conn.execute(
        """
        WITH ordered AS (
            SELECT condition_id, captured_at, raw_orderbook_hash AS new_hash,
                   LAG(raw_orderbook_hash) OVER (
                       PARTITION BY condition_id
                       ORDER BY captured_at, snapshot_id
                   ) AS prev_hash,
                   LAG(captured_at) OVER (
                       PARTITION BY condition_id
                       ORDER BY captured_at, snapshot_id
                   ) AS prev_captured_at
              FROM executable_market_snapshots
        )
        SELECT condition_id, prev_hash, new_hash, prev_captured_at, captured_at
          FROM ordered
         WHERE prev_hash IS NOT NULL AND prev_hash <> new_hash
         ORDER BY condition_id, captured_at
        """
    ).fetchall()

    assert [
        (row["condition_id"], row["prev_hash"], row["new_hash"])
        for row in transitions
    ] == [
        ("c1", "hash-a", "hash-b"),
        ("c1", "hash-b", "hash-c"),
    ]
    assert [
        int(
            (
                datetime.fromisoformat(row["captured_at"])
                - datetime.fromisoformat(row["prev_captured_at"])
            ).total_seconds()
            * 1000
        )
        for row in transitions
    ] == [1000, 2000]
    conn.close()


def test_write_read_roundtrip_integration() -> None:
    """Roundtrip: write_transition + read_transitions_by_market on in-memory DB.

    Uses ensure_table to set up schema; exercises the full writer/reader path
    without requiring the live world DB.
    """
    from src.state.book_hash_transitions import read_transitions_by_market, write_transition
    from src.state.schema.book_hash_transitions_schema import SCHEMA_VERSION, ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)

    write_transition(
        market_slug="test-market-slug",
        prev_hash="aaa",
        new_hash="bbb",
        observed_at="2026-05-21T00:00:00+00:00",
        delta_ms=1500,
        cycle_id=None,
        conn=conn,
    )

    rows = read_transitions_by_market(
        "test-market-slug",
        since="2026-05-20T00:00:00+00:00",
        conn=conn,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["market_slug"] == "test-market-slug"
    assert row["prev_hash"] == "aaa"
    assert row["new_hash"] == "bbb"
    assert row["transition_seq"] == 1
    assert row["delta_ms"] == 1500
    assert row["cycle_id"] is None
    assert row["schema_version"] == SCHEMA_VERSION

    conn.close()


def test_write_transition_noop_on_same_hash() -> None:
    """write_transition with prev_hash == new_hash must insert nothing."""
    from src.state.book_hash_transitions import write_transition
    from src.state.schema.book_hash_transitions_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)

    write_transition(
        market_slug="test-market",
        prev_hash="same",
        new_hash="same",
        observed_at="2026-05-21T00:00:00+00:00",
        delta_ms=0,
        conn=conn,
    )

    count = conn.execute("SELECT COUNT(*) FROM book_hash_transitions").fetchone()[0]
    assert count == 0, "no-op: prev_hash == new_hash must not insert"
    conn.close()


def test_write_transition_seq_increments() -> None:
    """Two transitions at the same (market_slug, observed_at) get seq 1 and 2."""
    from src.state.book_hash_transitions import write_transition
    from src.state.schema.book_hash_transitions_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)

    ts = "2026-05-21T00:00:00+00:00"
    write_transition("m", "a", "b", ts, delta_ms=100, conn=conn)
    write_transition("m", "b", "c", ts, delta_ms=200, conn=conn)

    rows = conn.execute(
        "SELECT transition_seq, prev_hash, new_hash FROM book_hash_transitions ORDER BY transition_seq"
    ).fetchall()
    assert rows[0] == (1, "a", "b")
    assert rows[1] == (2, "b", "c")
    conn.close()
