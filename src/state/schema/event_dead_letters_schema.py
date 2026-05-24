"""EDLI event_dead_letters schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS event_dead_letters (
    dead_letter_id TEXT NOT NULL PRIMARY KEY,
    consumer_name TEXT NOT NULL,
    event_id TEXT NOT NULL,
    failure_stage TEXT NOT NULL,
    error_message TEXT NOT NULL,
    event_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
)
"""

CREATE_EVENT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_event_dead_letters_event
    ON event_dead_letters(event_id, consumer_name)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_EVENT_INDEX_SQL)
