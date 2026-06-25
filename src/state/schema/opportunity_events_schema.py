# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: Operator P1 2026-06-04 — channel-sweep keeper query index-back
#                  (category-kill of 85s json_extract full-scan)
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
        'NEW_MARKET_DISCOVERED',
        -- Continuous re-decision resurrection (2026-06-12): a PRICE-driven re-decision of a
        -- forecast family. Carries the same FSR-shaped payload; routes through the forecast
        -- decision lane (see _FORECAST_DECISION_EVENT_TYPES across reactor/adapter/store).
        'EDLI_REDECISION_PENDING'
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

CREATE_FSR_TARGET_DATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_events_fsr_target_date
    ON opportunity_events(event_type, json_extract(payload_json, '$.target_date'), available_at)
"""

CREATE_DAY0_FAMILY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_events_day0_family
    ON opportunity_events(
        event_type,
        json_extract(payload_json, '$.target_date'),
        json_extract(payload_json, '$.city'),
        json_extract(payload_json, '$.metric'),
        available_at
    )
"""

# Expression index backing the keeper-subquery GROUP BY in
# EventStore.archive_superseded_channel_events (Step 1).  Without this index
# SQLite full-scans the table parsing json_extract three times per cycle —
# confirmed 85.6 s at 1.78 M rows on 2026-06-04.  The expression text
# MUST be byte-identical to the GROUP BY / WHERE expressions in the query
# so SQLite's expression-index matching fires.
CREATE_CHANNEL_TOKEN_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_opportunity_events_channel_token
    ON opportunity_events(event_type, json_extract(payload_json, '$.token_id'), available_at)
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


def _migrate_event_type_check_for_redecision(conn: sqlite3.Connection) -> None:
    """Add EDLI_REDECISION_PENDING to the event_type CHECK on LIVE DBs created before the
    continuous-redecision resurrection (2026-06-12).

    A SQLite CHECK constraint cannot be ALTERed in place; the canonical fix is a table rebuild. We
    only run it when the live table's stored SQL is MISSING the new type (idempotent — a fresh DB
    built from CREATE_TABLE_SQL already has it, so the guard is False and this is a pure no-op).
    The rebuild is append-only-safe: drop the no-update/no-delete triggers, copy rows into a new
    table carrying the widened CHECK, swap, and restore the triggers. Wrapped so a row that somehow
    predates the constraint cannot abort the daemon boot — but the copy is a straight INSERT SELECT
    of an append-only log, so it is deterministic."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='opportunity_events'"
    ).fetchone()
    if row is None or not row[0]:
        return  # table not yet created (CREATE_TABLE_SQL above handles fresh DBs)
    if "EDLI_REDECISION_PENDING" in row[0]:
        return  # already widened (fresh DB or prior migration) — no-op
    # Live DB with the old CHECK: rebuild with the widened constraint.
    conn.execute("DROP TRIGGER IF EXISTS trg_opportunity_events_no_update")
    conn.execute("DROP TRIGGER IF EXISTS trg_opportunity_events_no_delete")
    conn.execute(CREATE_TABLE_SQL.replace("opportunity_events", "opportunity_events__new"))
    conn.execute(
        "INSERT INTO opportunity_events__new SELECT * FROM opportunity_events"
    )
    conn.execute("DROP TABLE opportunity_events")
    conn.execute("ALTER TABLE opportunity_events__new RENAME TO opportunity_events")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    _migrate_event_type_check_for_redecision(conn)
    conn.execute(CREATE_PENDING_ORDER_INDEX_SQL)
    conn.execute(CREATE_TYPE_AVAILABLE_INDEX_SQL)
    conn.execute(CREATE_FSR_TARGET_DATE_INDEX_SQL)
    conn.execute(CREATE_DAY0_FAMILY_INDEX_SQL)
    conn.execute(CREATE_CHANNEL_TOKEN_INDEX_SQL)
    conn.execute(CREATE_NO_UPDATE_TRIGGER_SQL)
    conn.execute(CREATE_NO_DELETE_TRIGGER_SQL)
