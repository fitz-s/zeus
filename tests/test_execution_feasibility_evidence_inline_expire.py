# Created: 2026-08-25
# Last reused or audited: 2026-08-25
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 13 (bounded-by-construction storage redesign) -- coverage for
#   src/events/triggers/market_channel_ingestor.py::
#   _inline_expire_execution_feasibility_evidence, piggybacked in
#   insert_execution_feasibility_evidence_batch.
"""Antibodies for the execution_feasibility_evidence inline retention: old
rows deleted, recent rows survive, the LIMIT bound, the prerequisite index is
created idempotently, and it never raises even on a malformed table name."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.events.triggers.market_channel_ingestor import (
    _inline_expire_execution_feasibility_evidence,
    _FEASIBILITY_INLINE_EXPIRE_LIMIT,
    _FEASIBILITY_CUTOFF_INDEX_NAME,
)

TABLE_DDL = """
CREATE TABLE execution_feasibility_evidence (
    evidence_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    quote_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL
)
"""


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _seed(conn: sqlite3.Connection, evidence_id: str, ts: str) -> None:
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?, 'evt', 'cond', 'tok', 'YES', 'buy_yes', ?, ?, 1)",
        (evidence_id, ts, ts),
    )


def _ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT evidence_id FROM execution_feasibility_evidence")}


def test_deletes_old_rows_keeps_recent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(TABLE_DDL)
    old, recent = _iso(40), _iso(1)
    _seed(conn, "ev-old", old)
    _seed(conn, "ev-recent", recent)

    _inline_expire_execution_feasibility_evidence(conn, "execution_feasibility_evidence")

    assert _ids(conn) == {"ev-recent"}


def test_is_limit_bounded() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(TABLE_DDL)
    old = _iso(40)
    for i in range(_FEASIBILITY_INLINE_EXPIRE_LIMIT + 10):
        _seed(conn, f"ev-{i}", old)

    _inline_expire_execution_feasibility_evidence(conn, "execution_feasibility_evidence")

    remaining = conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
    assert remaining == 10


def test_creates_prerequisite_index_idempotently() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(TABLE_DDL)

    _inline_expire_execution_feasibility_evidence(conn, "execution_feasibility_evidence")
    _inline_expire_execution_feasibility_evidence(conn, "execution_feasibility_evidence")  # idempotent

    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (_FEASIBILITY_CUTOFF_INDEX_NAME,),
    ).fetchone()
    assert row is not None


def test_never_raises_on_bad_table_name() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(TABLE_DDL)

    # A malformed/nonexistent table name must be swallowed, not propagate --
    # this runs inside the caller's write path and must never block a real insert.
    _inline_expire_execution_feasibility_evidence(conn, "nonexistent_table")

    # The real table is untouched.
    count = conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
    assert count == 0
