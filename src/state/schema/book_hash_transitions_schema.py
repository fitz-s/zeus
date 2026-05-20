# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §4.3 (sha 00c2399742)
"""T1 — CREATE TABLE DDL for book_hash_transitions (trade DB, production pass 2026-05-20).

Per §4.3: composite PK (market_slug, observed_at, transition_seq),
CHECK (new_hash != prev_hash), CHECK (delta_ms >= 0),
schema_version CHECK (13, 14).
"""
from __future__ import annotations

import sqlite3

# Schema version stamped into each row; stays in sync with db.py SCHEMA_VERSION.
SCHEMA_VERSION = 14


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS book_hash_transitions (
    market_slug         TEXT NOT NULL,
    observed_at         TEXT NOT NULL,
    transition_seq      INTEGER NOT NULL,
    prev_hash           TEXT NOT NULL,
    new_hash            TEXT NOT NULL CHECK (new_hash != prev_hash),
    delta_ms            INTEGER NOT NULL CHECK (delta_ms >= 0),
    cycle_id            TEXT,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (13, 14)),
    PRIMARY KEY (market_slug, observed_at, transition_seq)
)
"""

CREATE_INDEX_MARKET_TIME_SQL = """
CREATE INDEX IF NOT EXISTS idx_book_hash_transitions_market_time
    ON book_hash_transitions(market_slug, observed_at)
"""

CREATE_INDEX_NEW_HASH_SQL = """
CREATE INDEX IF NOT EXISTS idx_book_hash_transitions_new_hash
    ON book_hash_transitions(new_hash)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create book_hash_transitions table + indices if they do not exist.

    Idempotent (IF NOT EXISTS). Called from two paths:
      1. db.py init_schema_trade_only (daemon boot, trade DB) — live substrate owner
      2. scripts/migrate_book_hash_transitions_create_2026_05_21.py — operator one-shot migration
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_MARKET_TIME_SQL)
    conn.execute(CREATE_INDEX_NEW_HASH_SQL)
