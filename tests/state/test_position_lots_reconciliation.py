# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: F20 reconciliation investigation (F20_RECONCILIATION_REPORT.md)
#
# Invariant antibody for position_lots <-> position_current reconciliation.
#
# Three invariant classes:
#   INV-LOTS-1  Naive join probe is unreliable — detects false orphans on healthy data
#   INV-LOTS-2  Lot shares must be read via latest-sequence dedup (append-only log)
#   INV-LOTS-3  Every lot links through trade_decisions to a valid position_current row
#
# Live-drift tests (@pytest.mark.live_drift) run the same checks against
# state/zeus_trades.db. They are NOT in default CI (require live DB access).
# Run manually: pytest -m live_drift tests/state/test_position_lots_reconciliation.py

import pytest
import sqlite3
import os
from decimal import Decimal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_db(tmp_path):
    """
    Minimal in-memory DB with:
      - position_current (2 UUID positions)
      - trade_decisions  (bridge: INT trade_id -> UUID runtime_trade_id)
      - position_lots    (append-only: OPTIMISTIC then CONFIRMED rows per fill)

    Deliberately healthy: no corrupt data.
    """
    db_path = tmp_path / "test_lots.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            shares REAL NOT NULL
        );

        CREATE TABLE trade_decisions (
            trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            market_id  TEXT NOT NULL,
            runtime_trade_id TEXT
        );

        CREATE TABLE position_lots (
            lot_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id      INTEGER NOT NULL,
            state            TEXT NOT NULL,
            shares           TEXT NOT NULL,
            local_sequence   INTEGER NOT NULL,
            UNIQUE (position_id, local_sequence)
        );
    """)

    # Position A: active, 35.6 shares
    conn.execute(
        "INSERT INTO position_current VALUES (?,?,?)",
        ("aaaa-0001", "active", 35.6),
    )
    conn.execute(
        "INSERT INTO trade_decisions (market_id, runtime_trade_id) VALUES (?,?)",
        ("market-A", "aaaa-0001"),
    )
    td_a = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Append-only: OPTIMISTIC then CONFIRMED (same shares, higher local_sequence)
    conn.execute(
        "INSERT INTO position_lots (position_id, state, shares, local_sequence) VALUES (?,?,?,?)",
        (td_a, "OPTIMISTIC_EXPOSURE", "35.6", 1),
    )
    conn.execute(
        "INSERT INTO position_lots (position_id, state, shares, local_sequence) VALUES (?,?,?,?)",
        (td_a, "CONFIRMED_EXPOSURE", "35.6", 2),
    )

    # Position B: economically_closed, 6.0 shares
    conn.execute(
        "INSERT INTO position_current VALUES (?,?,?)",
        ("bbbb-0002", "economically_closed", 6.0),
    )
    conn.execute(
        "INSERT INTO trade_decisions (market_id, runtime_trade_id) VALUES (?,?)",
        ("market-B", "bbbb-0002"),
    )
    td_b = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO position_lots (position_id, state, shares, local_sequence) VALUES (?,?,?,?)",
        (td_b, "OPTIMISTIC_EXPOSURE", "6.0", 1),
    )
    conn.execute(
        "INSERT INTO position_lots (position_id, state, shares, local_sequence) VALUES (?,?,?,?)",
        (td_b, "CONFIRMED_EXPOSURE", "6.0", 2),
    )

    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# INV-LOTS-1: Naive join probe produces false orphans on healthy data
# ---------------------------------------------------------------------------

class TestNaiveJoinIsUnreliable:
    """
    Demonstrates that CAST(pl.position_id AS TEXT) = pc.position_id
    always returns false when lots use INTEGER ids and positions use UUID strings.

    This test exists to prevent future agents from reusing the broken probe.
    A clean DB must produce false-orphan counts > 0 with the naive probe.
    """

    def test_naive_cast_probe_reports_false_orphans(self, seeded_db):
        """Naive CAST join on healthy DB reports all lots as orphans."""
        conn = seeded_db
        rows = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM position_lots pl
            WHERE NOT EXISTS (
                SELECT 1 FROM position_current pc
                WHERE pc.position_id = CAST(pl.position_id AS TEXT)
            )
        """).fetchone()
        # On a healthy DB the naive probe still reports all lots as orphans
        # because INTEGER position_ids never equal UUID strings.
        assert rows["cnt"] == 4, (
            "Naive CAST probe must report all 4 lots as orphans on healthy DB; "
            "this validates that the naive probe is broken by construction."
        )

    def test_bridge_join_reports_zero_orphans(self, seeded_db):
        """Correct 3-table bridge reports 0 orphans on same healthy DB."""
        conn = seeded_db
        rows = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM position_lots pl
            WHERE NOT EXISTS (
                SELECT 1
                FROM trade_decisions td
                JOIN position_current pc ON pc.position_id = td.runtime_trade_id
                WHERE td.trade_id = pl.position_id
            )
        """).fetchone()
        assert rows["cnt"] == 0, (
            "Bridge join must report 0 orphans on healthy DB."
        )


# ---------------------------------------------------------------------------
# INV-LOTS-2: Shares aggregation requires latest-sequence dedup
# ---------------------------------------------------------------------------

class TestLotSharesAggregation:
    """
    position_lots is append-only. Each fill produces:
      local_sequence=1  OPTIMISTIC_EXPOSURE  shares=X
      local_sequence=2  CONFIRMED_EXPOSURE   shares=X

    Naive SUM(shares) double-counts. Correct read: MAX(local_sequence) per position_id.
    """

    def test_naive_sum_double_counts(self, seeded_db):
        """SUM(shares) without dedup returns 2× actual shares."""
        conn = seeded_db
        rows = conn.execute("""
            SELECT pc.position_id, pc.shares as pc_shares,
                   SUM(CAST(pl.shares AS REAL)) as naive_sum
            FROM position_current pc
            JOIN trade_decisions td ON td.runtime_trade_id = pc.position_id
            JOIN position_lots pl ON pl.position_id = td.trade_id
            GROUP BY pc.position_id
        """).fetchall()
        for row in rows:
            assert abs(row["naive_sum"] - 2 * row["pc_shares"]) < 0.0001, (
                f"Position {row['position_id']}: naive SUM={row['naive_sum']} "
                f"should equal 2×pc_shares={row['pc_shares']} (double-count)."
            )

    def test_latest_sequence_dedup_matches_pc_shares(self, seeded_db):
        """Latest-sequence dedup produces shares == pc.shares."""
        conn = seeded_db
        rows = conn.execute("""
            SELECT pc.position_id, pc.shares as pc_shares,
                   CAST(lot.shares AS REAL) as lot_shares
            FROM position_current pc
            JOIN trade_decisions td ON td.runtime_trade_id = pc.position_id
            JOIN position_lots lot ON lot.position_id = td.trade_id
            JOIN (
                SELECT position_id, MAX(local_sequence) AS max_seq
                FROM position_lots
                GROUP BY position_id
            ) latest
              ON latest.position_id = lot.position_id
             AND latest.max_seq = lot.local_sequence
        """).fetchall()
        assert len(rows) == 2, "Expected 2 positions in healthy fixture."
        for row in rows:
            assert abs(row["lot_shares"] - row["pc_shares"]) < 0.0001, (
                f"Position {row['position_id']}: latest-dedup lot_shares="
                f"{row['lot_shares']} != pc_shares={row['pc_shares']}."
            )


# ---------------------------------------------------------------------------
# INV-LOTS-3: Every lot bridges to a valid position_current row
# ---------------------------------------------------------------------------

class TestLotPositionBridgeIntegrity:
    """
    Every row in position_lots must have:
      1. A parent trade_decisions row (trade_id match)
      2. That row's runtime_trade_id exists in position_current

    Voided positions are excluded — they correctly have no lots.
    """

    def test_every_lot_has_trade_decisions_parent(self, seeded_db):
        conn = seeded_db
        rows = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM position_lots pl
            WHERE NOT EXISTS (
                SELECT 1 FROM trade_decisions td WHERE td.trade_id = pl.position_id
            )
        """).fetchone()
        assert rows["cnt"] == 0, (
            "All lots must have a parent trade_decisions row."
        )

    def test_every_lot_bridges_to_position_current(self, seeded_db):
        conn = seeded_db
        rows = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM position_lots pl
            JOIN trade_decisions td ON td.trade_id = pl.position_id
            WHERE td.runtime_trade_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM position_current pc
                  WHERE pc.position_id = td.runtime_trade_id
              )
        """).fetchone()
        assert rows["cnt"] == 0, (
            "Every lot with a non-null runtime_trade_id must link to position_current."
        )


# ---------------------------------------------------------------------------
# Live-drift tests — NOT in default CI; require state/zeus_trades.db
# ---------------------------------------------------------------------------

LIVE_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "state", "zeus_trades.db"
)


def _live_conn():
    if not os.path.exists(LIVE_DB_PATH):
        pytest.skip(f"Live DB not found at {LIVE_DB_PATH}")
    conn = sqlite3.connect(f"file:{LIVE_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.live_drift
def test_live_no_true_orphan_lots():
    """
    Live DB: every lot links through trade_decisions to position_current.
    Failure means a lot was written without a trade_decisions parent —
    structural integrity breach.
    """
    conn = _live_conn()
    try:
        rows = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM position_lots pl
            WHERE NOT EXISTS (
                SELECT 1 FROM trade_decisions td WHERE td.trade_id = pl.position_id
            )
        """).fetchone()
        assert rows["cnt"] == 0, (
            f"Live DB has {rows['cnt']} lots with no trade_decisions parent (true orphans). "
            "These require operator investigation."
        )
    finally:
        conn.close()


