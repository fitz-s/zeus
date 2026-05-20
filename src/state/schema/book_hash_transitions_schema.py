# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §4.3 (sha 00c2399742)
"""T1 SCAFFOLD — CREATE TABLE DDL for book_hash_transitions (world DB).

DO NOT call from src/state/db.py in SCAFFOLD (production pass wires this).
SCHEMA_VERSION bump (13→14) is a production-pass responsibility.

Per §4.3: composite PK (market_slug, observed_at, transition_seq),
CHECK (new_hash != prev_hash), CHECK (delta_ms >= 0),
schema_version CHECK (13, 14).
"""
from __future__ import annotations

import sqlite3


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

    Idempotent (IF NOT EXISTS). Called by production-pass migration script
    and by db.py init (production pass wires this — DO NOT call from db.py
    in SCAFFOLD).

    T1 SCAFFOLD — production pass wires the call site in db.py.
    """
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_MARKET_TIME_SQL)
    conn.execute(CREATE_INDEX_NEW_HASH_SQL)
