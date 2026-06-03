"""EDLI live-cap usage schema owner."""

from __future__ import annotations

import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS edli_live_cap_usage (
    usage_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    cap_scope TEXT NOT NULL,
    max_notional_usd REAL NOT NULL CHECK (max_notional_usd >= 0),
    max_orders_per_day INTEGER NOT NULL CHECK (max_orders_per_day > 0),
    reserved_notional_usd REAL NOT NULL CHECK (reserved_notional_usd >= 0),
    order_count INTEGER NOT NULL CHECK (order_count >= 0),
    reservation_status TEXT NOT NULL CHECK (
        reservation_status IN ('RESERVED','RELEASED','CONSUMED','REJECTED')
    ),
    final_intent_id TEXT,
    execution_command_id TEXT,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(event_id, cap_scope)
)
"""

CREATE_CAP_DATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_cap_usage_scope_time
    ON edli_live_cap_usage(cap_scope, decision_time)
"""

CREATE_DAY_SLOTS_SQL = """
CREATE TABLE IF NOT EXISTS edli_live_cap_day_slots (
    cap_scope TEXT NOT NULL,
    cap_date TEXT NOT NULL,
    slot INTEGER NOT NULL CHECK (slot > 0),
    usage_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    PRIMARY KEY (cap_scope, cap_date, slot)
)
"""

CREATE_DAY_SLOTS_EVENT_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_edli_live_cap_day_slots_event
    ON edli_live_cap_day_slots(event_id, cap_scope)
"""

# BUG #99 antibody: order-emission RATE limiter, structurally DECOUPLED from the
# notional cap and from max_orders_per_day. This is a SECOND, independent slot
# pool keyed by a time-window bucket; its pool size (max_orders_per_window) is a
# separate config knob from tiny_live_max_notional_usd. Raising the notional cap
# can no longer silently erase the order-frequency bound — the two controls live
# in different tables and are reserved independently.
CREATE_RATE_WINDOW_SQL = """
CREATE TABLE IF NOT EXISTS edli_live_cap_rate_window (
    cap_scope TEXT NOT NULL,
    window_key TEXT NOT NULL,
    slot INTEGER NOT NULL CHECK (slot > 0),
    usage_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    PRIMARY KEY (cap_scope, window_key, slot)
)
"""

CREATE_RATE_WINDOW_EVENT_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_edli_live_cap_rate_window_event
    ON edli_live_cap_rate_window(event_id, cap_scope, window_key)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_CAP_DATE_INDEX_SQL)
    conn.execute(CREATE_DAY_SLOTS_SQL)
    conn.execute(CREATE_DAY_SLOTS_EVENT_INDEX_SQL)
    conn.execute(CREATE_RATE_WINDOW_SQL)
    conn.execute(CREATE_RATE_WINDOW_EVENT_INDEX_SQL)
