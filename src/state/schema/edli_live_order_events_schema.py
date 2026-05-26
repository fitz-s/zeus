"""EDLI live-order aggregate schema owner."""

from __future__ import annotations

import sqlite3


LIVE_ORDER_EVENT_TYPES = (
    "DecisionProofAccepted",
    "SubmitPlanBuilt",
    "PreSubmitRevalidated",
    "LiveCapReserved",
    "ExecutionCommandCreated",
    "VenueSubmitAttempted",
    "VenueSubmitAcknowledged",
    "SubmitRejected",
    "SubmitUnknown",
    "UserOrderObserved",
    "UserTradeObserved",
    "Reconciled",
    "CapTransitioned",
    "OrderLifecycleProjected",
)

_EVENT_TYPE_CHECK = ",".join(f"'{value}'" for value in LIVE_ORDER_EVENT_TYPES)

CREATE_EVENTS_SQL = f"""
CREATE TABLE IF NOT EXISTS edli_live_order_events (
    aggregate_event_id TEXT NOT NULL PRIMARY KEY,
    aggregate_id TEXT NOT NULL,
    event_sequence INTEGER NOT NULL CHECK (event_sequence > 0),
    event_type TEXT NOT NULL CHECK (event_type IN ({_EVENT_TYPE_CHECK})),
    parent_event_hash TEXT,
    event_hash TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    source_authority TEXT NOT NULL CHECK (
        source_authority IN (
            'decision_kernel',
            'engine_adapter',
            'live_cap_ledger',
            'existing_executor',
            'user_channel',
            'explicit_reconcile'
        )
    ),
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    UNIQUE(aggregate_id, event_sequence)
)
"""

CREATE_PROJECTION_SQL = """
CREATE TABLE IF NOT EXISTS edli_live_order_projection (
    aggregate_id TEXT NOT NULL PRIMARY KEY,
    event_id TEXT NOT NULL,
    final_intent_id TEXT,
    current_state TEXT NOT NULL,
    last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
    last_event_type TEXT,
    last_event_hash TEXT,
    pending_reconcile INTEGER NOT NULL CHECK (pending_reconcile IN (0,1)),
    venue_order_id TEXT,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
)
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_order_events_aggregate
    ON edli_live_order_events(aggregate_id, event_sequence)
"""

CREATE_TYPE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_order_events_type
    ON edli_live_order_events(event_type, occurred_at)
"""

CREATE_PROJECTION_STATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_live_order_projection_state
    ON edli_live_order_projection(current_state, updated_at)
"""

CREATE_NO_UPDATE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_edli_live_order_events_no_update
BEFORE UPDATE ON edli_live_order_events
BEGIN
    SELECT RAISE(ABORT, 'edli_live_order_events is append-only');
END
"""

CREATE_NO_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_edli_live_order_events_no_delete
BEFORE DELETE ON edli_live_order_events
BEGIN
    SELECT RAISE(ABORT, 'edli_live_order_events is append-only');
END
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_EVENTS_SQL)
    conn.execute(CREATE_PROJECTION_SQL)
    conn.execute(CREATE_INDEX_SQL)
    conn.execute(CREATE_TYPE_INDEX_SQL)
    conn.execute(CREATE_PROJECTION_STATE_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)
