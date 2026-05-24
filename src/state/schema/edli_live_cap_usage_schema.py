"""EDLI live-cap usage schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edli_live_cap_usage (
    usage_id TEXT PRIMARY KEY,
    cap_name TEXT NOT NULL,
    usage_date TEXT NOT NULL,
    event_id TEXT NOT NULL,
    notional_usd REAL NOT NULL CHECK (notional_usd >= 0),
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(cap_name, usage_date, event_id)
)
"""

CREATE_CAP_DATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_cap_usage_cap_date
    ON edli_live_cap_usage(cap_name, usage_date)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_CAP_DATE_INDEX_SQL)
