# Lifecycle: created=2026-09-20; last_reviewed=2026-09-20; last_reused=never
# Purpose: Install the world-DB covering index for the bounded EDLI belief
#   recency scan without invoking general init_schema during live boot.
# Authority: docs/operations/current/plans/hourly_capital_gains_improvement_loop.md
#   §2026-09-21T04:03Z exact belief-recency index plan.
# WRITER_LOCK: CREATE INDEX and ANALYZE run under db_writer_lock(BULK). The
#   migration is index/statistics-only and does not rewrite probability rows.
"""Install the EDLI belief recency covering index on a world DB.

The live reader in ``src/events/continuous_redecision.py`` must remain
unchanged.  Existing world DBs receive this narrow migration during a fenced
operator apply; fresh DBs receive the same index from ``src/state/db.py``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

TARGET_DB = "world"

_TABLE = "probability_trace_fact"
_INDEX = "idx_probability_trace_belief_recency_cover"
_CREATE_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {_INDEX} "
    f"ON {_TABLE}(recorded_at DESC, trace_id DESC, decision_id)"
)


def _database_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        return None
    return Path(str(row[2])).resolve()


def _table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE,),
        ).fetchone()
        is not None
    )


def _install(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn):
        conn.commit()
        return
    conn.execute(_CREATE_INDEX)
    # The decision_id prefix range is broad on the live table.  ANALYZE lets
    # SQLite choose this order-preserving covering index instead of the unique
    # decision_id autoindex plus a temporary sort.
    conn.execute(f"ANALYZE {_TABLE}")
    conn.commit()


def up(conn: sqlite3.Connection) -> None:
    """Install the index and refresh table statistics, safely re-runnable."""
    from src.state.db_writer_lock import WriteClass, db_writer_lock

    db_path = _database_path(conn)
    if db_path is None:
        # Unit fixtures may use :memory:, where there is no canonical lock
        # path.  The formal runner always supplies a file-backed world DB.
        _install(conn)
        return
    with db_writer_lock(db_path, WriteClass.BULK):
        _install(conn)
