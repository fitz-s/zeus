"""EDLI opportunity_events schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS opportunity_events (
    event_id TEXT NOT NULL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'FORECAST_SNAPSHOT_READY',
        'DAY0_EXTREME_UPDATED',
        'BOOK_SNAPSHOT',
        'BEST_BID_ASK_CHANGED',
        'NEW_MARKET_DISCOVERED'
    )),
    entity_key TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    causal_snapshot_id TEXT,
    payload_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    priority INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    payload_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    created_at TEXT NOT NULL
)
"""

CREATE_PENDING_ORDER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_events_pending_order
    ON opportunity_events(priority DESC, available_at ASC, received_at ASC, event_id ASC)
"""

CREATE_TYPE_AVAILABLE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_events_type_available
    ON opportunity_events(event_type, available_at)
"""

CREATE_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_opportunity_events_no_update
BEFORE UPDATE ON opportunity_events
BEGIN
    SELECT RAISE(ABORT, 'opportunity_events is append-only');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_opportunity_events_no_delete
BEFORE DELETE ON opportunity_events
BEGIN
    SELECT RAISE(ABORT, 'opportunity_events is append-only');
END
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_PENDING_ORDER_INDEX_SQL)
    conn.execute(CREATE_TYPE_AVAILABLE_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)
