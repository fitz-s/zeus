# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/WAVE_2_PLAN.md §#37 F11
#   "BulkChunker LIVE chunk boundary observability — DB_CHUNK_BOUNDARY event table"
"""Chunk-boundary event emission for BulkChunker LIVE-yield observability.

F11 (wave6 §#37): when BulkChunker yields to a LIVE writer, or when the
watchdog fires, the event is observable only via counter increments.
No queryable record exists, making post-hoc analysis impossible.

This module provides:
  * ``ensure_table(conn)`` — idempotent DDL for ``db_chunk_boundary_events``
  * ``emit_event(db_path, ...)`` — open-its-own-connection emit; thread-safe
    (safe to call from watchdog daemon thread). Failure-silent.

Table: ``db_chunk_boundary_events`` (schema_class: world_class, db: world)
  event_id       TEXT PRIMARY KEY  — UUIDv4
  occurred_at    TEXT NOT NULL     — ISO-8601 UTC
  caller_module  TEXT NOT NULL     — BulkChunker.caller_module
  db_path        TEXT NOT NULL     — path of the bulk-written DB
  rows_processed INTEGER NOT NULL DEFAULT 0
  duration_ms    INTEGER NOT NULL DEFAULT 0
  split_reason   TEXT NOT NULL
    CHECK (split_reason IN ('LIVE_CONTENDED', 'WATCHDOG', 'MANUAL'))
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS db_chunk_boundary_events (
    event_id       TEXT PRIMARY KEY,
    occurred_at    TEXT NOT NULL,
    caller_module  TEXT NOT NULL,
    db_path        TEXT NOT NULL,
    rows_processed INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    split_reason   TEXT NOT NULL
        CHECK (split_reason IN ('LIVE_CONTENDED', 'WATCHDOG', 'MANUAL'))
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create db_chunk_boundary_events if it does not exist. Idempotent."""
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()


def emit_event(
    db_path: Path | str,
    *,
    caller_module: str,
    split_reason: str,
    rows_processed: int = 0,
    duration_ms: int = 0,
) -> None:
    """Emit a DB_CHUNK_BOUNDARY event row into db_path. Failure-silent.

    Opens its own sqlite3 connection so it is safe to call from a daemon
    thread (the BulkChunker watchdog) without interfering with the main
    thread's connection.

    split_reason must be one of: 'LIVE_CONTENDED', 'WATCHDOG', 'MANUAL'.
    """
    try:
        event_id = str(uuid.uuid4())
        occurred_at = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            ensure_table(conn)
            conn.execute(
                "INSERT INTO db_chunk_boundary_events "
                "(event_id, occurred_at, caller_module, db_path, rows_processed, duration_ms, split_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, occurred_at, caller_module, str(db_path),
                 rows_processed, duration_ms, split_reason),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # emit_event is observability-only; never crash the caller.
        logger.debug(
            "chunk_boundary_events.emit_event failed (caller=%s reason=%s): %s",
            caller_module, split_reason, exc,
        )
