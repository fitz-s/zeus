# Lifecycle: created=2026-09-20; last_reviewed=2026-09-20; last_reused=never
# Purpose: Install the world-DB covering index for the bounded EDLI belief
#   recency scan without invoking general init_schema during live boot.
# Authority: docs/operations/current/plans/hourly_capital_gains_improvement_loop.md
#   exact belief-recency index plan section.
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
_EXPECTED_COLUMNS = (
    ("recorded_at", 1, "BINARY"),
    ("trace_id", 1, "BINARY"),
    ("decision_id", 0, "BINARY"),
)
_CREATE_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {_INDEX} "
    f"ON {_TABLE}(recorded_at DESC, trace_id DESC, decision_id)"
)


def _database_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        return None
    return Path(str(row[2])).resolve()


def _assert_table_exists(conn: sqlite3.Connection) -> None:
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE,),
        ).fetchone()
        is None
    ):
        raise RuntimeError(
            f"{_TABLE} is missing; refusing to mark {_INDEX} migration applied"
        )


def _validate_existing_index(conn: sqlite3.Connection) -> bool:
    """Return whether the exact intended index already exists; reject drift."""
    row = conn.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
        (_INDEX,),
    ).fetchone()
    if row is None:
        return False
    if str(row[0]) != _TABLE:
        raise RuntimeError(
            f"{_INDEX} exists on {row[0]!r}, expected {_TABLE!r}; refusing to drop/rebuild"
        )

    index_list = conn.execute(f"PRAGMA index_list({_TABLE})").fetchall()
    metadata = next((entry for entry in index_list if str(entry[1]) == _INDEX), None)
    if metadata is None:
        raise RuntimeError(
            f"{_INDEX} is not registered on {_TABLE}; refusing to drop/rebuild"
        )
    # PRAGMA index_list columns: seq, name, unique, origin, partial.
    if int(metadata[2]) != 0 or int(metadata[4]) != 0:
        raise RuntimeError(
            f"{_INDEX} must be nonunique and nonpartial; refusing to drop/rebuild"
        )

    key_columns = [
        (str(entry[2]), int(entry[3]), str(entry[4] or "BINARY"))
        for entry in conn.execute(f"PRAGMA index_xinfo({_INDEX})").fetchall()
        if int(entry[5]) == 1
    ]
    if key_columns != list(_EXPECTED_COLUMNS):
        raise RuntimeError(
            f"{_INDEX} has unexpected key columns {key_columns!r}; "
            "refusing to drop/rebuild"
        )
    return True


def _install(conn: sqlite3.Connection) -> None:
    _assert_table_exists(conn)
    if not _validate_existing_index(conn):
        conn.execute(_CREATE_INDEX)
        if not _validate_existing_index(conn):
            raise RuntimeError(
                f"{_INDEX} was not materialized on {_TABLE} after CREATE INDEX"
            )
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
