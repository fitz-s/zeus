# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/WAVE_2_PLAN.md §#37 F11
#   "BulkChunker LIVE chunk boundary observability — db_chunk_boundary_events table"
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: Create db_chunk_boundary_events table in zeus-world.db (world_class).
#   Idempotent — safe to run against a DB that already has the table.
# DB target: zeus-world.db (WORLD_CLASS tables)
# Runner interface: def up(conn: sqlite3.Connection) -> None
"""Migration: create db_chunk_boundary_events table.

F11 (wave6 §#37): emits a queryable row each time BulkChunker yields to a
LIVE writer (LIVE_CONTENDED) or the watchdog fires (WATCHDOG). Replaces
counter-only observability with durable event rows.

Schema:
  db_chunk_boundary_events (
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
from __future__ import annotations

import sqlite3

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

_IDEMPOTENCY_MARKER = "db_chunk_boundary_events"


def _is_already_applied(conn: sqlite3.Connection) -> bool:
    """True if db_chunk_boundary_events already exists."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (_IDEMPOTENCY_MARKER,),
    ).fetchone()
    return row is not None


def up(conn: sqlite3.Connection) -> None:
    """Create db_chunk_boundary_events. Idempotent."""
    if _is_already_applied(conn):
        print("202605_db_chunk_boundary_events: already applied, skipping")
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(_CREATE_TABLE_SQL.strip().rstrip(";"))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    print("202605_db_chunk_boundary_events: applied — db_chunk_boundary_events created")
