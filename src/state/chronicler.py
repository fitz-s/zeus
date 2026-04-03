"""Append-only trade chronicle. Spec §6.4.

Records every trade event (entry, exit, settlement) for auditing.
All writes go to the chronicle table in zeus.db.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _canonical_position_surface_available(conn) -> bool:
    from src.state.ledger import CANONICAL_POSITION_EVENT_COLUMNS
    from src.state.projection import CANONICAL_POSITION_CURRENT_COLUMNS

    event_columns = _table_columns(conn, "position_events")
    current_columns = _table_columns(conn, "position_current")
    return (
        bool(event_columns)
        and bool(current_columns)
        and set(CANONICAL_POSITION_EVENT_COLUMNS).issubset(event_columns)
        and set(CANONICAL_POSITION_CURRENT_COLUMNS).issubset(current_columns)
    )


def _legacy_runtime_position_event_shape_present(conn) -> bool:
    from src.state.db import LEGACY_RUNTIME_POSITION_EVENT_COLUMNS

    event_columns = _table_columns(conn, "position_events")
    return bool(event_columns) and set(LEGACY_RUNTIME_POSITION_EVENT_COLUMNS).issubset(event_columns)


def log_event(
    conn,
    event_type: str,
    trade_id: str | None = None,
    details: dict | None = None,
    env: str = "",
) -> None:
    """Append an event to the chronicle. Never updates existing records."""
    from src.config import settings
    now = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details or {})
    env = env or settings.mode

    if not _table_exists(conn, "chronicle"):
        if _canonical_position_surface_available(conn) and not _legacy_runtime_position_event_shape_present(conn):
            return
        raise sqlite3.OperationalError("no such table: chronicle")

    conn.execute("""
        INSERT INTO chronicle (event_type, trade_id, timestamp, details_json, env)
        VALUES (?, ?, ?, ?, ?)
    """, (event_type, trade_id, now, details_json, env))
