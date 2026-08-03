# Lifecycle: created=2026-08-02; last_reviewed=2026-08-02; last_reused=never
# Purpose: Add the active-only, typed EDLI processing projection used to select
#   redecision expiry debt without scanning append-only opportunity history.
# Authority: EDLI control-plane query-starvation Hotfix B.
"""Additive world-DB migration for typed active EDLI processing rows.

The immutable ``opportunity_events`` log remains the event-type authority.
This migration adds only a mutable, active-only projection and a per-consumer
active-set seed receipt. It fails closed unless the complete active set fits the
explicit fence and joins back to immutable event rows. ``down`` removes those
additive objects without altering either canonical event or processing rows.
"""
from __future__ import annotations

import sqlite3

from src.state.schema.opportunity_event_processing_schema import (
    install_active_event_type_projection,
)

TARGET_DB = "world"

_ROLLBACK_SQL = (
    "DROP TRIGGER IF EXISTS trg_event_processing_type_projection_insert_guard",
    "DROP TRIGGER IF EXISTS trg_event_processing_type_projection_update_guard",
    "DROP TRIGGER IF EXISTS trg_event_processing_type_projection_insert",
    "DROP TRIGGER IF EXISTS trg_event_processing_type_projection_update",
    "DROP TABLE IF EXISTS opportunity_event_processing_type_projection",
    "DROP TABLE IF EXISTS opportunity_event_processing_type_backfill",
)


def up(conn: sqlite3.Connection) -> None:
    """Install and completely seed EDLI's active projection; safe to re-run."""
    install_active_event_type_projection(
        conn,
        consumer_name="edli_reactor_v1",
    )


def down(conn: sqlite3.Connection) -> None:
    """Remove only Hotfix B's additive projection objects."""
    for statement in _ROLLBACK_SQL:
        conn.execute(statement)
