"""EDLI no_trade_regret_events schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS no_trade_regret_events (
    regret_event_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    rejection_stage TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    regret_bucket TEXT NOT NULL,
    market_slug TEXT,
    condition_id TEXT,
    token_id TEXT,
    outcome_label TEXT,
    decision_time TEXT,
    city TEXT,
    target_date TEXT,
    metric TEXT,
    family_id TEXT,
    bin_label TEXT,
    direction TEXT,
    q_live REAL,
    q_lcb_5pct REAL,
    c_fee_adjusted REAL,
    c_cost_95pct REAL,
    p_fill_lcb REAL,
    trade_score REAL,
    native_quote_available INTEGER CHECK (native_quote_available IN (0, 1) OR native_quote_available IS NULL),
    source_status TEXT,
    family_complete INTEGER CHECK (family_complete IN (0, 1) OR family_complete IS NULL),
    hypothetical_order_type TEXT,
    hypothetical_fill_status TEXT,
    hypothetical_fill_price REAL,
    causal_snapshot_id TEXT,
    executable_snapshot_id TEXT,
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
    _ensure_columns(conn)
    conn.execute(CREATE_STAGE_INDEX_SQL)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(no_trade_regret_events)").fetchall()}
    column_sql = {
        "decision_time": "TEXT",
        "city": "TEXT",
        "target_date": "TEXT",
        "metric": "TEXT",
        "family_id": "TEXT",
        "bin_label": "TEXT",
        "direction": "TEXT",
        "q_live": "REAL",
        "q_lcb_5pct": "REAL",
        "c_fee_adjusted": "REAL",
        "c_cost_95pct": "REAL",
        "p_fill_lcb": "REAL",
        "trade_score": "REAL",
        "native_quote_available": "INTEGER CHECK (native_quote_available IN (0, 1) OR native_quote_available IS NULL)",
        "source_status": "TEXT",
        "family_complete": "INTEGER CHECK (family_complete IN (0, 1) OR family_complete IS NULL)",
        "hypothetical_order_type": "TEXT",
        "hypothetical_fill_status": "TEXT",
        "hypothetical_fill_price": "REAL",
        "causal_snapshot_id": "TEXT",
        "executable_snapshot_id": "TEXT",
    }
    for column, ddl in column_sql.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE no_trade_regret_events ADD COLUMN {column} {ddl}")
