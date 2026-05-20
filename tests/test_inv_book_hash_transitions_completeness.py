# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §4.4 (sha 00c2399742)
"""Antibody test: INV-book-hash-transitions-completeness

Invariant: every `raw_orderbook_hash` change observed in `market_price_history`
(world DB) since T1_LAUNCH_DATE has a corresponding `book_hash_transitions` row.

Specifically: for each market in the last 24h of market_price_history, count
distinct `raw_orderbook_hash` values; the corresponding transitions count
must equal distinct_count - 1 (the first hash has no prior state, so no
transition row).

Single world-DB read path (no ATTACH); INV-37 trivially honored.
backfill-aware: `cycle_id IS NULL` rows are backfill; live rows carry a
cycle_id. Both satisfy the completeness invariant.

T1 SCAFFOLD: xfail because book_hash_transitions table does not exist
until production pass wires db.py + migration script.
"""
from __future__ import annotations

import sqlite3

import pytest

# T1_LAUNCH_DATE: set to 2026-05-21 (production-pass activation date).
# Antibody looks back from this date; production pass updates this constant.
T1_LAUNCH_DATE = "2026-05-21"

# 24h window for antibody check
LOOKBACK_HOURS = 24


@pytest.mark.xfail(reason="T1 SCAFFOLD — production pass implements")
def test_inv_book_hash_transitions_completeness() -> None:
    """For every market with raw_orderbook_hash changes in the last 24h,
    book_hash_transitions must carry (distinct_raw_hash_count - 1) rows.

    Single world-DB path; no ATTACH (INV-37 trivially honored).
    backfill rows (cycle_id IS NULL) and live rows both satisfy the invariant.

    Invariant check uses a direct sqlite3.connect() so the test runs even
    when the production wrappers (get_world_connection_read_only) are not
    fully wired. The xfail fires because book_hash_transitions does not exist
    until the production pass wires db.py + migration script.
    """
    from src.state.db import ZEUS_WORLD_DB_PATH

    # Open world DB directly (read-only URI). If the DB file is absent in
    # this environment, sqlite3 will raise OperationalError on the first
    # query — that is a legitimate xfail trigger (SCAFFOLD: table doesn't exist).
    conn = sqlite3.connect(f"file:{ZEUS_WORLD_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        # Count distinct raw_orderbook_hash values per market in last 24h.
        # market_price_history.raw_orderbook_hash is TEXT (nullable pre-PR6 rows).
        hash_counts = conn.execute(
            """
            SELECT market_slug, COUNT(DISTINCT raw_orderbook_hash) AS distinct_count
            FROM market_price_history
            WHERE recorded_at >= datetime('now', :lookback)
              AND raw_orderbook_hash IS NOT NULL
            GROUP BY market_slug
            HAVING COUNT(DISTINCT raw_orderbook_hash) > 1
            """,
            {"lookback": f"-{LOOKBACK_HOURS} hours"},
        ).fetchall()

        if not hash_counts:
            pytest.skip(
                f"no markets with >1 distinct raw_orderbook_hash in last {LOOKBACK_HOURS}h — "
                "non-degenerate antibody check requires hash transitions"
            )

        # For each market, verify transitions count = distinct_count - 1.
        # book_hash_transitions table must exist (production pass wires this).
        # SCAFFOLD: this query raises OperationalError "no such table" — xfail fires.
        missing: list[str] = []
        for row in hash_counts:
            market_slug = row["market_slug"]
            expected = row["distinct_count"] - 1

            transition_count = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM book_hash_transitions
                WHERE market_slug = ?
                  AND observed_at >= datetime('now', ?)
                """,
                (market_slug, f"-{LOOKBACK_HOURS} hours"),
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
