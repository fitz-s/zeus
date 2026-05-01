# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §3.2
"""World-view accessor: observations.

Provides get_latest_observation(world_conn, city, target_date) -> ObservationView | None.

Uses world_conn opened by the caller — no ATTACH, no module-level singleton.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class ObservationView:
    """Read-only view of the latest observation for a (city, target_date) pair.

    Fields mirror the canonical observation_instants_v2 row shape.
    Callers must not write back through this object.
    """
    city: str
    target_date: str
    max_temp_c: Optional[float]
    min_temp_c: Optional[float]
    source: Optional[str]
    authority: Optional[str]
    data_version: Optional[str]
    utc_timestamp: Optional[str]


def get_latest_observation(
    world_conn: sqlite3.Connection,
    city: str,
    target_date: date | str,
) -> Optional[ObservationView]:
    """Return the latest observation for (city, target_date) from world DB.

    Queries observation_instants_v2 first (canonical), falls back to
    observation_instants (legacy) if v2 table is absent or empty.

    world_conn must already be open — caller manages lifecycle.
    Returns None if no observation exists for the given city + date.
    """
    target_date_str = str(target_date)

    # Try canonical v2 table first
    try:
        row = world_conn.execute(
            """
            SELECT city, target_date, max_temp_c, min_temp_c,
                   source, authority, data_version, utc_timestamp
              FROM observation_instants_v2
             WHERE city = ? AND target_date = ?
             ORDER BY utc_timestamp DESC
             LIMIT 1
            """,
            (city, target_date_str),
        ).fetchone()
        if row is not None:
            return ObservationView(
                city=row["city"] if hasattr(row, "__getitem__") and "city" in row.keys() else row[0],
                target_date=target_date_str,
                max_temp_c=_safe_col(row, "max_temp_c", 2),
                min_temp_c=_safe_col(row, "min_temp_c", 3),
                source=_safe_col(row, "source", 4),
                authority=_safe_col(row, "authority", 5),
                data_version=_safe_col(row, "data_version", 6),
                utc_timestamp=_safe_col(row, "utc_timestamp", 7),
            )
    except sqlite3.OperationalError:
        pass  # v2 table absent — fall through to legacy

    # Fallback: legacy observation_instants
    try:
        row = world_conn.execute(
            """
            SELECT city, target_date, max_temp_c, min_temp_c, source, utc_timestamp
              FROM observation_instants
             WHERE city = ? AND target_date = ?
             ORDER BY utc_timestamp DESC
             LIMIT 1
            """,
            (city, target_date_str),
        ).fetchone()
        if row is not None:
            return ObservationView(
                city=_safe_col(row, "city", 0),
                target_date=target_date_str,
                max_temp_c=_safe_col(row, "max_temp_c", 2),
                min_temp_c=_safe_col(row, "min_temp_c", 3),
                source=_safe_col(row, "source", 4),
                authority=None,
                data_version=None,
                utc_timestamp=_safe_col(row, "utc_timestamp", 5),
            )
    except sqlite3.OperationalError:
        pass

    return None


def _safe_col(row, name: str, index: int):
    """Safely get column by name, fall back to positional index."""
    try:
        return row[name]
    except (IndexError, KeyError):
        try:
            return row[index]
        except (IndexError, TypeError):
            return None
