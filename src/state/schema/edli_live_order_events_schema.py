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
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    -- H2_E2E (REAUDIT_0_1.md §2/§4): typed posterior trace on the live-order
    -- projection so the live-order aggregate is itself SQL-reconstructable to the
    -- driving posterior WITHOUT JSON_EXTRACT. Nullable; NULL on non-replacement
    -- orders (observability only — never changes order state).
    posterior_id INTEGER,
    probability_authority TEXT
)
"""

CREATE_USER_MESSAGE_DEDUP_SQL = """
CREATE TABLE IF NOT EXISTS edli_user_channel_message_dedup (
    message_hash TEXT NOT NULL PRIMARY KEY,
    aggregate_id TEXT NOT NULL,
    venue_order_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

CREATE_USER_CHANNEL_INBOX_SQL = """
CREATE TABLE IF NOT EXISTS edli_user_channel_inbox (
    message_hash TEXT NOT NULL PRIMARY KEY,
    source_authority TEXT NOT NULL CHECK (source_authority = 'polymarket_user_channel'),
    message_type TEXT NOT NULL CHECK (message_type IN ('order','trade')),
    aggregate_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    final_intent_id TEXT NOT NULL,
    venue_order_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    processing_status TEXT NOT NULL CHECK (
        processing_status IN ('PENDING','PROCESSED','DUPLICATE','FAILED','STALE_REJECTED')
    ),
    processing_error TEXT,
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

CREATE_USER_MESSAGE_DEDUP_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_edli_live_order_user_msg_hash
    ON edli_live_order_events(
        aggregate_id,
        json_extract(payload_json, '$.raw_user_channel_message_hash')
    )
    WHERE event_type IN ('UserOrderObserved', 'UserTradeObserved')
      AND json_extract(payload_json, '$.raw_user_channel_message_hash') IS NOT NULL
      AND json_extract(payload_json, '$.raw_user_channel_message_hash') != ''
"""

CREATE_USER_MESSAGE_DEDUP_AGGREGATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_user_channel_message_dedup_aggregate
    ON edli_user_channel_message_dedup(aggregate_id, venue_order_id)
"""

CREATE_USER_CHANNEL_INBOX_STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_user_channel_inbox_status
    ON edli_user_channel_inbox(processing_status, received_at)
"""

CREATE_USER_CHANNEL_INBOX_AGGREGATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_edli_user_channel_inbox_aggregate
    ON edli_user_channel_inbox(aggregate_id, venue_order_id)
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


def _ensure_projection_column(conn: sqlite3.Connection, column_name: str, column_sql: str) -> None:
    # H2_E2E: additive, idempotent. Legacy live DBs predate the posterior-trace
    # columns; ALTER them in without data loss. Nullable, no DEFAULT — every
    # existing projection row is left unchanged.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(edli_live_order_projection)").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE edli_live_order_projection ADD COLUMN {column_name} {column_sql}")


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_EVENTS_SQL)
    conn.execute(CREATE_PROJECTION_SQL)
    _ensure_projection_column(conn, "posterior_id", "INTEGER")
    _ensure_projection_column(conn, "probability_authority", "TEXT")
    conn.execute(CREATE_USER_MESSAGE_DEDUP_SQL)
    conn.execute(CREATE_USER_CHANNEL_INBOX_SQL)
    conn.execute(CREATE_INDEX_SQL)
    conn.execute(CREATE_TYPE_INDEX_SQL)
    conn.execute(CREATE_USER_MESSAGE_DEDUP_INDEX_SQL)
    conn.execute(CREATE_USER_MESSAGE_DEDUP_AGGREGATE_INDEX_SQL)
    conn.execute(CREATE_USER_CHANNEL_INBOX_STATUS_INDEX_SQL)
    conn.execute(CREATE_USER_CHANNEL_INBOX_AGGREGATE_INDEX_SQL)
    conn.execute(CREATE_PROJECTION_STATE_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)
