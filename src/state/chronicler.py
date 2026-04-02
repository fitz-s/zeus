"""Append-only trade chronicle. Spec §6.4.

Records every trade event (entry, exit, settlement) for auditing.
All writes go to the chronicle table in zeus.db.
"""

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


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

    conn.execute("""
        INSERT INTO chronicle (event_type, trade_id, timestamp, details_json, env)
        VALUES (?, ?, ?, ?, ?)
    """, (event_type, trade_id, now, details_json, env))
