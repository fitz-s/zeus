"""EDLI opportunity_event_processing schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS opportunity_event_processing (
    consumer_name TEXT NOT NULL,
    event_id TEXT NOT NULL,
    processing_status TEXT NOT NULL CHECK (processing_status IN (
        'pending','processing','processed','failed','dead_letter','expired','ignored'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    processed_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (consumer_name, event_id)
)
"""

CREATE_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_event_processing_status
    ON opportunity_event_processing(consumer_name, processing_status, updated_at)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_STATUS_INDEX_SQL)
