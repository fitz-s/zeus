"""EDLI no-submit receipt schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edli_no_submit_receipts (
    receipt_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    causal_snapshot_id TEXT,
    decision_time TEXT NOT NULL,
    family_id TEXT,
    candidate_id TEXT,
    condition_id TEXT,
    token_id TEXT,
    direction TEXT,
    executable_snapshot_id TEXT,
    final_intent_id TEXT,
    side_effect_status TEXT NOT NULL CHECK (side_effect_status = 'NO_SUBMIT'),
    q_live REAL,
    q_lcb_5pct REAL,
    c_fee_adjusted REAL,
    c_cost_95pct REAL,
    p_fill_lcb REAL,
    trade_score REAL,
    fdr_family_id TEXT,
    fdr_hypothesis_count INTEGER NOT NULL DEFAULT 0,
    kelly_cost_basis_id TEXT,
    kelly_size_usd REAL NOT NULL DEFAULT 0.0,
    receipt_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(event_id, final_intent_id)
)
"""

CREATE_EVENT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_no_submit_receipts_event
    ON edli_no_submit_receipts(event_id)
"""

CREATE_DECISION_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_no_submit_receipts_decision_time
    ON edli_no_submit_receipts(decision_time)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_EVENT_INDEX_SQL)
    conn.execute(CREATE_DECISION_TIME_INDEX_SQL)
