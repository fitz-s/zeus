"""EDLI no_trade_regret_events schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS no_trade_regret_events (
    regret_event_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    rejection_stage TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    regret_bucket TEXT NOT NULL,
    market_slug TEXT,
    condition_id TEXT,
    token_id TEXT,
    outcome_label TEXT,
    later_outcome TEXT,
    would_have_won INTEGER CHECK (would_have_won IN (0, 1) OR would_have_won IS NULL),
    would_have_filled INTEGER CHECK (would_have_filled IN (0, 1) OR would_have_filled IS NULL),
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(event_id, rejection_stage, rejection_reason)
)
"""

CREATE_STAGE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_no_trade_regret_stage
    ON no_trade_regret_events(rejection_stage, created_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_STAGE_INDEX_SQL)
