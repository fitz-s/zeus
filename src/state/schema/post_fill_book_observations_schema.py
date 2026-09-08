"""Trade-DB schema for restart-safe, passive post-fill raw-book capture.

These facts are observation lineage only.  They cannot authorize a trade,
markout, PnL, settlement, freshness, or a decision snapshot.
"""

from __future__ import annotations

import sqlite3

TABLES = (
    "post_fill_book_protocols",
    "post_fill_book_requests",
    "post_fill_book_observation_events",
    "post_fill_book_cursors",
)

DDL = """
CREATE TABLE IF NOT EXISTS post_fill_book_protocols (
    protocol_id TEXT PRIMARY KEY,
    caller TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL CHECK (horizon_seconds > 0),
    window_seconds INTEGER NOT NULL CHECK (window_seconds > 0),
    registered_at TEXT NOT NULL,
    source_fact_baseline INTEGER NOT NULL CHECK (source_fact_baseline >= 0),
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS post_fill_book_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_id TEXT NOT NULL REFERENCES post_fill_book_protocols(protocol_id),
    command_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    source_trade_fact_id INTEGER NOT NULL,
    venue_order_id TEXT NOT NULL,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    source_state TEXT NOT NULL,
    source_venue_timestamp TEXT,
    fill_time_utc TEXT,
    due_at TEXT,
    window_end_at TEXT,
    filled_size TEXT NOT NULL,
    fill_price TEXT NOT NULL,
    fee_paid_micro INTEGER,
    tx_hash TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(protocol_id, command_id, trade_id)
);
CREATE TABLE IF NOT EXISTS post_fill_book_observation_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_id TEXT NOT NULL REFERENCES post_fill_book_protocols(protocol_id),
    request_id INTEGER REFERENCES post_fill_book_requests(request_id),
    source_trade_fact_id INTEGER,
    command_id TEXT,
    trade_id TEXT,
    event_type TEXT NOT NULL CHECK (event_type IN (
      'SOURCE_OBSERVED','CAPTURED','FETCH_ERROR','MISSED_WINDOW'
    )),
    reason TEXT NOT NULL DEFAULT '',
    source_state TEXT,
    source_type TEXT,
    source_raw_payload_hash TEXT,
    source_venue_timestamp TEXT,
    source_filled_size TEXT,
    source_fill_price TEXT,
    source_fee_paid_micro INTEGER,
    source_tx_hash TEXT,
    source_condition_id TEXT,
    source_token_id TEXT,
    source_side TEXT,
    clock_provenance_verified INTEGER NOT NULL DEFAULT 0 CHECK (clock_provenance_verified = 0),
    fill_time_utc TEXT,
    due_at TEXT,
    window_end_at TEXT,
    observed_at TEXT NOT NULL,
    fetch_started_at TEXT,
    fetch_finished_at TEXT,
    endpoint TEXT,
    http_status INTEGER,
    raw_body BLOB,
    raw_body_sha256 TEXT,
    provider_timestamp_raw TEXT,
    provider_asset_id TEXT,
    provider_market TEXT,
    freshness_verified INTEGER NOT NULL DEFAULT 0 CHECK (freshness_verified = 0),
    CHECK ((event_type = 'SOURCE_OBSERVED') OR request_id IS NOT NULL),
    UNIQUE(protocol_id, source_trade_fact_id, event_type)
);
CREATE TABLE IF NOT EXISTS post_fill_book_cursors (
    protocol_id TEXT PRIMARY KEY REFERENCES post_fill_book_protocols(protocol_id),
    last_trade_fact_id INTEGER NOT NULL CHECK (last_trade_fact_id >= 0),
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_post_fill_book_requests_due
 ON post_fill_book_requests(protocol_id, due_at, window_end_at, request_id);
CREATE INDEX IF NOT EXISTS idx_post_fill_book_requests_global_due
 ON post_fill_book_requests(window_end_at, due_at, request_id);
CREATE INDEX IF NOT EXISTS idx_post_fill_book_events_request
 ON post_fill_book_observation_events(request_id, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_post_fill_book_one_terminal_per_request
 ON post_fill_book_observation_events(request_id)
 WHERE event_type IN ('CAPTURED','MISSED_WINDOW');
CREATE TRIGGER IF NOT EXISTS post_fill_book_protocols_no_update
 BEFORE UPDATE ON post_fill_book_protocols BEGIN SELECT RAISE(ABORT, 'post-fill protocol immutable'); END;
CREATE TRIGGER IF NOT EXISTS post_fill_book_protocols_no_delete
 BEFORE DELETE ON post_fill_book_protocols BEGIN SELECT RAISE(ABORT, 'post-fill protocol append-only'); END;
CREATE TRIGGER IF NOT EXISTS post_fill_book_requests_no_update
 BEFORE UPDATE ON post_fill_book_requests BEGIN SELECT RAISE(ABORT, 'post-fill request immutable'); END;
CREATE TRIGGER IF NOT EXISTS post_fill_book_requests_no_delete
 BEFORE DELETE ON post_fill_book_requests BEGIN SELECT RAISE(ABORT, 'post-fill request append-only'); END;
CREATE TRIGGER IF NOT EXISTS post_fill_book_events_no_update
 BEFORE UPDATE ON post_fill_book_observation_events BEGIN SELECT RAISE(ABORT, 'post-fill observation event immutable'); END;
CREATE TRIGGER IF NOT EXISTS post_fill_book_events_no_delete
 BEFORE DELETE ON post_fill_book_observation_events BEGIN SELECT RAISE(ABORT, 'post-fill observation event append-only'); END;
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    # executescript() commits any caller transaction before it runs DDL.  This
    # helper is called inside registration/source-event transactions, so execute
    # complete SQLite statements one at a time and preserve the outer boundary.
    statement = ""
    for line in DDL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                conn.execute(statement)
            statement = ""
    if statement.strip():
        raise RuntimeError("incomplete post-fill observation schema statement")
