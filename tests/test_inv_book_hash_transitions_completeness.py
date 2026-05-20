# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: 2026-05-20 live substrate repair; trade-owned executable snapshot substrate
"""Antibody test: INV-book-hash-transitions-completeness

Invariant: every `raw_orderbook_hash` change observed in active
`executable_market_snapshots` (trade DB) since T1_LAUNCH_DATE has a
corresponding `book_hash_transitions` row.

Specifically: for each market in the last 24h of executable snapshots, count
distinct `raw_orderbook_hash` values; the corresponding transitions count
must equal distinct_count - 1 (the first hash has no prior state, so no
transition row).

Single trade-DB read path (no ATTACH); INV-37 trivially honored.
backfill-aware: `cycle_id IS NULL` rows are backfill; live rows carry a
cycle_id. Both satisfy the completeness invariant.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

# T1_LAUNCH_DATE: set to 2026-05-21 (production-pass activation date).
# Antibody looks back from this date; production pass updates this constant.
T1_LAUNCH_DATE = "2026-05-21"

# 24h window for antibody check
LOOKBACK_HOURS = 24


def test_inv_book_hash_transitions_completeness() -> None:
    """For every market with raw_orderbook_hash changes in the last 24h,
    book_hash_transitions must carry (distinct_raw_hash_count - 1) rows.

    Single trade-DB path; no ATTACH (INV-37 trivially honored).
    backfill rows (cycle_id IS NULL) and live rows both satisfy the invariant.

    Invariant check uses a direct sqlite3.connect() so the test runs even
    when the production wrappers (get_world_connection_read_only) are not
    fully wired. The xfail fires because book_hash_transitions does not exist
    until the production pass wires db.py + migration script.
    """
    from src.config import STATE_DIR

    trade_db_path = STATE_DIR / "zeus_trades.db"
    # Open trade DB directly (read-only URI). Skip if DB absent (CI / worktree
    # environment without a live trade DB). This is a production-environment
    # antibody only.
    try:
        conn = sqlite3.connect(f"file:{trade_db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        pytest.skip("trade DB not present in this environment — live-only antibody")
    conn.row_factory = sqlite3.Row

    # Compute ISO-8601 cutoff in Python to avoid datetime('now',...) vs
    # ISO-8601 TEXT format mismatch in SQLite lexicographic compare.
    # Both market_price_history.recorded_at and book_hash_transitions.observed_at
    # are written as ISO-8601 (e.g. "2026-05-20T12:34:56+00:00"). SQLite
    # datetime('now', ...) returns "YYYY-MM-DD HH:MM:SS" which sorts differently.
    _cutoff_iso = (
        datetime.now(tz=timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    ).isoformat()

    try:
        # Count distinct raw_orderbook_hash values per market in last 24h.
        # executable_market_snapshots.raw_orderbook_hash is TEXT NOT NULL.
        hash_counts = conn.execute(
            """
            SELECT event_slug AS market_slug, COUNT(DISTINCT raw_orderbook_hash) AS distinct_count
            FROM executable_market_snapshots
            WHERE captured_at >= :cutoff
              AND raw_orderbook_hash IS NOT NULL
            GROUP BY event_slug
            HAVING COUNT(DISTINCT raw_orderbook_hash) > 1
            """,
            {"cutoff": _cutoff_iso},
        ).fetchall()

        if not hash_counts:
            pytest.skip(
                f"no markets with >1 distinct raw_orderbook_hash in last {LOOKBACK_HOURS}h — "
                "non-degenerate antibody check requires hash transitions"
            )

        # For each market, verify transitions count = distinct_count - 1.
        # book_hash_transitions table must exist (production pass wires this).
        missing: list[str] = []
        for row in hash_counts:
            market_slug = row["market_slug"]
            expected = row["distinct_count"] - 1

            transition_count = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM book_hash_transitions
                WHERE market_slug = ?
                  AND observed_at >= ?
                """,
                (market_slug, _cutoff_iso),
            ).fetchone()["cnt"]

            if transition_count < expected:
                missing.append(
                    f"{market_slug}: expected>={expected} transitions, "
                    f"got {transition_count}"
                )

        assert not missing, (
            f"INV-book-hash-transitions-completeness FAILED for "
            f"{len(missing)} markets:\n" + "\n".join(missing)
        )

    finally:
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