@pytest.mark.live_drift
def test_live_no_unresolvable_lots():
    """
    Live DB: every lot whose trade_decisions.runtime_trade_id is non-null
    must resolve to an existing position_current row.
    """
    conn = _live_conn()
    try:
        rows = conn.execute("""
            SELECT pl.position_id, td.runtime_trade_id
            FROM position_lots pl
            JOIN trade_decisions td ON td.trade_id = pl.position_id
            WHERE td.runtime_trade_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM position_current pc
                  WHERE pc.position_id = td.runtime_trade_id
              )
            GROUP BY pl.position_id
        """).fetchall()
        if rows:
            ids = [r["runtime_trade_id"] for r in rows]
            pytest.fail(
                f"Live DB: {len(rows)} lot group(s) reference runtime_trade_ids "
                f"absent from position_current: {ids}"
            )
    finally:
        conn.close()


@pytest.mark.live_drift
def test_live_latest_sequence_shares_match():
    """
    Live DB: for every non-voided position with lots, latest-sequence lot shares
    must match position_current.shares within 0.0001.
    Failure means a fill was partially materialized or position_current was
    updated without a corresponding lot state transition.
    """
    conn = _live_conn()
    try:
        rows = conn.execute("""
            SELECT pc.position_id, pc.phase, pc.shares as pc_shares,
                   CAST(lot.shares AS REAL) as lot_shares
            FROM position_current pc
            JOIN trade_decisions td ON td.runtime_trade_id = pc.position_id
            JOIN position_lots lot ON lot.position_id = td.trade_id
            JOIN (
                SELECT position_id, MAX(local_sequence) AS max_seq
                FROM position_lots
                GROUP BY position_id
            ) latest
              ON latest.position_id = lot.position_id
             AND latest.max_seq = lot.local_sequence
            WHERE pc.phase NOT IN ('voided', 'admin_closed', 'quarantined')
              AND lot.state NOT IN ('ECONOMICALLY_CLOSED_OPTIMISTIC',
                                    'ECONOMICALLY_CLOSED_CONFIRMED',
                                    'SETTLED')
        """).fetchall()
        mismatches = [
            (r["position_id"], r["phase"], r["pc_shares"], r["lot_shares"])
            for r in rows
            if abs((r["lot_shares"] or 0) - r["pc_shares"]) > 0.0001
        ]
        if mismatches:
            detail = "; ".join(
                f"{pos} ({phase}): pc={pc} lot={lot}"
                for pos, phase, pc, lot in mismatches
            )
            pytest.fail(
                f"Live DB: {len(mismatches)} shares mismatch(es) via latest-sequence: {detail}"
            )
    finally:
        conn.close()
