# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   center-evidence campaign data prerequisite (market-implied center vs our
#   posterior mu). The full family FamilyBook is assembled every decision
#   cycle (src/execution/family_book.py) and discarded once the cycle's
#   trade/no-trade call is made; this table is the durable ladder history.
"""family_book_snapshots -- append-only, dedup-by-hash ledger of decision-time
family order-book ladders.

EVIDENCE, never decision authority: nothing on the live trade path reads this
table to decide anything; it exists so the center-evidence campaign can
compare the market-implied center against our posterior mu over time.

Append-only, no update path anywhere, ever: UNIQUE(family_id, book_hash) +
INSERT OR IGNORE makes a re-decided, unchanged book a free no-op -- the
volume control is dedup-by-book-hash, not a cap. ``snapshot_id`` (the PK) is
a deterministic hash of (family_id, book_hash, decision_time) -- finer-grained
than the dedup key, so a re-decided identical book computes a snapshot_id
that is simply never written (the stored row keeps its original one).
"""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS family_book_snapshots (
    snapshot_id          TEXT PRIMARY KEY,
    family_id            TEXT NOT NULL,
    city                 TEXT NOT NULL,
    target_date          TEXT NOT NULL,
    temperature_metric   TEXT NOT NULL,
    decision_time        TEXT NOT NULL,
    captured_at_utc      TEXT NOT NULL,
    book_hash            TEXT NOT NULL,
    complete_book        INTEGER NOT NULL,
    ladder_json          TEXT NOT NULL,
    market_center_c      REAL,
    our_mu_c             REAL,
    our_sigma_c          REAL,
    decision_snapshot_id TEXT,
    schema_version       INTEGER NOT NULL
)
"""

# Dedup key: same book re-decided for the same family writes zero new rows.
CREATE_UNIQUE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_family_book_snapshots_identity
    ON family_book_snapshots(family_id, book_hash)
"""

# Backs the center-evidence campaign's per-family book-history query.
CREATE_FAMILY_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_family_book_snapshots_family_time
    ON family_book_snapshots(family_id, decision_time)
"""

CREATE_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_family_book_snapshots_no_update
BEFORE UPDATE ON family_book_snapshots
BEGIN
    SELECT RAISE(ABORT, 'family_book_snapshots is append-only');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_family_book_snapshots_no_delete
BEFORE DELETE ON family_book_snapshots
BEGIN
    SELECT RAISE(ABORT, 'family_book_snapshots is append-only');
END
"""

SCHEMA_VERSION = 1


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_UNIQUE_INDEX_SQL)
    conn.execute(CREATE_FAMILY_TIME_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)


def append_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    family_id: str,
    city: str,
    target_date: str,
    temperature_metric: str,
    decision_time: str,
    captured_at_utc: str,
    book_hash: str,
    complete_book: bool,
    ladder_json: str,
    market_center_c: float | None,
    our_mu_c: float | None,
    our_sigma_c: float | None,
    decision_snapshot_id: str | None,
) -> bool:
    """Append one family book snapshot. Returns True iff a new row was
    inserted, False if (family_id, book_hash) was already present (INSERT OR
    IGNORE -- append-only dedup, never a mutation)."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO family_book_snapshots (
            snapshot_id, family_id, city, target_date, temperature_metric,
            decision_time, captured_at_utc, book_hash, complete_book,
            ladder_json, market_center_c, our_mu_c, our_sigma_c,
            decision_snapshot_id, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id, family_id, city, target_date, temperature_metric,
            decision_time, captured_at_utc, book_hash, int(complete_book),
            ladder_json, market_center_c, our_mu_c, our_sigma_c,
            decision_snapshot_id, SCHEMA_VERSION,
        ),
    )
    return cur.rowcount > 0
