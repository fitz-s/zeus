"""EDLI execution_feasibility_evidence schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS execution_feasibility_evidence (
    evidence_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    outcome_label TEXT NOT NULL CHECK (outcome_label IN ('YES', 'NO')),
    direction TEXT NOT NULL CHECK (direction IN ('buy_yes', 'buy_no', 'sell_yes', 'sell_no')),
    quote_seen_at TEXT NOT NULL,
    book_hash_before TEXT,
    best_bid_before REAL,
    best_ask_before REAL,
    depth_before_json TEXT,
    order_intent_time TEXT,
    submit_time TEXT,
    accepted_or_rejected TEXT,
    venue_order_id TEXT,
    fok_full_fill INTEGER CHECK (fok_full_fill IN (0, 1) OR fok_full_fill IS NULL),
    fak_partial_fill INTEGER CHECK (fak_partial_fill IN (0, 1) OR fak_partial_fill IS NULL),
    filled_shares REAL,
    fill_price REAL,
    cancel_remainder_status TEXT,
    book_hash_after TEXT,
    latency_ms INTEGER,
    maker_cancel_before_submit INTEGER CHECK (maker_cancel_before_submit IN (0, 1) OR maker_cancel_before_submit IS NULL),
    would_have_edge_after_fee INTEGER CHECK (would_have_edge_after_fee IN (0, 1) OR would_have_edge_after_fee IS NULL),
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
)
"""

CREATE_TOKEN_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_execution_feasibility_evidence_token_time
    ON execution_feasibility_evidence(token_id, quote_seen_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_TOKEN_TIME_INDEX_SQL)
