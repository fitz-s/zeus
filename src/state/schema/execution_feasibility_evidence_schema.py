"""EDLI trade-class execution_feasibility_evidence schema owner.

C4 telemetry-truth (2026-06-16) — HONEST_NULL_COLUMNS declaration:

    order_intent_time, submit_time, latency_ms are permanently write-NULL.
    No post-fill UPDATE path exists: the EDLI execution lane writes this row
    at decision time before fill confirmation arrives; the fill confirmation
    arrives on a separate WS event with no back-link to re-update this row.
    Do NOT fabricate these values with datetime.now() or 0.

HONEST_NULL_COLUMNS = {"order_intent_time", "submit_time", "latency_ms"}
"""

from __future__ import annotations

import sqlite3

# C4 telemetry-truth: these columns are permanently write-NULL. The EDLI lane
# has no post-fill UPDATE path to populate them after fill confirmation arrives.
# Do NOT assign datetime.now(), 0, or any synthetic value to these columns.
HONEST_NULL_COLUMNS = frozenset({"order_intent_time", "submit_time", "latency_ms"})

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS execution_feasibility_evidence (
    evidence_id TEXT NOT NULL PRIMARY KEY,
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

CREATE_TOKEN_CREATED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_execution_feasibility_evidence_token_created
    ON execution_feasibility_evidence(token_id, created_at DESC)
"""

CREATE_LATEST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS execution_feasibility_latest (
    token_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('buy_yes', 'buy_no', 'sell_yes', 'sell_no')),
    evidence_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    outcome_label TEXT NOT NULL CHECK (outcome_label IN ('YES', 'NO')),
    quote_seen_at TEXT NOT NULL,
    book_hash_before TEXT,
    best_bid_before REAL,
    best_ask_before REAL,
    depth_before_json TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    PRIMARY KEY (token_id, direction)
)
"""

CREATE_LATEST_TOKEN_CREATED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_execution_feasibility_latest_token_created
    ON execution_feasibility_latest(token_id, created_at DESC)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_TOKEN_TIME_INDEX_SQL)
    conn.execute(CREATE_TOKEN_CREATED_INDEX_SQL)
    conn.execute(CREATE_LATEST_TABLE_SQL)
    conn.execute(CREATE_LATEST_TOKEN_CREATED_INDEX_SQL)
