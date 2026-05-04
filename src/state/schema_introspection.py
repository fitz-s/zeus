# Created: 2026-05-04
# Last reused/audited: 2026-05-04
# Authority basis: docs/operations/PROPOSALS_2026-05-04.md P2 — extracted
#                  from src/calibration/store.py::_v2_table_has_stratification
#                  during PR #55+#56 merge capsule.
"""Schema-introspection helpers for SQLite-backed state.

Centralises column-presence detection so loaders/writers can degrade
gracefully when staged migrations add optional columns.  Pre-2026-05-04
this logic was inlined per-module (most recently in
``src/calibration/store.py`` for the Phase 2 cycle/source/horizon
columns); the fragmentation made it hard to find and to keep
consistent across migration waves.

Use ``has_columns`` whenever a code path depends on a column that may
or may not exist (because the migration that adds it hasn't run on
every DB yet — most often a test fixture or a pre-migration archive).
"""

from __future__ import annotations

import sqlite3
from typing import Optional


def has_columns(
    conn: sqlite3.Connection,
    table: str,
    *cols: str,
    attached: Optional[str] = None,
) -> bool:
    """True iff ``table`` has all of ``cols``.

    Returns False on PRAGMA failure (table missing, attached DB
    unavailable, table name not whitelisted by the connection's pragmas)
    so callers can fall back to a legacy form rather than crash.

    Args:
        conn: SQLite connection.
        table: bare table name (no schema prefix); pass ``attached``
            instead of dot-prefixing.
        *cols: column names that must all be present.  An empty
            ``cols`` argument trivially returns True (no requirements).
        attached: optional attached-DB name (e.g., ``"world"`` for
            ``world.platt_models_v2``).  When provided, queries
            ``PRAGMA <attached>.table_info(<table>)``.

    Returns:
        True iff every name in ``cols`` appears in the table's column
        set; False if any column is missing OR if the PRAGMA query
        raises.
    """
    if not cols:
        return True
    try:
        if attached:
            rows = conn.execute(
                f"PRAGMA {attached}.table_info({table})"
            ).fetchall()
        else:
            rows = conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
    except sqlite3.Error:
        return False
    present = {row[1] for row in rows}
    return all(c in present for c in cols)
